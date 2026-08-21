# -*- coding: utf-8 -*-
"""Backfill the collection scope introduced in 0071.

Two kinds of row predate that migration:

* NetCDFFile rows uploaded before files were filed by collection. Without a collection they would
  vanish from every collection tab once the file list is filtered, so each is attached to a
  collection of its bundle. Where the file is still on disk we can tell which collection directory
  it sits in and use that; otherwise, a bundle with exactly one collection is unambiguous. Anything
  still unresolved is left NULL rather than guessed at.

* AMA rows (ModelMetadata / SimulationConfiguration / FileDescription) that were bundle-scoped.
  Their values were entered to describe the bundle's model output, so they become the default for
  the bundle's first collection rather than being discarded.

Reversing this only clears the columns again; the rows themselves are untouched.
"""

from __future__ import unicode_literals

import os

from django.db import migrations


def bundle_directory(bundle):
    """Mirror of Bundle.directory(): historical models have no methods, so the path is rebuilt."""
    from django.conf import settings

    name_edit = '{0}_bundle'.format(bundle.name.lower().replace(' ', '_'))
    return os.path.join(settings.ARCHIVE_DIR, bundle.user.username, name_edit)


def resolve_collection_for_file(netcdf_file, collections):
    """Pick the collection a legacy NetCDF file belongs to, or None if it cannot be determined."""
    if not collections:
        return None

    # Prefer hard evidence: the file physically sitting in a collection's directory.
    basename = os.path.basename(netcdf_file.file.name or '')
    if basename:
        for collection in collections:
            try:
                candidate = os.path.join(
                    bundle_directory(collection.bundle), collection.collection_name.lower(),
                    basename)
            except Exception:
                continue
            if os.path.exists(candidate):
                return collection

    # Otherwise a single collection is unambiguous.
    if len(collections) == 1:
        return collections[0]

    return None


def backfill(apps, schema_editor):
    NetCDFFile = apps.get_model('build', 'NetCDFFile')
    AdditionalCollections = apps.get_model('build', 'AdditionalCollections')

    collections_by_bundle = {}
    for collection in AdditionalCollections.objects.all().select_related('bundle'):
        collections_by_bundle.setdefault(collection.bundle_id, []).append(collection)

    assigned = 0
    unresolved = 0
    for netcdf_file in NetCDFFile.objects.filter(collection__isnull=True):
        collections = collections_by_bundle.get(netcdf_file.bundle_id, [])
        collection = resolve_collection_for_file(netcdf_file, collections)
        if collection is None:
            unresolved += 1
            continue
        netcdf_file.collection = collection
        netcdf_file.save(update_fields=['collection'])
        assigned += 1

    print('  NetCDF files: {} attached to a collection, {} left unassigned.'.format(
        assigned, unresolved))

    for model_name in ('ModelMetadata', 'SimulationConfiguration', 'FileDescription'):
        model = apps.get_model('build', model_name)
        moved = 0
        for record in model.objects.filter(collection__isnull=True):
            collections = collections_by_bundle.get(record.bundle_id, [])
            if not collections:
                continue
            record.collection = collections[0]
            record.save(update_fields=['collection'])
            moved += 1
        print('  {}: {} row(s) re-scoped to a collection.'.format(model_name, moved))


def clear(apps, schema_editor):
    for model_name in ('NetCDFFile', 'ModelMetadata', 'SimulationConfiguration', 'FileDescription'):
        model = apps.get_model('build', model_name)
        model.objects.update(collection=None)


class Migration(migrations.Migration):

    dependencies = [
        ('build', '0071_ama_collection_scope'),
    ]

    operations = [
        migrations.RunPython(backfill, clear),
    ]
