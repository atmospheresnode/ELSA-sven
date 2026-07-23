"""Usage and quality report for the assistant; closes the ratings loop.

    python3 manage.py assistant_stats             # last 7 days
    python3 manage.py assistant_stats --days 30

Prints volume, model mix, latency percentiles, error breakdown, ratings, and
the actual thumbs-down exchanges so they can be triaged into knowledge-chunk
updates or new eval cases.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Count
from django.utils import timezone

from assistant.models import Message


def _percentile(sorted_values, pct):
    if not sorted_values:
        return 0
    idx = min(len(sorted_values) - 1, int(len(sorted_values) * pct / 100))
    return sorted_values[idx]


class Command(BaseCommand):
    help = 'Report assistant usage, latency, errors, and rated conversations.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)

    def handle(self, *args, **options):
        since = timezone.now() - timedelta(days=options['days'])
        replies = Message.objects.filter(role='model', created_at__gte=since)
        users = (Message.objects.filter(role='user', created_at__gte=since)
                 .values('conversation__user').distinct().count())

        self.stdout.write(f'=== Assistant stats, last {options["days"]} day(s) ===')
        self.stdout.write(f'Replies: {replies.count()}  |  Active users: {users}')

        self.stdout.write('\nModel mix:')
        for row in (replies.exclude(model_used='').values('model_used')
                    .annotate(n=Count('id')).order_by('-n')):
            self.stdout.write(f"  {row['model_used']}: {row['n']}")

        latencies = sorted(replies.exclude(latency_ms=None)
                           .values_list('latency_ms', flat=True))
        if latencies:
            self.stdout.write(
                f'\nLatency ms: p50={_percentile(latencies, 50)} '
                f'p90={_percentile(latencies, 90)} p99={_percentile(latencies, 99)} '
                f'max={latencies[-1]}')

        errors = (replies.exclude(error='').values('error')
                  .annotate(n=Count('id')).order_by('-n'))
        self.stdout.write('\nErrors:' if errors else '\nErrors: none')
        for row in errors:
            self.stdout.write(f"  {row['error']}: {row['n']}")

        ups = replies.filter(rating=1).count()
        downs = replies.filter(rating=-1).count()
        self.stdout.write(f'\nRatings: {ups} up / {downs} down '
                          f'({replies.filter(rating=0).count()} unrated)')

        down_msgs = replies.filter(rating=-1).select_related('conversation')[:20]
        if down_msgs:
            self.stdout.write('\n=== Thumbs-down exchanges (triage these) ===')
        for reply in down_msgs:
            question = (reply.conversation.messages
                        .filter(role='user', created_at__lt=reply.created_at)
                        .order_by('-created_at').first())
            self.stdout.write(f'\n[conv {reply.conversation_id}, '
                              f'{reply.conversation.user.username}, '
                              f'{reply.created_at:%Y-%m-%d %H:%M}]')
            if question:
                self.stdout.write(f'  Q: {question.text[:200]}')
            self.stdout.write(f'  A: {reply.text[:300]}')
