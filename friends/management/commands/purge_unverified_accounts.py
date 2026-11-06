"""Remove sign-ups that never confirmed their email address.

    python3 manage.py purge_unverified_accounts              # dry run, 7-day cutoff
    python3 manage.py purge_unverified_accounts --days 14    # dry run, custom cutoff
    python3 manage.py purge_unverified_accounts --delete     # actually delete

Only accounts that are inactive, never signed in, have no email_verified_at and own no bundles.
Their archive folder is removed only if empty.

Also deletes expired rate-limit counters, old remembered devices, account activity over a
year old, and device rows for expired sessions.

Cron (daily, 03:30), as a user that can write ARCHIVE_DIR:
    30 3 * * * cd /export/atmos1/htdocs/elsa && /usr/local/venvs/ELSA_3.11/bin/python manage.py purge_unverified_accounts --delete
"""
import os
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from build.models import Bundle
from django.conf import settings

from friends import devices, throttle
from friends.models import UserSession
from friends.views import archive_dir_for


class Command(BaseCommand):
    help = 'Delete accounts that never confirmed their email address.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7,
                            help='Age in days before an unconfirmed account is removed (default 7).')
        parser.add_argument('--delete', action='store_true',
                            help='Actually delete; without this flag it is a dry run.')

    def handle(self, *args, **options):
        if options['days'] < 1:
            raise CommandError('--days must be at least 1.')

        cutoff = timezone.now() - timedelta(days=options['days'])
        stale = (User.objects
                 .filter(is_active=False, last_login__isnull=True,
                         userprofile__email_verified_at__isnull=True,
                         userprofile__closed_at__isnull=True,
                         date_joined__lt=cutoff)
                 .exclude(pk__in=Bundle.objects.values('user'))
                 .order_by('date_joined'))

        doing = 'Deleting' if options['delete'] else 'Would delete'
        removed = 0
        for user in stale:
            self.stdout.write('{0} {1} (joined {2:%Y-%m-%d})'.format(
                doing, user.username, user.date_joined))
            if not options['delete']:
                continue
            folder = self._archive_folder(user.username)
            user.delete()
            removed += 1
            if folder:
                try:
                    os.rmdir(folder)   # Only succeeds on an empty folder.
                except OSError:
                    self.stdout.write('  kept non-empty folder {0}'.format(folder))

        if options['delete']:
            pruned = throttle.prune()
            expired_devices, old_events = devices.prune()
            stale_sessions = self._prune_sessions()
            self.stdout.write(self.style.SUCCESS(
                'Removed {0} unconfirmed account(s), {1} expired rate-limit counter(s), '
                '{2} expired remembered device(s), {3} year-old activity record(s) and '
                '{4} ended session(s).'.format(removed, pruned, expired_devices, old_events, stale_sessions)))
        else:
            self.stdout.write('Dry run: {0} account(s) would be removed. Pass --delete to act.'
                              .format(stale.count()))

    @staticmethod
    def _prune_sessions():
        cutoff = timezone.now() - timedelta(seconds=settings.SESSION_COOKIE_AGE)
        deleted, _ = UserSession.objects.filter(last_seen_at__lt=cutoff).delete()
        return deleted

    @staticmethod
    def _archive_folder(username):
        try:
            folder = archive_dir_for(username)
        except ValueError:
            return None
        return folder if os.path.isdir(folder) else None
