"""Give an account an Admin Console role.

    python3 manage.py grant_staff lneakras --role curator
    python3 manage.py grant_staff lneakras --role account_admin --reason "Node manager"

Roles are viewer, curator and account_admin (see staff/roles.py). Granting a new one
replaces the old one. This is the only way to make someone staff. No page can do it.
"""
from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand, CommandError

from staff import roles

from ._staffing import notify, record, server_actor


class Command(BaseCommand):
    help = 'Give an account a role in the Admin Console.'

    def add_arguments(self, parser):
        parser.add_argument('username')
        parser.add_argument('--role', required=True, choices=list(roles.ROLES))
        parser.add_argument('--reason', default='')

    def handle(self, *args, **options):
        try:
            user = User.objects.get(username=options['username'])
        except User.DoesNotExist:
            raise CommandError('No account named {0}.'.format(options['username']))
        if not user.is_active:
            raise CommandError('{0} is not active; enable the account first.'.format(user.username))

        roles.sync_groups()
        role = roles.ROLES[options['role']]
        try:
            group = Group.objects.get(name=role['group'])
        except Group.DoesNotExist:
            raise CommandError('Role groups are missing; run `manage.py migrate staff` first.')

        before = roles.roles_for(user)
        user.groups.remove(*Group.objects.filter(name__in=roles.GROUP_NAMES))
        user.groups.add(group)
        record('role.grant', user, {'roles': [before, [options['role']]]}, options['reason'])

        notify('Admin Console: {0} is now {1}'.format(user.username, role['label']),
               '{0} ({1}) was given the {2} role in the Admin Console by {3}.\n\n{4}\n\n'
               'If you did not expect this, tell the ELSA developers.'.format(
                   user.username, user.get_full_name() or 'no name', role['label'],
                   server_actor(), role['summary']))

        self.stdout.write(self.style.SUCCESS('{0} is now {1}.'.format(user.username, role['label'])))
        if not user.passkeys.exists():
            self.stdout.write(self.style.WARNING(
                '{0} has no passkey yet. The Admin Console only opens with a passkey; they can add '
                'one on their Account page.'.format(user.username)))
