"""Data retention for assistant conversations.

    python3 manage.py assistant_purge              # dry run, 90-day cutoff
    python3 manage.py assistant_purge --days 60    # dry run, custom cutoff
    python3 manage.py assistant_purge --delete     # actually delete

Deletes conversations (and their messages, via cascade) that have not been
updated within the retention window. Run periodically (cron) once the team
settles the privacy notice.

A conversation with a rated reply is kept longer (--rated-days, default 365):
the ratings are the evidence for fixing the assistant, and a weekly review can
fall behind. They are still deleted in the end.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from assistant.models import Conversation, Message


class Command(BaseCommand):
    help = 'Delete assistant conversations idle longer than the retention window.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=90,
                            help='Retention window in days (default 90).')
        parser.add_argument('--rated-days', type=int, default=365,
                            help='Retention window for conversations with a rated reply (default 365).')
        parser.add_argument('--delete', action='store_true',
                            help='Actually delete; without this flag it is a dry run.')

    def handle(self, *args, **options):
        if options['rated_days'] < options['days']:
            raise CommandError('--rated-days cannot be shorter than --days.')
        now = timezone.now()
        cutoff = now - timedelta(days=options['days'])
        rated_cutoff = now - timedelta(days=options['rated_days'])
        rated = Exists(Message.objects.filter(conversation=OuterRef('pk')).exclude(rating=0))
        idle = Conversation.objects.filter(updated_at__lt=cutoff)
        qs = idle.filter(~rated | Q(updated_at__lt=rated_cutoff))
        count = qs.count()
        kept = idle.count() - count
        kept_note = (f' {kept} idle conversation(s) with ratings are kept until '
                     f'{options["rated_days"]} days.' if kept else '')
        if not options['delete']:
            self.stdout.write(f'{count} conversation(s) idle since before '
                              f'{cutoff:%Y-%m-%d} would be deleted.{kept_note} '
                              'Re-run with --delete to remove them.')
            return
        deleted, per_model = qs.delete()
        self.stdout.write(self.style.SUCCESS(
            f'Deleted {per_model.get("assistant.Conversation", 0)} conversation(s) '
            f'and {per_model.get("assistant.Message", 0)} message(s) '
            f'idle since before {cutoff:%Y-%m-%d}.{kept_note}'))
