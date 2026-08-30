# -*- coding: utf-8 -*-
"""Repair <collection_type> in collection labels that were already written to disk.

Two values ELSA has been writing are not members of the PDS4 Collection/type enumeration for the
information model it targets, so the labels do not validate:

* "XML_Schema" is spelled "XML Schema" in the enumeration. The underscore belongs to ELSA's
  internal key (the directory name, the LID segment, the label filename), not to the label.

* An External (AMA) bundle's document collection was labelled "Document". Its members carry LIDs
  in the urn:nasa:pds-ama namespace, which a Document collection does not admit; every collection
  in an External bundle is of type External, the document collection included. Only the declared
  type changes - the directory is still `document` and the LID still ends in `:document`.

The second one also travels into the bundle label: Bundle_Member_Entry/reference_type has to agree
with the type of the collection it points at, so bundle_has_document_collection becomes
bundle_has_external_collection for those entries.

Bundles whose labels are missing from disk are skipped rather than treated as an error: ELSA
already tolerates a bundle whose directory has been removed by hand.

Reversing this is a no-op. Putting the invalid values back has no use, and the labels are
regenerated with the correct values from here on.
"""

from __future__ import unicode_literals

import os

from django.conf import settings
from django.db import migrations

from build.chocolate import close_label, open_label_with_tree

NAMESPACE = '{http://pds.nasa.gov/pds4/pds/v1}'

# Stored Product_Collection.collection -> PDS4 Collection/type, for Archive bundles. Anything not
# listed here already spells out to its enumerated value.
PDS4_COLLECTION_TYPES = {
    'XML_Schema': 'XML Schema',
}

PDS4_REFERENCE_TYPES = {
    'XML Schema': 'bundle_has_schema_collection',
}


def bundle_directory(bundle):
    """Mirror of Bundle.directory(): historical models have no methods, so the path is rebuilt."""
    name_edit = '{0}_bundle'.format(bundle.name.lower().replace(' ', '_'))
    return os.path.join(settings.ARCHIVE_DIR, bundle.user.username, name_edit)


def collection_label(bundle, collection):
    """Mirror of Product_Collection.label()."""
    name_edit = collection.collection.lower()
    bundle_id = (bundle.bundleID or '').strip().lower().replace(' ', '_')
    return os.path.join(
        bundle_directory(bundle), name_edit,
        'collection_{0}_{1}.xml'.format(bundle_id, name_edit))


def bundle_label(bundle):
    """Mirror of Product_Bundle.label()."""
    bundle_id = (bundle.bundleID or '').strip().lower().replace(' ', '_')
    return os.path.join(bundle_directory(bundle), 'bundle_{0}.xml'.format(bundle_id))


def pds4_collection_type(bundle, collection):
    if bundle.bundle_type == 'External':
        return 'External'
    return PDS4_COLLECTION_TYPES.get(collection.collection, collection.collection)


def pds4_reference_type(collection_type):
    return PDS4_REFERENCE_TYPES.get(
        collection_type, 'bundle_has_{0}_collection'.format(collection_type.lower()))


def repair_collection_label(path, wanted):
    """Set <collection_type> in one collection label. Returns True when the file changed."""
    label = open_label_with_tree(path)
    root, tree = label[1], label[2]

    collection = root.find('{0}Collection'.format(NAMESPACE))
    if collection is None:
        return False
    col_type = collection.find('{0}collection_type'.format(NAMESPACE))
    if col_type is None or (col_type.text or '').strip() == wanted:
        return False

    col_type.text = wanted
    close_label(path, root, tree)
    return True


def repair_bundle_label(path, wanted_by_lid_suffix):
    """Realign each Bundle_Member_Entry's reference_type with its collection's type.

    Entries are matched on the last segment of lid_reference, which is the collection key the
    member entry was built from. Entries for collections we know nothing about (the user's own
    AdditionalCollections, which already carry a valid type) are left alone.
    """
    label = open_label_with_tree(path)
    root, tree = label[1], label[2]

    changed = False
    for entry in root.findall('{0}Bundle_Member_Entry'.format(NAMESPACE)):
        lid_reference = entry.find('{0}lid_reference'.format(NAMESPACE))
        reference_type = entry.find('{0}reference_type'.format(NAMESPACE))
        if lid_reference is None or reference_type is None or not lid_reference.text:
            continue

        suffix = lid_reference.text.strip().rsplit(':', 1)[-1]
        wanted = wanted_by_lid_suffix.get(suffix)
        if wanted is None or (reference_type.text or '').strip() == wanted:
            continue

        reference_type.text = wanted
        changed = True

    if changed:
        close_label(path, root, tree)
    return changed


def repair(apps, schema_editor):
    Bundle = apps.get_model('build', 'Bundle')
    Product_Collection = apps.get_model('build', 'Product_Collection')

    collections_by_bundle = {}
    for collection in Product_Collection.objects.all():
        collections_by_bundle.setdefault(collection.bundle_id, []).append(collection)

    collections_fixed = 0
    bundles_fixed = 0
    skipped = 0

    for bundle in Bundle.objects.all().select_related('user'):
        collections = collections_by_bundle.get(bundle.id, [])
        if not collections:
            continue

        wanted_by_lid_suffix = {}
        for collection in collections:
            # Data collections live in data_<processing_level> directories and have no single
            # label of their own, so there is nothing here to repair for them.
            if collection.collection in ('Data', 'Not_Set'):
                continue

            wanted = pds4_collection_type(bundle, collection)
            wanted_by_lid_suffix[collection.collection.lower()] = pds4_reference_type(wanted)

            path = collection_label(bundle, collection)
            if not os.path.isfile(path):
                skipped += 1
                continue
            try:
                if repair_collection_label(path, wanted):
                    collections_fixed += 1
            except Exception as error:
                print('  could not repair {0}: {1}'.format(path, error))

        path = bundle_label(bundle)
        if not os.path.isfile(path):
            skipped += 1
            continue
        try:
            if repair_bundle_label(path, wanted_by_lid_suffix):
                bundles_fixed += 1
        except Exception as error:
            print('  could not repair {0}: {1}'.format(path, error))

    print('  collection labels retyped: {0}'.format(collections_fixed))
    print('  bundle labels realigned:   {0}'.format(bundles_fixed))
    print('  labels missing from disk:  {0}'.format(skipped))


class Migration(migrations.Migration):

    dependencies = [
        ('build', '0072_backfill_ama_collection_scope'),
    ]

    operations = [
        migrations.RunPython(repair, migrations.RunPython.noop),
    ]
