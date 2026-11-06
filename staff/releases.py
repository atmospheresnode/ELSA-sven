# -*- coding: utf-8 -*-
"""Releasing an accepted bundle into the PDS archive: final validate, delivery package, DOI,
delivery to the Engineering Node, registry, release.

ELSA builds the package and keeps the record. Staff do the delivery and registry steps
themselves, nothing is sent to EN or the DOI service from here.
"""
from __future__ import unicode_literals

import hashlib
import logging
import os
import re
import shutil
import tarfile
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import audit, roles
from .actions import ActionError, _not_own, _require, _storable
from .models import Announcement, Case, Release


logger = logging.getLogger(__name__)

PDS_NS = '{http://pds.nasa.gov/pds4/pds/v1}'
# DOI format: "10." + registrant code + "/" + suffix (DataCite).
DOI_PATTERN = re.compile(r'^10\.\d{4,9}/[-._;()/:A-Za-z0-9]{1,180}$')
# Larger bundles are packaged with `manage.py build_release <id>` instead of in a web request.
WEB_PACKAGE_LIMIT = 2 * 1024 ** 3


# Unreleased data, so only the ELSA account and group can read packages on the shared server.
FOLDER_MODE = 0o750


def release_root():
    root = getattr(settings, 'ELSA_RELEASE_DIR', '') or os.path.join(settings.BASE_DIR, 'releases')
    os.makedirs(root, mode=FOLDER_MODE, exist_ok=True)
    return root


def _release_dir(release):
    path = os.path.join(release_root(), 'bundle-{0}'.format(release.bundle_id), 'release-{0}'.format(release.pk))
    os.makedirs(path, mode=FOLDER_MODE, exist_ok=True)
    return path


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _require_release_rights(request, release):
    _require(request, roles.REVIEW_BUNDLES)
    if release.case is not None:
        _not_own(request, release.case)
    elif release.bundle.user_id == request.user.pk:
        raise ActionError('This is your own bundle. A colleague has to release it.')


# --- Reading labels safely ---

def _safe_parser():
    """Labels come from users, so no entities, DTDs or network access when parsing."""
    from lxml import etree
    return etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False,
                           remove_comments=True)


def lidvid(path):
    """'urn:...::1.0' from a label's Identification_Area, or '' for anything else."""
    from lxml import etree
    try:
        tree = etree.parse(path, _safe_parser())
    except (etree.XMLSyntaxError, OSError, ValueError):
        return ''
    root = tree.getroot()
    area = root.find(PDS_NS + 'Identification_Area')
    if area is None:
        return ''
    lid = (area.findtext(PDS_NS + 'logical_identifier') or '').strip()
    vid = (area.findtext(PDS_NS + 'version_id') or '').strip()
    if not lid:
        return ''
    return '{0}::{1}'.format(lid, vid) if vid else lid


def _md5(path):
    # PDS defines the checksum manifest as MD5. It's a checksum, not a security control.
    digest = hashlib.md5(usedforsecurity=False)
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


# --- Steps ---

def start(request, case):
    """Opens a release for an accepted bundle."""
    _require(request, roles.REVIEW_BUNDLES)
    _not_own(request, case)
    if case.kind != Case.SUBMISSION or case.bundle is None:
        raise ActionError('Only a submitted bundle can be released.')
    if case.stage != Case.STAGE_ACCEPTED:
        raise ActionError('Accept the bundle first.')
    open_release = Release.objects.filter(bundle=case.bundle, released_at__isnull=True).first()
    if open_release is not None:
        return open_release
    with transaction.atomic():
        count = Release.objects.filter(bundle=case.bundle).count()
        release = Release.objects.create(bundle=case.bundle, case=case, label='Release {0}'.format(count + 1),
                                         created_by=request.user)
        audit.record(request, 'release.start', case.bundle, changes={'release': release.pk})
    return release


