#!/usr/bin/env python3
"""Detect stale assistant knowledge. Standalone: needs only Python 3 and git,
so CI can run it without Django or ELSA's (gitignored) settings.

Each file in assistant/knowledge/ declares which source files it describes:

    <!-- watches: build/views.py, templates/build/citation_information -->

If a watched path has commits newer than the knowledge file's last commit (or
has uncommitted changes locally), the chunk probably needs review.

Usage:
    python3 assistant/knowledge_check.py        # exits 1 when stale
Or via Django:
    python3 manage.py assistant_knowledge_check
"""
import re
import subprocess
import sys
from pathlib import Path

KNOWLEDGE_DIR = Path(__file__).resolve().parent / 'knowledge'
REPO_ROOT = KNOWLEDGE_DIR.parents[1]

WATCHES_RE = re.compile(r'<!--\s*watches:\s*(.*?)\s*-->', re.DOTALL)


def parse_watches(text):
    """Return the list of watched paths declared in a knowledge file."""
    match = WATCHES_RE.search(text)
    if not match:
        return []
    return [p.strip() for p in match.group(1).split(',') if p.strip()]


def _last_commit_ts(path):
    """Unix timestamp of the last commit touching path, or None if never committed."""
    out = subprocess.run(
        ['git', 'log', '-1', '--format=%ct', '--', str(path)],
        capture_output=True, text=True, cwd=REPO_ROOT,
    ).stdout.strip()
    return int(out) if out else None


def _dirty_paths():
    """Set of repo-relative paths with uncommitted changes."""
    out = subprocess.run(
        ['git', 'status', '--porcelain'],
        capture_output=True, text=True, cwd=REPO_ROOT,
    ).stdout
    return {line[3:].strip() for line in out.splitlines() if line.strip()}


def run_check(out=print):
    """Run the staleness check. Returns (stale, unwatched) lists."""
    dirty = _dirty_paths()
    stale = []
    unwatched = []

    for chunk_path in sorted(KNOWLEDGE_DIR.glob('*.md')):
        rel_chunk = chunk_path.relative_to(REPO_ROOT)
        watched = parse_watches(chunk_path.read_text())
        if not watched:
            unwatched.append(str(rel_chunk))
            continue

        chunk_ts = _last_commit_ts(rel_chunk)
        reasons = []
        for path in watched:
            if any(d.startswith(path.rstrip('/')) for d in dirty):
                reasons.append(f'{path} has uncommitted changes')
                continue
            watched_ts = _last_commit_ts(path)
            if chunk_ts and watched_ts and watched_ts > chunk_ts:
                reasons.append(f'{path} committed after the knowledge file')

        if reasons:
            stale.append((str(rel_chunk), reasons))

    for rel_chunk in unwatched:
        out(f'NO WATCHES  {rel_chunk} declares no watched paths; add "<!-- watches: ... -->"')

    if stale:
        for rel_chunk, reasons in stale:
            out(f'STALE  {rel_chunk}')
            for reason in reasons:
                out(f'       - {reason}')
        out('')
        out('Review the chunks above, update them if the behavior they describe changed,')
        out('then commit (a no-change commit also clears the flag).')
    else:
        out(f'OK: no stale knowledge ({len(list(KNOWLEDGE_DIR.glob("*.md")))} chunks checked).')

    return stale, unwatched


if __name__ == '__main__':
    stale_chunks, _ = run_check()
    sys.exit(1 if stale_chunks else 0)
