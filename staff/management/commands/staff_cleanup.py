"""Admin Console cleanup.

    python3 manage.py staff_cleanup

Deletes email log rows over 180 days old and admin session rows over 30 days old.
Cron, daily at 03:40:
    40 3 * * * cd /export/atmos1/htdocs/elsa && /usr/local/venvs/ELSA_3.11/bin/python manage.py staff_cleanup
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from staff.models import MailLog, StaffSession


class Command(BaseCommand):
    help = 'Prune old Admin Console email-log and session rows.'

    def handle(self, *args, **options):
        now = timezone.now()
        mail, _ = MailLog.objects.filter(created_at__lt=now - timedelta(days=180)).delete()
        sessions, _ = StaffSession.objects.filter(last_seen_at__lt=now - timedelta(days=30)).delete()
        self.stdout.write('Removed {0} email-log row(s) and {1} session row(s).'.format(mail, sessions))
