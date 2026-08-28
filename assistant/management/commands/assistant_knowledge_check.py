"""Django wrapper around assistant/knowledge_check.py (see that file for docs).

    python3 manage.py assistant_knowledge_check

CI runs the standalone script directly (no Django settings needed):
    python3 assistant/knowledge_check.py
"""
from django.core.management.base import BaseCommand, CommandError

from assistant.knowledge_check import parse_watches, run_check  # noqa: F401 (parse_watches re-exported for tests)


class Command(BaseCommand):
    help = 'Warn when source files described by assistant knowledge changed after the knowledge did.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--update', action='store_true',
            help='Re-baseline the fingerprints after reviewing the chunks.')

    def handle(self, *args, **options):
        stale, _unwatched, broken = run_check(
            out=self.stdout.write, update=options['update'])
        if broken:
            raise CommandError(f'{len(broken)} knowledge chunk(s) watch a path that no longer resolves.')
        if stale:
            raise CommandError(f'{len(stale)} knowledge chunk(s) may be stale.')
