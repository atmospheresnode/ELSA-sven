"""Clear out unfinished NetCDF uploads that have expired, and staging files with no upload behind them.

Meant for a daily cron:
    python manage.py purge_netcdf_uploads
Finished uploads are never touched: their files are in the archive now.
"""
from django.core.management.base import BaseCommand

from build import netcdf_uploads


class Command(BaseCommand):
    help = 'Delete expired unfinished NetCDF uploads and orphaned staging files.'

    def handle(self, *args, **options):
        expired = netcdf_uploads.purge_expired()
        orphans = netcdf_uploads.purge_orphans()
        self.stdout.write('Removed {} expired upload(s) and {} orphaned staging file(s).'.format(
            expired, orphans))
