"""What's in an uploaded NetCDF file, for the bundle page's inspector.

Header only, drawn the way xarray shows a dataset. Opened with decode_cf=False so it shows
the file as stored. xarray escapes names and attributes, so the HTML is safe to insert.
"""
import hashlib
import os

import xarray as xr
from django.core.cache import cache

# Keyed on path, size and modified time, so a replaced file is never stale.
CACHE_SECONDS = 24 * 60 * 60


def _cache_key(path):
    stat = os.stat(path)
    fingerprint = '{}|{}|{}'.format(path, stat.st_size, stat.st_mtime_ns)
    return 'netcdf_header:' + hashlib.sha1(fingerprint.encode('utf-8')).hexdigest()


def contents_html(path):
    """xarray's HTML view of the file at path. Raises if the file cannot be opened."""
    key = _cache_key(path)
    html = cache.get(key)
    if html is None:
        # Same setting processing uses: HDF5 locking fails on some shared filesystems.
        os.environ.setdefault('HDF5_USE_FILE_LOCKING', 'FALSE')
        with xr.open_dataset(path, engine='netcdf4', decode_cf=False) as dataset:
            html = dataset._repr_html_()
        cache.set(key, html, CACHE_SECONDS)
    return html
