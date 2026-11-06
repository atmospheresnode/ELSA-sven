"""Compute the slow Admin Console lists ahead of time.

    python3 manage.py staff_precompute

Stalled bundles has to check every bundle folder and Validation insights every latest run.
Cron, nightly at 03:00:
    0 3 * * * cd /export/atmos1/htdocs/elsa && /usr/local/venvs/ELSA_3.11/bin/python manage.py staff_precompute
"""
from django.core.management.base import BaseCommand

from staff import snapshots


class Command(BaseCommand):
    help = 'Compute the Stalled bundles and Validation insights lists for the Admin Console.'

    def handle(self, *args, **options):
        for key in snapshots.COMPUTE:
            snapshots.store(key)
            self.stdout.write('Computed {0}.'.format(key))
