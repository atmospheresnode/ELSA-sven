"""List who can open the Admin Console.

    python3 manage.py list_staff
"""
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db.models import Q

from staff import roles


class Command(BaseCommand):
    help = 'List the accounts that can open the Admin Console.'

    def handle(self, *args, **options):
        members = (User.objects.filter(Q(is_superuser=True) | Q(groups__name__in=roles.GROUP_NAMES))
                   .distinct().order_by('username'))
        if not members:
            self.stdout.write('Nobody can open the Admin Console.')
            return
        for user in members:
            self.stdout.write('{0:<20} {1:<15} {2:<8} passkeys: {3}'.format(
                user.username, roles.role_label(user),
                'active' if user.is_active else 'DISABLED', user.passkeys.count()))
