# -*- coding: utf-8 -*-
"""Bundle downloads, shared by the bundle page and the Admin Console."""
from __future__ import unicode_literals

import os
import tarfile
import tempfile

from django.conf import settings
from django.http import FileResponse


def bundle_tarball_response(directory):
    """The folder as a .tar.gz download.

    Built in an unnamed temp file that's removed when the response closes, so nothing is
    left on disk and two downloads at once can't overwrite each other.
    """
    scratch = getattr(settings, 'TEMPORARY_DIR', '')
    spool = tempfile.TemporaryFile(dir=scratch if scratch and os.path.isdir(scratch) else None)
    try:
        with tarfile.open(fileobj=spool, mode='w:gz') as archive:
            # Symlinks are stored as links, so nothing outside the bundle gets in.
            archive.add(directory, arcname=os.path.basename(directory))
        spool.seek(0)
    except Exception:
        spool.close()
        raise
    return FileResponse(spool, as_attachment=True, content_type='application/gzip',
                        filename='{0}.tar.gz'.format(os.path.basename(directory)))