def run_final_validate(request, release):
    from build import validate_runner
    from build.models import ValidationRun
    _require_release_rights(request, release)
    if release.released_at:
        raise ActionError('This release is finished.')
    run = validate_runner.start(release.bundle, ValidationRun.TIER_FULL)
    if getattr(run, 'pk', None) is None:
        raise ActionError('The server is already running as many checks as it allows. Try again in a few minutes.')
    Release.objects.filter(pk=release.pk).update(final_run=run)
    audit.record(request, 'release.validate', release.bundle, changes={'release': release.pk, 'run': run.pk})
    return run


def _ready_for_package(release):
    run = release.final_run
    if run is None or run.status != 'd':
        return 'Run the final full validate first, and wait for it to finish.'
    if run.error_count:
        return 'The final validate found {0} error{1}. Fix them and run it again.'.format(
            run.error_count, '' if run.error_count == 1 else 's')
    try:
        stale = run.is_stale()
    except Exception:
        # Can't confirm the files are unchanged, so refuse.
        stale = True
    if stale:
        return 'The bundle changed after the final validate (or ELSA could not confirm it did not). Run it again.'
    return ''


def build_package(release, actor=None, request=None):
    """Build the delivery package: a .tar.gz with an MD5 manifest and a LID::VID transfer manifest.

    Written to a temp file first so a failure can't leave half a package.
    """
    from . import review
    problem = _ready_for_package(release)
    if problem:
        raise ActionError(problem)
    folder = review.bundle_folder(release.bundle)
    if folder is None:
        raise ActionError('The bundle folder is missing on the server.')
    files = review.walk(release.bundle)
    total = sum(size for _, size, _ in files)
    target_dir = _release_dir(release)
    if shutil.disk_usage(target_dir).free < total * 1.2 + 50 * 1024 ** 2:
        raise ActionError('Not enough free disk space to build the package.')

    name = os.path.basename(folder.rstrip(os.sep))
    checksum_lines, transfer_lines = [], []
    for relative, _size, _mtime in files:
        path = os.path.join(folder, relative)
        checksum_lines.append('{0}  {1}/{2}'.format(_md5(path), name, relative))
        if relative.lower().endswith('.xml'):
            ident = lidvid(path)
            if ident:
                transfer_lines.append('{0:<100} {1}/{2}'.format(ident, name, relative))

    checksum_path = os.path.join(target_dir, '{0}_checksum_manifest.txt'.format(name))
    transfer_path = os.path.join(target_dir, '{0}_transfer_manifest.txt'.format(name))
    with open(checksum_path, 'w', encoding='utf-8') as handle:
        handle.write('\n'.join(checksum_lines) + '\n')
    with open(transfer_path, 'w', encoding='utf-8') as handle:
        handle.write('\n'.join(transfer_lines) + '\n')

    final = os.path.join(target_dir, '{0}.tar.gz'.format(name))
    scratch = final + '.part'
    with tarfile.open(scratch, 'w:gz') as archive:
        for relative, _size, _mtime in files:
            # Regular files only (review.walk skips links), each added on its own.
            archive.add(os.path.join(folder, relative), arcname='{0}/{1}'.format(name, relative), recursive=False)
        archive.add(checksum_path, arcname=os.path.basename(checksum_path))
        archive.add(transfer_path, arcname=os.path.basename(transfer_path))
    os.replace(scratch, final)

    digest = _sha256(final)
    Release.objects.filter(pk=release.pk).update(
        package_path=final, package_size=os.path.getsize(final), package_sha256=digest,
        package_files=len(files), package_built_at=timezone.now())
    if request is not None:
        audit.record(request, 'release.package', release.bundle,
                     changes={'release': release.pk, 'files': len(files), 'sha256': digest})
    return final


def package_from_web(request, release):
    _require_release_rights(request, release)
    if release.released_at:
        raise ActionError('This release is finished.')
    from . import review
    total = sum(size for _, size, _ in review.walk(release.bundle))
    if total > WEB_PACKAGE_LIMIT:
        raise ActionError('This bundle is {0:.1f} GB. Build its package on the server with '
                          '"manage.py build_release {1}".'.format(total / 1024 ** 3, release.pk))
    return build_package(release, request=request)


