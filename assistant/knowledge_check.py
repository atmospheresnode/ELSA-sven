#!/usr/bin/env python3
"""Detect stale assistant knowledge. Standalone: needs only Python 3, so CI can
run it without Django or ELSA's (gitignored) settings.

Each file in assistant/knowledge/ declares the source it describes, and records
a fingerprint of that source as of the last time a human reviewed the chunk:

    <!-- watches: build/models.py#Product_Collection, templates/build/alias -->
    <!-- fingerprint:
         build/models.py#Product_Collection = 8f3a1c2d4e5f
         templates/build/alias = b2d40e7a1c93
    -->

A chunk is stale when a watched region's content no longer hashes to the
recorded fingerprint. Content, not commit time: an earlier version of this
script compared commit timestamps, which meant every merge and every unrelated
commit to a 5000-line file re-flagged every chunk watching it. The check was
red continuously and the only remedy was a commit that changed nothing but a
date, so it stopped carrying information.

A watch is `path` or `path#anchor`:

    build/views.py                    the whole file
    build/models.py#Product_Collection  just that class or function
    build/views.py#context_search*    that def and every one whose name extends it
    templates/base-derk.html#nav      the region between knowledge:nav marker comments
    templates/build/alias             every file in that directory, recursively

In a Python file an anchor names a top-level or nested class or def, and the
region runs to the end of that block; a trailing `*` covers every definition
whose name starts with it. In any other file an anchor names a region marked
with `knowledge:<anchor>` ... `/knowledge:<anchor>` comments.

Narrow watches are the point: `build/models.py` alone is 5000+ lines covering
every model in ELSA, so watching it whole means unrelated work marks the chunk
stale, which is how this check previously became noise.

Usage:
    python3 assistant/knowledge_check.py            # exits 1 when stale, showing
                                                   # the diff behind each flag
    python3 assistant/knowledge_check.py --quiet   # flags only, no diffs
    python3 assistant/knowledge_check.py --update  # re-baseline after review
Or via Django:
    python3 manage.py assistant_knowledge_check
"""
import difflib
import hashlib
import re
import subprocess
import sys
from pathlib import Path

KNOWLEDGE_DIR = Path(__file__).resolve().parent / 'knowledge'
REPO_ROOT = KNOWLEDGE_DIR.parents[1]

WATCHES_RE = re.compile(r'<!--\s*watches:\s*(.*?)\s*-->', re.DOTALL)
FINGERPRINT_RE = re.compile(r'<!--\s*fingerprint:\s*(.*?)\s*-->', re.DOTALL)
REVIEWED_RE = re.compile(r'<!--\s*reviewed:\s*(\d{4}-\d{2}-\d{2})\s*-->')
BASELINE_RE = re.compile(r'<!--\s*baseline:\s*([0-9a-f]{7,40})\s*-->')

# Never contribute to a directory's fingerprint.
IGNORED_NAMES = {'__pycache__', '.DS_Store'}
IGNORED_SUFFIXES = {'.pyc', '.pyo'}

HASH_LENGTH = 12


class WatchError(Exception):
    """A watch that cannot be resolved: a missing path, or an anchor that is not there."""


def parse_watches(text):
    """Return the list of watch specs declared in a knowledge file."""
    match = WATCHES_RE.search(text)
    if not match:
        return []
    return [spec.strip() for spec in match.group(1).split(',') if spec.strip()]


def parse_fingerprints(text):
    """Return {watch spec: recorded hash} from a knowledge file."""
    match = FINGERPRINT_RE.search(text)
    if not match:
        return {}
    recorded = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or '=' not in line:
            continue
        spec, value = line.split('=', 1)
        recorded[spec.strip()] = value.strip()
    return recorded


def parse_reviewed(text):
    """The date a human last recorded reviewing this chunk, or None.

    Informational only. It used to decide staleness, which is what made the
    check trip on every merge; staleness now comes from content alone.
    """
    match = REVIEWED_RE.search(text)
    return match.group(1) if match else None


