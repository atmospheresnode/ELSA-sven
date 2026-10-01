"""What is inside an uploaded NetCDF file, for the bundle page's inspector.

The header only: dimensions, coordinates, variables and their attributes, drawn the way
xarray draws a dataset in a notebook, which is the view most of ELSA's users already read
every day. Opened with decode_cf=False so the panel shows the file as stored (raw dtypes,
_FillValue, scale_factor, time units) rather than xarray's interpretation of it, since the
stored form is what the PDS4 label describes.

No data values are read beyond the dimension coordinates xarray indexes on open, so a file
of any size opens in roughly the time it takes to read its header. xarray escapes every
name and attribute it writes into the HTML, so the result is safe to insert into the page.
"""
import hashlib
import os

import xarray as xr
from django.core.cache import cache

# Keyed on path, size and modification time, so a replaced file is never shown stale and an
# unchanged one is read once a day at most.
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
