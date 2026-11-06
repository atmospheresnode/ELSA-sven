# -*- coding: utf-8 -*-
"""Check the outside things ELSA depends on for new versions (staff/watchers.py).

    python3 manage.py staff_watch                 # every source
    python3 manage.py staff_watch --only django   # one (validate, pds4_im, ama_ldd, django, gemini)

A new version opens one "Outside update" request. Nothing is installed or changed.
Cron, daily at 06:30:

    30 6 * * * cd /export/atmos1/htdocs/elsa && /usr/local/venvs/ELSA_3.11/bin/python manage.py staff_watch
"""
from __future__ import unicode_literals

from django.core.management.base import BaseCommand, CommandError

from staff import watchers


class Command(BaseCommand):
    help = 'Check PDS validate, the PDS4 information model, the AMA dictionary, Django and the Assistant models.'

    def add_arguments(self, parser):
        parser.add_argument('--only', action='append', choices=list(watchers.SOURCES), default=None)

    def handle(self, *args, **options):
        outcome = watchers.run(options['only'])
        if not outcome:
            raise CommandError('Nothing was checked.')
        from staff.models import WatchedSource
        for row in WatchedSource.objects.filter(key__in=outcome):
            self.stdout.write('{0}: {1}{2}'.format(
                row.key, 'uses {0}, newest {1}'.format(row.current or '-', row.latest or '-')
                if outcome[row.key] == 'ok' else 'could not check ({0})'.format(row.error),
                '; ' + row.note if row.note else ''))