def split_spec(spec):
    """'build/models.py#Bundle' -> ('build/models.py', 'Bundle')."""
    if '#' in spec:
        path, anchor = spec.split('#', 1)
        return path.strip(), anchor.strip()
    return spec.strip(), None


def normalize(text):
    """Content reduced to what a reader would call a change.

    Trailing whitespace and blank lines are dropped so that reindentation and
    spacing edits do not present as behavior changes. Everything else, comments
    included, counts: a comment can carry the behavior a chunk documents.
    """
    lines = [line.rstrip() for line in text.replace('\r\n', '\n').split('\n')]
    return '\n'.join(line for line in lines if line.strip())


def extract_anchor(source, anchor, spec):
    """The text of every `class anchor` or `def anchor` block in a Python source file.

    Every one, not the first: build/models.py and build/views.py both define some
    names twice (an older definition left above the live one). Hashing only the
    first would leave the definition that actually runs unwatched, so all blocks
    with the name are concatenated in file order.
    """
    if anchor.endswith('*'):
        # A prefix anchor covers a family of related definitions, e.g. context_search*
        # matches context_search and every context_search_<thing> beside it.
        name = re.escape(anchor[:-1]) + r'\w*'
    else:
        name = re.escape(anchor) + r'\b'

    pattern = re.compile(
        r'^(?P<indent>[ \t]*)(?:async\s+)?(?:class|def)\s+{}'.format(name),
        re.MULTILINE)

    blocks = []
    for match in pattern.finditer(source):
        lines = source[match.start():].split('\n')
        indent = len(match.group('indent').expandtabs())

        body = [lines[0]]
        for line in lines[1:]:
            if not line.strip():
                body.append(line)
                continue
            # The block ends at the first non-blank line indented no further than its header.
            if len(line) - len(line.lstrip()) <= indent:
                break
            body.append(line)
        blocks.append('\n'.join(body))

    if not blocks:
        raise WatchError('{}: no class or def named {!r}'.format(spec, anchor))
    return '\n'.join(blocks)


def extract_marked_region(source, anchor, spec):
    """The text between `knowledge:<anchor>` and `/knowledge:<anchor>` marker comments.

    For files with no class or def to point at, chiefly templates. The markers go in
    whatever comment syntax the file already uses, so a Django template uses
    {# knowledge:nav #} ... {# /knowledge:nav #} and nothing reaches the browser.

    This is how a chunk avoids watching a 4000-line template whole. The motivating case:
    "Navigating ELSA" describes the nav bar, but watching all of base-derk.html meant the
    version string in the footer marked the chunk stale on every release.
    """
    start = re.search(r'knowledge:{}\b'.format(re.escape(anchor)), source)
    if not start:
        raise WatchError('{}: no "knowledge:{}" marker in the file'.format(spec, anchor))
    end = re.search(r'/knowledge:{}\b'.format(re.escape(anchor)), source[start.end():])
    if not end:
        raise WatchError('{}: "knowledge:{}" is never closed with "/knowledge:{}"'.format(
            spec, anchor, anchor))
    return source[start.end():start.end() + end.start()]


def files_in(directory):
    """Every file under a watched directory, sorted, ignoring build artifacts."""
    found = []
    for path in sorted(directory.rglob('*')):
        if not path.is_file():
            continue
        if any(part in IGNORED_NAMES for part in path.parts):
            continue
        if path.suffix in IGNORED_SUFFIXES:
            continue
        found.append(path)
    return found


