# -*- coding: utf-8 -*-
"""Review workspace: what to check, what changed since the last submission, and reading
bundle files in the console. Read-only, nothing here writes to the bundle folder.
"""
from __future__ import unicode_literals

import difflib
import hashlib
import os
import zlib

from django.conf import settings

from .models import SnapshotFile, SubmissionSnapshot


# --- Checklist ---

COMMON_CHECKS = (
    ('validate', 'PDS validate reports no errors on the latest files'),
    ('citation', 'Citation, title and description are accurate and complete'),
    ('history', 'Modification history describes this version'),
    ('context', 'Targets and other context products are the right ones'),
    ('documentation', 'Documentation is enough for someone else to use the data'),
    ('names', 'File and product names are consistent and meaningful'),
)

TYPE_CHECKS = {
    'External': (
        ('netcdf', 'NetCDF variables, units and coordinates are described'),
        ('ama', 'Model metadata (AMA) is filled in and plausible'),
        ('usable', 'A sample file opens and plots as described'),
    ),
    'Archive': (
        ('inventory', 'Collection inventories list every product'),
        ('tables', 'Table definitions match the data files'),
    ),
}


def checklist_for(case):
    bundle_type = (case.bundle.bundle_type if case.bundle else case.data.get('bundle_type', '')) or ''
    items = COMMON_CHECKS + TYPE_CHECKS.get(bundle_type, ())
    answers = case.checklist or {}
    return [{'key': key, 'label': label, 'done': bool(answers.get(key))} for key, label in items]


def checklist_open(case):
    return [item for item in checklist_for(case) if not item['done']]


# --- Reading the bundle folder ---

MAX_FILES = 20000
HASH_LIMIT = 5 * 1024 * 1024        # Hash labels and small files; big data files by size and time.
VIEW_LIMIT = 512 * 1024             # Largest file shown in the browser.


def _root():
    return os.path.realpath(settings.ARCHIVE_DIR)


def bundle_folder(bundle):
    """The bundle's folder, or None if it is missing or not under the archive."""
    folder = os.path.realpath(bundle.directory())
    root = _root()
    if not (folder == root or folder.startswith(root + os.sep)) or not os.path.isdir(folder):
        return None
    return folder


def resolve(bundle, relative):
    """Absolute path for a file the reviewer asked for, or None.

    Has to stay inside the bundle folder after following symlinks.
    """
    folder = bundle_folder(bundle)
    if folder is None or not relative:
        return None
    relative = relative.replace('\\', '/').lstrip('/')
    if '\x00' in relative or '..' in relative.split('/'):
        return None
    target = os.path.realpath(os.path.join(folder, relative))
    if not target.startswith(folder + os.sep) or not os.path.isfile(target):
        return None
    return target


def walk(bundle):
    """[(relative path, size, mtime)] for every regular file, links left out."""
    folder = bundle_folder(bundle)
    if folder is None:
        return []
    found = []
    for current, dirs, files in os.walk(folder, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not os.path.islink(os.path.join(current, d)))
        for name in sorted(files):
            path = os.path.join(current, name)
            if os.path.islink(path):
                continue
            try:
                info = os.stat(path)
            except OSError:
                continue
            found.append((os.path.relpath(path, folder).replace(os.sep, '/'), info.st_size, info.st_mtime))
            if len(found) >= MAX_FILES:
                return found
    return found


def tree(bundle):
    """The files grouped by folder, for the inspector's list."""
    groups = {}
    for relative, size, mtime in walk(bundle):
        folder, _, name = relative.rpartition('/')
        groups.setdefault(folder or '.', []).append({'path': relative, 'name': name, 'size': size,
                                                     'mtime': mtime})
    return sorted(groups.items())


def read_text(path):
    """Returns (text, note). Text is None for a binary or oversized file, note says why."""
    size = os.path.getsize(path)
    if size > VIEW_LIMIT:
        return None, 'This file is {0:.1f} MB, too large to show here. Download it instead.'.format(
            size / 1048576.0)
    with open(path, 'rb') as handle:
        raw = handle.read()
    if b'\x00' in raw[:8192]:
        return None, 'This is a binary file. Download it to open it.'
    return raw.decode('utf-8', errors='replace'), ''


