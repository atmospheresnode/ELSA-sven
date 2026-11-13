# -*- coding: utf-8 -*-
"""Everything `manage.py repair_labels --apply` does, for one bundle.

Empty template fields, the old AMA investigation reference, blank ROR IDs, AMA titles and
nan values, duplicate or orphaned bundle member entries, the citation and targets copied
into product labels, and the collection inventory tables.
"""
import os

title = 'Cleaned up labels written by an older version of ELSA'


def apply(bundle):
    from build.label_repair import repair_bundle_members, repair_label
    from build.models import mirror_citation_into_data_products, mirror_targets_into_products
    from build.views import bundle_label_targets

    directory = bundle.directory()
    if not os.path.isdir(directory):
        return []

    changes = []
    for dirpath, _dirnames, filenames in os.walk(directory):
        for filename in sorted(filenames):
            if filename.lower().endswith('.xml'):
                changes.extend(repair_label(os.path.join(dirpath, filename)))

    # Before the inventories get rebuilt.
    changes.extend('bundle member entry: {}'.format(reason)
                   for _lid, reason in repair_bundle_members(bundle))

    # After the labels, so the repaired citation is what gets copied.
    if mirror_citation_into_data_products(bundle):
        changes.append('citation copied into product labels')
    if mirror_targets_into_products(bundle):
        changes.append('targets copied into product labels')

    # Only where needed, since a rebuild stamps a new creation time into the label.
    for collection in bundle_label_targets(bundle):
        try:
            if inventory_stale(collection.label()):
                collection.build_inventory()
                changes.append('inventory')
        except Exception as error:                       # noqa: BLE001 - one collection
            # Same as rebuild_collection_inventories: one bad collection doesn't stop the rest.
            print('label migration 0002: inventory of {} failed: {}'.format(collection, error))
    return changes


def inventory_stale(label_path):
    """No table, the wrong record count, or a table still under the old collection_<x>.csv name."""
    from lxml import etree

    from build.chocolate import inventory_path_for
    from build.label_repair import PDS_NS, inventory_needs_writing

    if not os.path.exists(label_path):
        return False
    if inventory_needs_writing(label_path):
        return True
    try:
        root = etree.parse(label_path).getroot()
    except (etree.XMLSyntaxError, OSError):
        return False
    name = (root.findtext('{0}File_Area_Inventory/{0}File/{0}file_name'.format(PDS_NS)) or '').strip()
    return bool(name) and name != os.path.basename(inventory_path_for(label_path))