def package_file(release):
    """The package path if it exists and is inside the releases folder."""
    path = release.package_path or ''
    if not path:
        return None
    root = os.path.realpath(release_root())
    real = os.path.realpath(path)
    if not real.startswith(root + os.sep) or not os.path.isfile(real):
        return None
    return real


def package_intact(release, path):
    """True if the file on disk matches the size and SHA-256 recorded when ELSA built it."""
    if not release.package_sha256:
        return False
    try:
        if os.path.getsize(path) != release.package_size:
            return False
    except OSError:
        return False
    return _sha256(path) == release.package_sha256


def set_doi(request, release, doi, status):
    _require_release_rights(request, release)
    doi = (doi or '').strip()
    for prefix in ('https://doi.org/', 'http://doi.org/', 'doi:'):
        if doi.lower().startswith(prefix):
            doi = doi[len(prefix):]
    if status not in (Release.DOI_RESERVED, Release.DOI_REGISTERED):
        raise ActionError('Choose whether the DOI is reserved or registered.')
    if not DOI_PATTERN.match(doi):
        raise ActionError('That does not look like a DOI. It starts with "10." and has a "/", '
                          'for example 10.17189/1234567.')
    before = [release.doi, release.doi_status]
    Release.objects.filter(pk=release.pk).update(doi=doi, doi_status=status)
    audit.record(request, 'release.doi', release.bundle, changes={'doi': [before, [doi, status]]})


def record_delivery(request, release, note):
    _require_release_rights(request, release)
    if not release.package_built_at:
        raise ActionError('Build the delivery package first.')
    note = (note or '').strip()[:2000]
    if not note:
        raise ActionError('Say how and where it was delivered, for the record.')
    _storable(note)
    Release.objects.filter(pk=release.pk).update(delivered_at=timezone.now(), delivery_note=note)
    audit.record(request, 'release.delivered', release.bundle, reason=note, changes={'release': release.pk})


def set_registry(request, release, status):
    _require_release_rights(request, release)
    if status not in dict(Release.REGISTRY_CHOICES) or not status:
        raise ActionError('Choose a registry status.')
    if not release.delivered_at:
        raise ActionError('Record the delivery first.')
    Release.objects.filter(pk=release.pk).update(registry_status=status)
    audit.record(request, 'release.registry', release.bundle, changes={'registry': status})


def mark_released(request, release, note, announce=False):
    """Last step: tell the owner the DOI and close the review as Released."""
    from . import actions
    _require_release_rights(request, release)
    # Check this first so a refused announcement doesn't leave a half-done release.
    if announce and not roles.has(request.user, roles.MANAGE_ACCOUNTS):
        raise ActionError('Only an account admin can post a site-wide announcement. Release it without one, '
                          'and ask an account admin to announce it.')
    if release.released_at:
        raise ActionError('This release is already finished.')
    if not release.delivered_at:
        raise ActionError('Record the delivery first.')
    if release.doi_status != Release.DOI_REGISTERED:
        raise ActionError('Register the DOI first, so the owner can cite the data.')
    note = (note or '').strip()[:4000]
    _storable(note)
    message = note
    message = (message + '\n\n' if message else '') + 'Cite it as https://doi.org/{0}'.format(release.doi)
    case = release.case
    if case is not None and case.stage != Case.STAGE_RELEASED:
        actions.set_stage(request, case, Case.STAGE_RELEASED, message, accept_anyway=True)
    Release.objects.filter(pk=release.pk).update(released_at=timezone.now(), release_note=note)
    audit.record(request, 'release.released', release.bundle, reason=note,
                 changes={'release': release.pk, 'doi': release.doi})
    if announce:
        # Same permission check and audit entry as the Announcements page.
        now = timezone.now()
        actions.post_announcement(request, 'New in the PDS archive: {0}'.format(release.bundle.name),
                                  'Cite it as https://doi.org/{0}'.format(release.doi), Announcement.INFO,
                                  now, now + timedelta(days=7))
