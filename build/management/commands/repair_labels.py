"""Repair bundle labels written before the generator fixes.

    python3 manage.py repair_labels                  # report only, writes nothing
    python3 manage.py repair_labels --apply
    python3 manage.py repair_labels --apply --bundle 836

Only writes with --apply.
"""
import os

from django.core.management.base import BaseCommand

from build.chocolate import inventory_path_for
from build.label_repair import (repair_bundle_members, repair_label,
                                stale_bundle_members)
from build.models import Bundle
from build.views import bundle_label_targets, rebuild_collection_inventories
from build.label_repair import stray_root_files
from build.models import mirror_citation_into_data_products


def labels_of(bundle):
    """Every XML label in this bundle's directory."""
    found = []
    directory = bundle.directory()
    if not os.path.isdir(directory):
        return found
    for dirpath, _dirnames, filenames in os.walk(directory):
        for filename in sorted(filenames):
            if filename.lower().endswith('.xml'):
                found.append(os.path.join(dirpath, filename))
    return found


class Command(BaseCommand):
    help = ('Remove empty stubs from old labels, correct the old AMA investigation '
            'reference, fix bundle member entries and rewrite collection inventories.')

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Actually rewrite the labels. Without it, nothing is written.')
        parser.add_argument(
            '--bundle', type=int, default=None,
            help='Only this bundle id, rather than every bundle.')

    def handle(self, *args, **options):
        bundles = Bundle.objects.all().order_by('pk')
        if options['bundle'] is not None:
            bundles = bundles.filter(pk=options['bundle'])

        apply_changes = options['apply']
        total_labels = 0
        total_removed = 0
        touched_bundles = 0
        total_members = 0
        skipped = []
        skipped_labels = []

        for bundle in bundles:
            labels = labels_of(bundle)
            if not labels:
                continue

            removed_here = []
            for label_path in labels:
                try:
                    if apply_changes:
                        removed = repair_label(label_path)
                    else:
                        removed = self._would_remove(label_path)
                except Exception as error:               # noqa: BLE001 - one label
                    skipped_labels.append((label_path, error))
                    continue
                if removed:
                    removed_here.append((os.path.basename(label_path), removed))

            # Keep going if one bundle fails. Some prod bundles have no bundleID.
            members, inventories = [], 0
            try:
                # Before the inventories get rebuilt.
                members = (repair_bundle_members(bundle) if apply_changes
                           else stale_bundle_members(bundle))

                if apply_changes:
                    # Data labels have their own copy of the citation, so copy it over again.
                    if bundle.bundle_type == 'External':
                        mirror_citation_into_data_products(bundle)

                    # The table and record count may never have been written.
                    inventories = len(bundle_label_targets(bundle))
                    rebuild_collection_inventories(bundle)
                else:
                    # Report the ones that would change: no table yet, or one still
                    # under the old collection_<x>.csv name.
                    inventories = sum(
                        1 for collection in bundle_label_targets(bundle)
                        if os.path.exists(collection.label())
                        and not os.path.exists(inventory_path_for(collection.label())))
            except Exception as error:                   # noqa: BLE001 - see comment
                skipped.append((bundle, error))

            # Reported either way and never removed: these may be someone's data.
            for name in stray_root_files(bundle):
                self.stdout.write('{} (#{}): file outside any collection, not touched: {}'.format(
                    bundle.name, bundle.pk, name))

            if removed_here or inventories or members:
                touched_bundles += 1
                self.stdout.write('{} (#{}, {})'.format(
                    bundle.name, bundle.pk, bundle.bundle_type or 'unknown'))
                for name, removed in removed_here:
                    total_labels += 1
                    total_removed += len(removed)
                    self.stdout.write('    {:44} {} fix{}: {}'.format(
                        name, len(removed), '' if len(removed) == 1 else 'es',
                        ', '.join(sorted(set(removed)))))
                for lid, reason in members:
                    total_members += 1
                    self.stdout.write('    {:44} member entry {}: {}'.format(
                        'bundle label', lid, reason))
                if inventories:
                    self.stdout.write(
                        '    {:44} {} inventory table(s) {}'.format(
                            '', inventories,
                            'rewritten' if apply_changes else 'missing or under the old name'))

        self.stdout.write('')
        for label_path, error in skipped_labels:
            self.stdout.write(self.style.WARNING('Skipped {}: {}: {}'.format(
                label_path, type(error).__name__, error)))
        for bundle, error in skipped:
            self.stdout.write(self.style.WARNING(
                'Skipped the bundle label and inventories of {} (#{}): {}: {}'.format(
                    bundle.name, bundle.pk, type(error).__name__, error)))
        if not touched_bundles:
            self.stdout.write(self.style.SUCCESS('Nothing to repair.'))
            return

        self.stdout.write('{} fix(es) across {} label(s) in {} bundle(s).'
                          .format(total_removed, total_labels, touched_bundles))
        if total_members:
            self.stdout.write('{} duplicate or orphaned bundle member entr{}.'.format(
                total_members, 'y' if total_members == 1 else 'ies'))

        if not apply_changes:
            self.stdout.write(self.style.WARNING(
                'Nothing was written. Re-run with --apply to repair them.'))
        else:
            self.stdout.write(self.style.SUCCESS('Done.'))

    def _would_remove(self, label_path):
        """What --apply would remove, without touching the file."""
        from lxml import etree
        from build.label_repair import repair_root
        try:
            root = etree.parse(label_path).getroot()
        except (etree.XMLSyntaxError, OSError):
            return []
        return repair_root(root)
