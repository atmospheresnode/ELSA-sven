# -*- coding: utf-8 -*-
"""Rebuild NetCDF labels whose identifier still says sample_bundle.

Same as `manage.py rebuild_netcdf_labels --apply` for one bundle.
"""
import os

title = 'Fixed data product labels that named a placeholder bundle instead of this one'

bundle_types = ('External',)


def apply(bundle):
    from build.management.commands.rebuild_netcdf_labels import stale_labels
    from build.models import NetCDFFile
    from build.views import regenerate_netcdf_labels

    stale = stale_labels(bundle)
    if not stale:
        return []

    # Only files still on disk, since the label comes from the file itself.
    rows = [row for row in NetCDFFile.objects.filter(bundle=bundle)
            if row.file and os.path.exists(
                os.path.join(row.directory(), os.path.basename(row.file.name)))]
    if not rows:
        return []

    errors = regenerate_netcdf_labels(bundle, rows)
    if errors:
        raise RuntimeError('; '.join(errors))
    return ['data product label rebuilt'] * len(rows)
