"""Save one emailed reply from standard input.

    python3 manage.py ingest_reply < message.eml

For a mail server that pipes messages to a program. Same parsing as fetch_replies.
"""
import sys

from django.core.management.base import BaseCommand

from staff import inbound


class Command(BaseCommand):
    help = 'Store one emailed reply read from standard input.'

    def handle(self, *args, **options):
        raw = sys.stdin.buffer.read()
        outcome, case = inbound.ingest(raw)
        self.stdout.write('{0}{1}'.format(outcome, ' (case #{0})'.format(case.pk) if case else ''))
