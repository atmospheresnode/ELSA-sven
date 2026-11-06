"""Admin Console security check, from the server.

    python3 manage.py staff_selfcheck

Same list as the Health page. Exits with status 1 if anything failed, so it can run after a deploy.
"""
import sys

from django.core.management.base import BaseCommand

from staff import selfcheck


class Command(BaseCommand):
    help = 'Run the Admin Console security check.'

    def handle(self, *args, **options):
        rows = selfcheck.run()
        marks = {selfcheck.OK: 'ok  ', selfcheck.WARN: 'WARN', selfcheck.FAIL: 'FAIL'}
        for row in rows:
            self.stdout.write('{0}  {1}: {2}'.format(marks[row['level']], row['name'], row['found']))
            if row['fix'] and row['level'] != selfcheck.OK:
                self.stdout.write('      -> ' + row['fix'])
        if selfcheck.worst(rows) == selfcheck.FAIL:
            sys.exit(1)
