"""Repair bundle labels written before the generator was fixed.

    python3 manage.py repair_labels                  # report only, writes nothing
    python3 manage.py repair_labels --apply
    python3 manage.py repair_labels --apply --bundle 836

A bundle built today validates clean; one built months ago does not, because its
labels are files on disk and no fix reaches back into them. This brings the old ones
up to what the current code writes.

Reports by default and writes nothing. These are archived files, some belonging to
bundles already submitted for review, so the write needs asking for.
"""
import os

from django.core.management.base import BaseCommand

from build.label_repair import (repair_bundle_members, repair_label,
                                stale_bundle_members)
from build.models import Bundle
from build.views import bundle_label_targets, rebuild_collection_inventories


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
    help = 'Remove empty stubs from old labels and rewrite collection inventories.'

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

        for bundle in bundles:
            labels = labels_of(bundle)
            if not labels:
                continue

            removed_here = []
            for label_path in labels:
                if apply_changes:
                    removed = repair_label(label_path)
                else:
                    removed = self._would_remove(label_path)
                if removed:
                    removed_here.append((os.path.basename(label_path), removed))

            # Before the inventories are rebuilt, so they are rebuilt from the bundle
            # label as it will stay.
            members = (repair_bundle_members(bundle) if apply_changes
                       else stale_bundle_members(bundle))

            inventories = 0
            if apply_changes:
                # Membership has not changed, but the table and the record count may
                # never have been written at all.
                inventories = len(bundle_label_targets(bundle))
                rebuild_collection_inventories(bundle)

            if removed_here or inventories or members:
                touched_bundles += 1
                self.stdout.write('{} (#{}, {})'.format(
                    bundle.name, bundle.pk, bundle.bundle_type or 'unknown'))
                for name, removed in removed_here:
                    total_labels += 1
                    total_removed += len(removed)
                    self.stdout.write('    {:44} {} empty container{}: {}'.format(
                        name, len(removed), '' if len(removed) == 1 else 's',
                        ', '.join(sorted(set(removed)))))
                for lid, reason in members:
                    total_members += 1
                    self.stdout.write('    {:44} member entry {}: {}'.format(
                        'bundle label', lid, reason))
                if inventories:
                    self.stdout.write(
                        '    {:44} {} inventory table(s) rewritten'.format(
                            '', inventories))

        self.stdout.write('')
        if not touched_bundles:
            self.stdout.write(self.style.SUCCESS('Nothing to repair.'))
            return

        self.stdout.write('{} empty container(s) across {} label(s) in {} bundle(s).'
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
        from build.label_repair import prune_blank_containers
        try:
            root = etree.parse(label_path).getroot()
        except (etree.XMLSyntaxError, OSError):
            return []
        return prune_blank_containers(root)
