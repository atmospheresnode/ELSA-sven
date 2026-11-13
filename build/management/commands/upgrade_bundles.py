"""Bring old bundles' labels up to the newest label migration.

    python3 manage.py upgrade_bundles                         # list what's pending, writes nothing
    python3 manage.py upgrade_bundles --apply                 # after each deploy, and nightly from cron
    python3 manage.py upgrade_bundles --bundle 836 --preview  # show the diff, writes nothing
    python3 manage.py upgrade_bundles --bundle 836 --apply --include-submitted
    python3 manage.py upgrade_bundles --undo 42               # put back the files from upgrade #42

Submitted bundles are left out unless --include-submitted, since someone may be reviewing them.
Bundles also upgrade by themselves when opened, checked or submitted.
"""
import time

from django.core.management.base import BaseCommand, CommandError

from build import label_migrations, label_upgrades
from build.models import Bundle, BundleUpgrade


class Command(BaseCommand):
    help = 'Apply pending label migrations (build/label_migrations) to existing bundles.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Actually change the labels. Without it, nothing is written.')
        parser.add_argument('--bundle', type=int, default=None, help='Only this bundle id.')
        parser.add_argument('--include-submitted', action='store_true',
                            help='Also upgrade bundles that have been submitted.')
        parser.add_argument('--preview', action='store_true',
                            help='Print what would change, as a diff, on a copy.')
        parser.add_argument('--limit', type=int, default=0,
                            help='Stop after this many bundles. 0 means no limit.')
        parser.add_argument('--max-minutes', type=float, default=0,
                            help='Stop starting new bundles after this long. 0 means no limit.')
        parser.add_argument('--undo', type=int, default=None, metavar='UPGRADE_ID',
                            help='Put back the files from before this upgrade.')

    def handle(self, *args, **options):
        if options['undo'] is not None:
            return self._undo(options['undo'])

        head = label_migrations.head()
        bundles = Bundle.objects.filter(label_revision__lt=head).select_related('user').order_by('pk')
        if options['bundle'] is not None:
            bundles = Bundle.objects.filter(pk=options['bundle']).select_related('user')
            if not bundles.exists():
                raise CommandError('No bundle {}.'.format(options['bundle']))
        if not options['include_submitted']:
            skipped = bundles.filter(submitted_at__isnull=False).count()
            bundles = bundles.filter(submitted_at__isnull=True)
        else:
            skipped = 0

        bundles = list(bundles)
        self.stdout.write('Newest label migration: {:04d}. {} bundle{} behind.'.format(
            head, len(bundles), '' if len(bundles) == 1 else 's'))
        if skipped:
            self.stdout.write('{} submitted bundle{} left out (use --include-submitted).'.format(
                skipped, '' if skipped == 1 else 's'))

        if options['preview']:
            return self._preview(bundles)
        if not options['apply']:
            for bundle in bundles:
                titles = ['{:04d}'.format(m.number) for m in label_migrations.pending(bundle)
                          if m.applies_to(bundle)]
                self.stdout.write('  {} (#{}, {}, {}): revision {}, pending {}'.format(
                    bundle.name, bundle.pk, bundle.bundle_type, bundle.user.username,
                    bundle.label_revision, ', '.join(titles) or 'none that apply'))
            if bundles:
                self.stdout.write(self.style.WARNING(
                    'Nothing was written. Re-run with --apply, or --preview to see the diff.'))
            return

        started = time.monotonic()
        done = changed = failed = busy = 0
        for bundle in bundles:
            if options['limit'] and done >= options['limit']:
                break
            if options['max_minutes'] and time.monotonic() - started > options['max_minutes'] * 60:
                self.stdout.write('Out of time, the rest wait for the next run.')
                break
            try:
                rows = label_upgrades.upgrade_bundle(
                    bundle, source=label_upgrades.SOURCE_SWEEP,
                    include_submitted=options['include_submitted'], wait_seconds=10)
            except label_upgrades.UpgradeBusy:
                busy += 1
                continue
            done += 1
            for row in rows:
                if row.status == BundleUpgrade.STATUS_FAILED:
                    failed += 1
                    self.stdout.write(self.style.ERROR('  {} (#{}): {:04d} failed: {}'.format(
                        bundle.name, bundle.pk, row.number, row.error[:300])))
                else:
                    changed += 1
                    self.stdout.write('  {} (#{}): {:04d} changed {} file{}'.format(
                        bundle.name, bundle.pk, row.number, len(row.files),
                        '' if len(row.files) == 1 else 's'))

        pruned = label_upgrades.prune_snapshots()
        self.stdout.write('')
        self.stdout.write('{} bundle{} done, {} change{}, {} failure{}{}. {} old snapshot{} removed.'.format(
            done, '' if done == 1 else 's', changed, '' if changed == 1 else 's',
            failed, '' if failed == 1 else 's',
            ', {} busy and skipped'.format(busy) if busy else '',
            pruned, '' if pruned == 1 else 's'))
        if failed:
            self.stdout.write(self.style.WARNING('Failed bundles were put back as they were.'))
        else:
            self.stdout.write(self.style.SUCCESS('Done.'))

    def _preview(self, bundles):
        for bundle in bundles:
            self.stdout.write('')
            self.stdout.write('== {} (#{}, {}) =='.format(bundle.name, bundle.pk, bundle.bundle_type))
            results = label_upgrades.preview_bundle(bundle)
            if not results:
                self.stdout.write('  nothing to change')
            for result in results:
                self.stdout.write('-- {:04d} {}'.format(result['number'], result['title']))
                if result['error']:
                    self.stdout.write(self.style.ERROR('  would fail: {}'.format(result['error'])))
                elif not result['diff']:
                    self.stdout.write('  no change')
                else:
                    self.stdout.write(result['diff'])
        self.stdout.write(self.style.WARNING('Preview only, nothing was written.'))

    def _undo(self, pk):
        row = BundleUpgrade.objects.filter(pk=pk).select_related('bundle').first()
        if row is None:
            raise CommandError('No upgrade {}.'.format(pk))
        try:
            label_upgrades.undo(row)
        except ValueError as error:
            raise CommandError(str(error))
        self.stdout.write(self.style.SUCCESS('Put back {} file{} in {} from before {:04d}_{}.'.format(
            len(row.files), '' if len(row.files) == 1 else 's', row.bundle.name, row.number, row.name)))
