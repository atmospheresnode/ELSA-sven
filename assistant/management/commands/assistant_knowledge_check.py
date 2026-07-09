"""Detect stale assistant knowledge.

Each file in assistant/knowledge/ can declare which source files it describes:

    <!-- watches: build/views.py, templates/build/citation_information -->

This command compares git history: if a watched path has commits newer than the
knowledge file's last commit (or has uncommitted changes), the chunk probably
needs review. It cannot say WHAT changed, only that the described code moved
while the description stood still.

Run before releases (or in CI):
    python3 manage.py assistant_knowledge_check
Exits non-zero when stale chunks are found.
"""
import re
import subprocess
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

KNOWLEDGE_DIR = Path(__file__).resolve().parents[2] / 'knowledge'
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


class Command(BaseCommand):
    help = 'Warn when source files described by assistant knowledge changed after the knowledge did.'

    def handle(self, *args, **options):
        dirty = _dirty_paths()
        stale = []
        unwatched = []

        for chunk_path in sorted(KNOWLEDGE_DIR.glob('*.md')):
            rel_chunk = chunk_path.relative_to(REPO_ROOT)
            watched = parse_watches(chunk_path.read_text())
            if not watched:
                unwatched.append(rel_chunk)
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
                stale.append((rel_chunk, reasons))

        for rel_chunk in unwatched:
            self.stdout.write(self.style.WARNING(
                f'NO WATCHES  {rel_chunk} declares no watched paths; add "<!-- watches: ... -->"'))

        if not stale:
            self.stdout.write(self.style.SUCCESS(
                f'OK: no stale knowledge ({len(list(KNOWLEDGE_DIR.glob("*.md")))} chunks checked).'))
            return

        for rel_chunk, reasons in stale:
            self.stdout.write(self.style.ERROR(f'STALE  {rel_chunk}'))
            for reason in reasons:
                self.stdout.write(f'       - {reason}')
        self.stdout.write('\nReview the chunks above, update them if the behavior they '
                          'describe changed, then commit (a no-change commit also clears the flag).')
        raise CommandError(f'{len(stale)} knowledge chunk(s) may be stale.')