def fingerprint(spec):
    """The hash of the content a watch spec resolves to.

    Raises WatchError when the spec resolves to nothing, so a watch that has
    quietly stopped protecting anything (a renamed file, a deleted directory)
    is reported rather than passing.
    """
    relative, anchor = split_spec(spec)
    target = REPO_ROOT / relative

    if not target.exists():
        raise WatchError('{}: no such file or directory'.format(spec))

    digest = hashlib.sha256()

    if target.is_dir():
        if anchor:
            raise WatchError('{}: anchors are not supported on directories'.format(spec))
        contents = files_in(target)
        if not contents:
            raise WatchError('{}: directory is empty'.format(spec))
        for path in contents:
            # The name goes in too, so that renaming or removing a file registers.
            digest.update(str(path.relative_to(REPO_ROOT)).encode('utf-8'))
            digest.update(b'\0')
            digest.update(normalize(path.read_text(encoding='utf-8', errors='replace')).encode('utf-8'))
            digest.update(b'\0')
        return digest.hexdigest()[:HASH_LENGTH]

    text = target.read_text(encoding='utf-8', errors='replace')
    if anchor:
        if target.suffix == '.py':
            text = extract_anchor(text, anchor, spec)
        else:
            text = extract_marked_region(text, anchor, spec)

    digest.update(normalize(text).encode('utf-8'))
    return digest.hexdigest()[:HASH_LENGTH]


def parse_baseline(text):
    """The commit a chunk was last baselined against, or None."""
    match = BASELINE_RE.search(text)
    return match.group(1) if match else None


def git(*args):
    """Run a git command, or return None when git or the object is unavailable.

    Only the diff display needs git. The check itself is pure content hashing, so a
    shallow clone or a missing baseline degrades to "content changed" rather than failing.
    """
    try:
        result = subprocess.run(['git'] + list(args), capture_output=True, text=True,
                                cwd=REPO_ROOT)
    except OSError:
        return None
    return result.stdout if result.returncode == 0 else None


def head_commit():
    out = git('rev-parse', 'HEAD')
    return out.strip() if out else None


def region_at(commit, spec):
    """The watched region's text as of `commit`, or None if it cannot be recovered."""
    relative, anchor = split_spec(spec)
    text = git('show', '{}:{}'.format(commit, relative))
    if text is None:
        return None
    if not anchor:
        return text
    try:
        if relative.endswith('.py'):
            return extract_anchor(text, anchor, spec)
        return extract_marked_region(text, anchor, spec)
    except WatchError:
        return None


def region_now(spec):
    """The watched region's text as it stands, or None for a directory watch."""
    relative, anchor = split_spec(spec)
    target = REPO_ROOT / relative
    if target.is_dir():
        return None
    text = target.read_text(encoding='utf-8', errors='replace')
    if not anchor:
        return text
    try:
        if target.suffix == '.py':
            return extract_anchor(text, anchor, spec)
        return extract_marked_region(text, anchor, spec)
    except WatchError:
        return None


def region_diff(baseline, spec, context=2):
    """A unified diff of the watched region since the chunk was baselined.

    This is what turns a flagged chunk from a hash comparison into a decision you can
    make in seconds: you see the change the chunk may need to describe.
    """
    if not baseline:
        return []
    before = region_at(baseline, spec)
    after = region_now(spec)
    if before is None or after is None:
        return []
    lines = list(difflib.unified_diff(
        normalize(before).splitlines(), normalize(after).splitlines(),
        fromfile='{}@{}'.format(spec, baseline[:8]), tofile='{}@now'.format(spec),
        lineterm='', n=context))
    return lines[2:] if len(lines) > 2 else []


def render_fingerprints(specs_to_hashes):
    """The fingerprint comment block a chunk carries."""
    if not specs_to_hashes:
        return '<!-- fingerprint: -->'
    width = max(len(spec) for spec in specs_to_hashes)
    lines = ['<!-- fingerprint:']
    for spec, value in specs_to_hashes.items():
        lines.append('     {} = {}'.format(spec.ljust(width), value))
    lines.append('-->')
    return '\n'.join(lines)


