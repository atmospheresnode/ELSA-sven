"""Rebuild NetCDF labels written before the generator fixes.

    python3 manage.py rebuild_netcdf_labels                 # report only, writes nothing
    python3 manage.py rebuild_netcdf_labels --apply         # rewrite them
    python3 manage.py rebuild_netcdf_labels --apply --bundle 831

Labels from before 2026-09-11 say "sample_bundle" in their identifier, and have other old
problems too, so these get rebuilt instead of patched. Only writes with --apply.
"""
import os

from django.core.management.base import BaseCommand

from build.models import Bundle, NetCDFFile
from build.views import regenerate_netcdf_labels

# What an old label says where the bundle identifier belongs.
PLACEHOLDER = 'pds-ama:sample_bundle'


def stale_labels(bundle):
    """Paths of this bundle's labels still carrying the placeholder identifier."""
    found = []
    directory = bundle.directory()
    if not os.path.isdir(directory):
        return found

    for dirpath, _dirnames, filenames in os.walk(directory):
        for filename in filenames:
            if not filename.lower().endswith('.xml'):
                continue
            path = os.path.join(dirpath, filename)
            try:
                with open(path, encoding='utf-8', errors='replace') as label:
                    if PLACEHOLDER in label.read():
                        found.append(path)
            except OSError:
                # Can't read this label and this command can't fix that, so skip it.
                continue
    return found


class Command(BaseCommand):
    help = ('Rebuild NetCDF labels whose product identifier still says '
            '"sample_bundle" instead of the bundle they belong to.')

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Actually rewrite the labels. Without it, nothing is written.')
        parser.add_argument(
            '--bundle', type=int, default=None,
            help='Only this bundle id, rather than every affected bundle.')

    def handle(self, *args, **options):
        bundles = Bundle.objects.all()
        if options['bundle'] is not None:
            bundles = bundles.filter(pk=options['bundle'])

        affected = []
        for bundle in bundles:
            stale = stale_labels(bundle)
            if stale:
                affected.append((bundle, stale))

        if not affected:
            self.stdout.write(self.style.SUCCESS(
                'No labels carry the placeholder identifier.'))
            return

        total = sum(len(paths) for _bundle, paths in affected)
        self.stdout.write('{} label{} across {} bundle{} still {} "{}":'.format(
            total, '' if total == 1 else 's', len(affected),
            '' if len(affected) == 1 else 's',
            'says' if total == 1 else 'say', PLACEHOLDER))
        self.stdout.write('')

        for bundle, paths in affected:
            self.stdout.write('  {} ({})  {} label{}'.format(
                bundle.name, bundle.user.username, len(paths),
                '' if len(paths) == 1 else 's'))

        if not options['apply']:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                'Nothing was written. Re-run with --apply to rebuild them.'))
            return

        self.stdout.write('')
        rebuilt = 0
        skipped = 0
        failed = []

        for bundle, paths in affected:
            # Only files still on disk, since the label comes from the file's metadata.
            rows = [row for row in NetCDFFile.objects.filter(bundle=bundle)
                    if os.path.exists(
                        os.path.join(row.directory(), os.path.basename(row.file.name)))]

            missing = NetCDFFile.objects.filter(bundle=bundle).count() - len(rows)
            if missing:
                skipped += missing

            if not rows:
                self.stdout.write(self.style.WARNING(
                    '  {}: no NetCDF files on disk, cannot rebuild'.format(bundle.name)))
                continue

            try:
                errors = regenerate_netcdf_labels(bundle, rows)
            except Exception as error:
                # Keep going if one bundle fails.
                failed.append((bundle.name, str(error)))
                continue

            if errors:
                failed.append((bundle.name, '; '.join(errors)))
                continue

            remaining = stale_labels(bundle)
            rebuilt += len(rows)
            if remaining:
                self.stdout.write(self.style.WARNING(
                    '  {}: rebuilt, but {} label(s) still carry it'.format(
                        bundle.name, len(remaining))))
            else:
                self.stdout.write('  {}: rebuilt {} label(s)'.format(
                    bundle.name, len(rows)))

        self.stdout.write('')
        self.stdout.write('  {} label(s) rebuilt, {} skipped for a missing file'.format(
            rebuilt, skipped))
        for name, error in failed:
            self.stdout.write(self.style.ERROR('  {}: {}'.format(name, error[:150])))

        if not failed:
            self.stdout.write(self.style.SUCCESS('  Done.'))
