# -*- coding: utf-8 -*-
"""Send one email to users in the background.

    python3 manage.py send_broadcast <id>

The Send and Resume buttons start this, it's not a cron job. Run by hand it resumes a
stopped send. Does nothing if the email is finished or another sender is working on it.
"""
from __future__ import unicode_literals

from django.core.management.base import BaseCommand, CommandError

from staff import broadcasts
from staff.models import Broadcast


class Command(BaseCommand):
    help = 'Send (or resume sending) one Admin Console email to users.'

    def add_arguments(self, parser):
        parser.add_argument('broadcast_id', type=int)

    def handle(self, *args, **options):
        if not Broadcast.objects.filter(pk=options['broadcast_id']).exists():
            raise CommandError('No email to users with id {0}.'.format(options['broadcast_id']))
        result = broadcasts.run(options['broadcast_id'])
        if result is None:
            self.stdout.write('Nothing to do: it is finished, still a draft, or another sender is working on it.')
            return
        self.stdout.write('Sent {0}, failed {1} in this run.'.format(*result))