# --- Snapshots ---

def _fingerprint(path, size, mtime):
    if size > HASH_LIMIT:
        return 'mtime:{0:.0f}'.format(mtime)
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest()


# Text saved per submission for comparing. Labels and small text files only, with size limits
# so a bundle full of tables can't fill the database.
TEXT_SUFFIXES = ('.xml', '.txt', '.lblx', '.xsd', '.sch', '.csv', '.tab')
TEXT_FILE_LIMIT = 256 * 1024
TEXT_TOTAL_LIMIT = 8 * 1024 * 1024


def take_snapshot(bundle):
    """Save the bundle's files as they are now. Called at each submission."""
    folder = bundle_folder(bundle)
    if folder is None:
        return None
    manifest = {}
    kept = []
    budget = TEXT_TOTAL_LIMIT
    for relative, size, mtime in walk(bundle):
        path = os.path.join(folder, relative)
        try:
            manifest[relative] = [size, _fingerprint(path, size, mtime)]
        except OSError:
            continue
        if relative.lower().endswith(TEXT_SUFFIXES) and size <= TEXT_FILE_LIMIT and size <= budget:
            try:
                with open(path, 'rb') as handle:
                    raw = handle.read(TEXT_FILE_LIMIT + 1)
            except OSError:
                continue
            if b'\x00' in raw[:8192]:
                continue
            budget -= len(raw)
            kept.append((relative, zlib.compress(raw, 6)))
    snapshot = SubmissionSnapshot.objects.create(bundle=bundle, manifest=manifest)
    SnapshotFile.objects.bulk_create([SnapshotFile(snapshot=snapshot, path=p, data=d) for p, d in kept])
    return snapshot


DIFF_LINE_LIMIT = 5000
CONTEXT = 3


def file_diff(bundle, relative):
    """What changed in one file between the last two submissions. None if nothing to compare.

    Rows are (kind, old line number, new line number, text).
    """
    latest = list(SubmissionSnapshot.objects.filter(bundle=bundle)[:2])
    if len(latest) < 2:
        return None
    new = SnapshotFile.objects.filter(snapshot=latest[0], path=relative).first()
    old = SnapshotFile.objects.filter(snapshot=latest[1], path=relative).first()
    if new is None and old is None:
        return None
    old_lines = (old.text() if old else '').splitlines()[:DIFF_LINE_LIMIT]
    new_lines = (new.text() if new else '').splitlines()[:DIFF_LINE_LIMIT]
    rows = []
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            span = list(range(i2 - i1))
            if len(span) > 2 * CONTEXT + 1:
                for k in span[:CONTEXT]:
                    rows.append(('same', i1 + k + 1, j1 + k + 1, old_lines[i1 + k]))
                rows.append(('skip', None, None, '{0} unchanged lines'.format(len(span) - 2 * CONTEXT)))
                for k in span[-CONTEXT:]:
                    rows.append(('same', i1 + k + 1, j1 + k + 1, old_lines[i1 + k]))
            else:
                for k in span:
                    rows.append(('same', i1 + k + 1, j1 + k + 1, old_lines[i1 + k]))
            continue
        for k in range(i1, i2):
            rows.append(('removed', k + 1, None, old_lines[k]))
        for k in range(j1, j2):
            rows.append(('added', None, k + 1, new_lines[k]))
    return {'rows': rows, 'since': latest[1].taken_at, 'old_missing': old is None, 'new_missing': new is None,
            'changed': any(r[0] in ('added', 'removed') for r in rows)}


def changes_since_last(bundle):
    """Files added, removed and changed between the last two submissions, or None if only one."""
    latest = list(SubmissionSnapshot.objects.filter(bundle=bundle)[:2])
    if len(latest) < 2:
        return None
    new, old = latest[0].manifest, latest[1].manifest
    return {
        'since': latest[1].taken_at,
        'added': sorted(set(new) - set(old)),
        'removed': sorted(set(old) - set(new)),
        'changed': sorted(path for path in set(new) & set(old) if new[path] != old[path]),
    }
