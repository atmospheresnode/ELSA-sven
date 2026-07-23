"""Data retention for assistant conversations.

    python3 manage.py assistant_purge              # dry run, 90-day cutoff
    python3 manage.py assistant_purge --days 60    # dry run, custom cutoff
    python3 manage.py assistant_purge --delete     # actually delete

Deletes conversations (and their messages, via cascade) that have not been
updated within the retention window. Run periodically (cron) once the team
settles the privacy notice.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from assistant.models import Conversation


class Command(BaseCommand):
    help = 'Delete assistant conversations idle longer than the retention window.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=90,
                            help='Retention window in days (default 90).')
        parser.add_argument('--delete', action='store_true',
                            help='Actually delete; without this flag it is a dry run.')

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=options['days'])
        qs = Conversation.objects.filter(updated_at__lt=cutoff)
        count = qs.count()
        if not options['delete']:
            self.stdout.write(f'{count} conversation(s) idle since before '
                              f'{cutoff:%Y-%m-%d} would be deleted. '
                              'Re-run with --delete to remove them.')
            return
        deleted, per_model = qs.delete()
        self.stdout.write(self.style.SUCCESS(
            f'Deleted {per_model.get("assistant.Conversation", 0)} conversation(s) '
            f'and {per_model.get("assistant.Message", 0)} message(s) '
            f'idle since before {cutoff:%Y-%m-%d}.'))
