"""Build a release's delivery package on the server, for bundles too big for the web.

    python3 manage.py build_release 12

Run as a user who can read the archive and write the releases folder.
"""
from django.core.management.base import BaseCommand, CommandError

from staff import audit, releases
from staff.actions import ActionError
from staff.models import Release

from ._staffing import server_actor


class Command(BaseCommand):
    help = 'Build the delivery package for a release.'

    def add_arguments(self, parser):
        parser.add_argument('release_id', type=int)

    def handle(self, *args, **options):
        release = Release.objects.select_related('bundle', 'final_run').filter(pk=options['release_id']).first()
        if release is None:
            raise CommandError('No release {0}.'.format(options['release_id']))
        if release.released_at:
            raise CommandError('That release is already finished.')
        try:
            path = releases.build_package(release)
        except ActionError as error:
            raise CommandError(str(error))
        release.refresh_from_db()
        audit.record(None, 'release.package', release.bundle, actor_name=server_actor(),
                     changes={'release': release.pk, 'files': release.package_files,
                              'sha256': release.package_sha256})
        self.stdout.write(self.style.SUCCESS('Built {0} ({1} files).'.format(path, release.package_files)))
