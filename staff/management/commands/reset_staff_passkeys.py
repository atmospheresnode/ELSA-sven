"""Recovery for a staff member who lost their passkeys or had one stolen.

    python3 manage.py reset_staff_passkeys lneakras --reason "Laptop stolen 2026-10-14"

The console can't remove a staff member's passkeys, so this is the way back. It removes
the passkeys, signs the account out everywhere, forgets trusted devices, writes to the
audit log and emails the person and all console members.

They then sign in with password and email code and add a new passkey on the Account page.
Check who they are by phone or in person before running this.
"""
from django.contrib.auth.models import User
from django.core.mail import EmailMessage
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from friends import devices
from friends.models import Passkey
from staff import roles
from staff.actions import _end_all_sessions

from ._staffing import logger, notify, record, server_actor


class Command(BaseCommand):
    help = "Remove every passkey of an Admin Console member and sign them out everywhere (lost or stolen keys)."

    def add_arguments(self, parser):
        parser.add_argument('username')
        parser.add_argument('--reason', required=True,
                            help='Why, for the audit log (for example "Laptop stolen 2026-10-14").')

    def handle(self, *args, **options):
        reason = ' '.join((options['reason'] or '').split())
        if not reason:
            raise CommandError('Give a reason with --reason; it goes in the audit log.')
        try:
            user = User.objects.get(username=options['username'])
        except User.DoesNotExist:
            raise CommandError('No account named {0}.'.format(options['username']))
        if not (roles.is_member(user) or user.is_superuser):
            raise CommandError('{0} is not an Admin Console member. Ordinary accounts are helped from '
                               'the console (Users, then the account).'.format(user.username))

        names = list(Passkey.objects.filter(user=user).values_list('name', flat=True))
        with transaction.atomic():
            Passkey.objects.filter(user=user).delete()
            _end_all_sessions(user)
            devices.record(user, 'passkey_removed', None,
                           detail='All passkeys, by ELSA staff on the server'[:255])
            record('user.passkey_reset', user, {'passkeys': names}, reason)

        actor = server_actor()
        if user.email:
            try:
                EmailMessage(
                    subject='Your ELSA passkeys were reset',
                    body=('Hello {0},\n\nThe ELSA developers removed every passkey from your account and '
                          'signed it out everywhere ({1}).\n\nTo get back in: sign in with your password '
                          'and the code ELSA emails you, then add a new passkey on your Account page. '
                          'The Admin Console opens again once the new passkey is there.\n\nIf you did not ask '
                          'for this, tell the ELSA developers straight away.\n').format(
                              user.first_name or user.username, reason),
                    from_email='atm-elsa@nmsu.edu', to=[user.email]).send()
            except Exception:
                logger.exception('passkey reset notice to %s failed', user.username)
        notify('Admin Console: passkeys reset for {0}'.format(user.username),
               "{0} removed every passkey of {1} and signed the account out everywhere.\nReason: {2}\n"
               "If this is unexpected, tell the ELSA developers.".format(actor, user.username, reason))
        self.stdout.write(self.style.SUCCESS(
            'Removed {0} passkey{1} from {2} and signed the account out everywhere.'.format(
                len(names), '' if len(names) == 1 else 's', user.username)))
        self.stdout.write('Next: {0} signs in with password and email code, then adds a passkey '
                          'on the Account page.'.format(user.username))
