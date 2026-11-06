"""Remove an account's Admin Console role.

    python3 manage.py revoke_staff lneakras --reason "Left the node"

Takes effect on the next request, so an open console session stops working right away.
"""
from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand, CommandError

from staff import roles

from ._staffing import notify, record, server_actor


class Command(BaseCommand):
    help = "Remove an account's role in the Admin Console."

    def add_arguments(self, parser):
        parser.add_argument('username')
        parser.add_argument('--reason', default='')

    def handle(self, *args, **options):
        try:
            user = User.objects.get(username=options['username'])
        except User.DoesNotExist:
            raise CommandError('No account named {0}.'.format(options['username']))

        before = [key for key in roles.roles_for(user) if key != 'superuser']
        if not before:
            raise CommandError('{0} has no Admin Console role.'.format(user.username))

        user.groups.remove(*Group.objects.filter(name__in=roles.GROUP_NAMES))
        record('role.revoke', user, {'roles': [before, []]}, options['reason'])
        notify('Admin Console: {0} no longer has a role'.format(user.username),
               "{0}'s Admin Console role was removed by {1}.".format(user.username, server_actor()))
        self.stdout.write(self.style.SUCCESS('{0} no longer has an Admin Console role.'.format(user.username)))
        if user.is_superuser:
            self.stdout.write(self.style.WARNING(
                '{0} is still a Django superuser and keeps full access through that.'.format(user.username)))