def write_fingerprints(chunk_path, text, specs_to_hashes, reviewed_date):
    """Replace (or insert) a chunk's fingerprint block and reviewed date."""
    block = render_fingerprints(specs_to_hashes)

    if FINGERPRINT_RE.search(text):
        text = FINGERPRINT_RE.sub(lambda _m: block, text, count=1)
    else:
        # Sits directly under the watches line it belongs to.
        match = WATCHES_RE.search(text)
        insert_at = match.end() if match else 0
        text = text[:insert_at] + '\n' + block + text[insert_at:]

    marker = '<!-- reviewed: {} -->'.format(reviewed_date)
    if REVIEWED_RE.search(text):
        text = REVIEWED_RE.sub(lambda _m: marker, text, count=1)
    else:
        match = FINGERPRINT_RE.search(text)
        insert_at = match.end() if match else 0
        text = text[:insert_at] + '\n' + marker + text[insert_at:]

    # The commit the fingerprints describe, so a later failure can show the diff since.
    commit = head_commit()
    if commit:
        baseline = '<!-- baseline: {} -->'.format(commit)
        if BASELINE_RE.search(text):
            text = BASELINE_RE.sub(lambda _m: baseline, text, count=1)
        else:
            match = REVIEWED_RE.search(text)
            insert_at = match.end() if match else 0
            text = text[:insert_at] + '\n' + baseline + text[insert_at:]

    chunk_path.write_text(text, encoding='utf-8')


def run_check(out=print, update=False, today=None, show_diff=True):
    """Compare each chunk's recorded fingerprints against the source as it stands.

    Returns (stale, unwatched, broken):
      stale     [(chunk, [(reason, diff_lines)])] content changed since review
      unwatched [chunk]              declares no watches at all
      broken    [(chunk, [reasons])] a watch that resolves to nothing
    """
    import datetime
    today = today or datetime.date.today().isoformat()

    stale = []
    unwatched = []
    broken = []
    updated = []

    for chunk_path in sorted(KNOWLEDGE_DIR.glob('*.md')):
        rel_chunk = str(chunk_path.relative_to(REPO_ROOT))
        text = chunk_path.read_text(encoding='utf-8')

        specs = parse_watches(text)
        if not specs:
            unwatched.append(rel_chunk)
            continue

        recorded = parse_fingerprints(text)
        baseline = parse_baseline(text)
        current = {}
        problems = []
        drifted = []

        for spec in specs:
            try:
                current[spec] = fingerprint(spec)
            except WatchError as error:
                problems.append(str(error))
                continue

            if spec not in recorded:
                drifted.append(('{}: no fingerprint recorded yet'.format(spec), []))
            elif recorded[spec] != current[spec]:
                drifted.append((
                    '{}: content changed since this chunk was reviewed'.format(spec),
                    [] if show_diff is False else region_diff(baseline, spec)))

        if problems:
            broken.append((rel_chunk, problems))

        # --update also fills in a chunk that is in sync but predates the baseline marker,
        # so recording the diff anchor does not require an artificial edit first.
        if update and not problems and (drifted or not baseline):
            write_fingerprints(chunk_path, text, current, today)
            updated.append(rel_chunk)
        elif drifted:
            stale.append((rel_chunk, drifted))

    for rel_chunk in unwatched:
        out('NO WATCHES  {} declares none; add "<!-- watches: ... -->"'.format(rel_chunk))

    for rel_chunk, reasons in broken:
        out('BROKEN WATCH  {}'.format(rel_chunk))
        for reason in reasons:
            out('              - {}'.format(reason))

    for rel_chunk in updated:
        out('UPDATED  {}'.format(rel_chunk))

    if stale:
        for rel_chunk, reasons in stale:
            out('STALE  {}'.format(rel_chunk))
            for reason, diff in reasons:
                out('       - {}'.format(reason))
                for line in diff:
                    out('         | {}'.format(line))
        out('')
        out('The source these chunks describe changed. Read the diff, update the chunk')
        out('if the behavior it documents changed, then re-baseline with:')
        out('    python3 assistant/knowledge_check.py --update')
    elif not broken and not updated:
        count = len(list(KNOWLEDGE_DIR.glob('*.md')))
        out('OK: no stale knowledge ({} chunks checked).'.format(count))

    return stale, unwatched, broken


if __name__ == '__main__':
    arguments = sys.argv[1:]
    stale_chunks, _unwatched, broken_watches = run_check(
        update='--update' in arguments, show_diff='--quiet' not in arguments)
    sys.exit(1 if (stale_chunks or broken_watches) else 0)
