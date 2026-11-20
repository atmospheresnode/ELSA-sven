# -*- coding: utf-8 -*-
"""Bring a bundle's labels up to the newest label migration (build/label_migrations).

    upgrade_bundle(bundle)       apply what's pending, returns the BundleUpgrade rows
    preview_bundle(bundle)       what it would change, on a copy, without writing

Before each migration the bundle's labels and inventory tables are copied to a snapshot.
If the migration raises or leaves a label that won't parse, the copy is put back and the
bundle stays at its old revision. Snapshots are kept so staff can undo a change.

Submitted bundles are skipped unless asked for, since someone may be reviewing them.
"""
import datetime
import difflib
import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile
import time

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.utils import timezone
from lxml import etree

from build import label_migrations

SOURCE_PAGE = 'page'
SOURCE_CHECK = 'check'
SOURCE_SUBMIT = 'submit'
SOURCE_SWEEP = 'sweep'
SOURCE_STAFF = 'staff'

# Don't retry a failed migration from page views more often than this.
PAGE_RETRY_HOURS = 6


class UpgradeBusy(RuntimeError):
    """Another process is upgrading this bundle."""


def upgrade_root():
    return getattr(settings, 'LABEL_UPGRADE_DIR', os.path.join(settings.BASE_DIR, 'label_upgrades'))


def snapshot_dir_for(bundle_pk):
    return os.path.join(upgrade_root(), str(bundle_pk))


def keep_days():
    return getattr(settings, 'LABEL_UPGRADE_KEEP_DAYS', 90)


# -- which files a migration may touch --

def _tracked(relative_path):
    """Labels and inventory tables. Everything else is the user's data and is left alone."""
    name = os.path.basename(relative_path).lower()
    return name.endswith('.xml') or (name.startswith('collection_') and name.endswith('.csv'))


def _tracked_files(directory):
    found = {}
    if not os.path.isdir(directory):
        return found
    for dirpath, dirnames, filenames in os.walk(directory):
        dirnames.sort()
        for filename in sorted(filenames):
            path = os.path.join(dirpath, filename)
            relative = os.path.relpath(path, directory)
            if not _tracked(relative) or os.path.islink(path):
                continue
            with open(path, 'rb') as handle:
                digest = hashlib.sha256(handle.read()).hexdigest()
            stat = os.stat(path)
            found[relative] = {'sha256': digest, 'mtime_ns': stat.st_mtime_ns}
    return found


def _parses(path):
    try:
        etree.parse(path)
        return True
    except (etree.XMLSyntaxError, OSError):
        return False


# -- snapshots --

def _take_snapshot(bundle, migration, before):
    directory = bundle.directory()
    stamp = timezone.now().strftime('%Y%m%d%H%M%S%f')
    target = os.path.join(snapshot_dir_for(bundle.pk), '{}_{:04d}'.format(stamp, migration.number))
    os.makedirs(target)
    for relative in before:
        destination = os.path.join(target, 'files', relative)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.copy2(os.path.join(directory, relative), destination)
    with open(os.path.join(target, 'manifest.json'), 'w', encoding='utf-8') as handle:
        json.dump({'bundle': bundle.pk, 'directory': directory, 'migration': migration.number,
                   'files': before}, handle)
    return target


def _restore_snapshot(snapshot, directory):
    """Put the snapshot's files back, and remove tracked files made since."""
    with open(os.path.join(snapshot, 'manifest.json'), encoding='utf-8') as handle:
        manifest = json.load(handle)
    saved = manifest['files']
    for relative in _tracked_files(directory):
        if relative not in saved:
            os.remove(os.path.join(directory, relative))
    for relative, stats in saved.items():
        destination = os.path.join(directory, relative)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.copy2(os.path.join(snapshot, 'files', relative), destination)
        os.utime(destination, ns=(stats['mtime_ns'], stats['mtime_ns']))


def prune_snapshots(now=None):
    """Delete snapshots older than LABEL_UPGRADE_KEEP_DAYS. Returns how many."""
    from build.models import BundleUpgrade

    cutoff = (now or timezone.now()) - datetime.timedelta(days=keep_days())
    removed = 0
    for row in BundleUpgrade.objects.filter(applied_at__lt=cutoff).exclude(snapshot=''):
        shutil.rmtree(row.snapshot, ignore_errors=True)
        row.snapshot = ''
        row.save(update_fields=['snapshot'])
        removed += 1
    return removed


# -- locking --

class _BundleLock(object):
    def __init__(self, bundle_pk, wait_seconds):
        self.path = os.path.join(snapshot_dir_for(bundle_pk), '.lock')
        self.wait_seconds = wait_seconds
        self.handle = None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.handle = open(self.path, 'w')
        deadline = time.monotonic() + self.wait_seconds
        while True:
            try:
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    self.handle.close()
                    raise UpgradeBusy('Bundle {} is being upgraded'.format(self.path))
                time.sleep(0.2)

    def __exit__(self, *exc):
        fcntl.flock(self.handle, fcntl.LOCK_UN)
        self.handle.close()


# -- the upgrade --

def needs_upgrade(bundle, include_submitted=False):
    if not include_submitted and bundle.submitted_at is not None:
        return False
    return (bundle.label_revision or 0) < label_migrations.head()


def _recently_failed(bundle, migration):
    from build.models import BundleUpgrade
    since = timezone.now() - datetime.timedelta(hours=PAGE_RETRY_HOURS)
    return BundleUpgrade.objects.filter(
        bundle=bundle, number=migration.number, status=BundleUpgrade.STATUS_FAILED,
        applied_at__gte=since).exists()


def _set_revision(bundle, number):
    # update(), not save(), so updated_at doesn't move. This isn't the user's edit.
    from build.models import Bundle
    Bundle.objects.filter(pk=bundle.pk).update(label_revision=number)
    bundle.label_revision = number


def upgrade_bundle(bundle, source=SOURCE_PAGE, include_submitted=False, wait_seconds=0):
    """Apply this bundle's pending label migrations. Returns the BundleUpgrade rows made.

    Raises UpgradeBusy only if wait_seconds runs out. Never raises for a failed migration:
    that's recorded on a row, staff get an email, and the bundle stays where it was.
    """
    from build.models import Bundle

    if not needs_upgrade(bundle, include_submitted):
        return []

    rows = []
    with _BundleLock(bundle.pk, wait_seconds):
        # Someone else may have finished it while this waited.
        bundle.label_revision = Bundle.objects.filter(pk=bundle.pk).values_list(
            'label_revision', flat=True).first() or 0
        for migration in label_migrations.pending(bundle):
            if not migration.applies_to(bundle):
                _set_revision(bundle, migration.number)
                continue
            if source == SOURCE_PAGE and _recently_failed(bundle, migration):
                break
            row, ok = _apply_one(bundle, migration, source)
            if row is not None:
                rows.append(row)
            if not ok:
                break
            _set_revision(bundle, migration.number)
    _tell_owner(bundle, rows, source)
    return rows


def _tell_owner(bundle, rows, source):
    """A bell notice when this happened while the owner wasn't looking at the bundle."""
    from build.models import BundleUpgrade
    from staff import hooks

    applied = [row for row in rows if row.status == BundleUpgrade.STATUS_APPLIED]
    if not applied or source in (SOURCE_PAGE, SOURCE_SUBMIT):
        return
    hooks.tell_owner_labels_updated(
        bundle, 'ELSA updated {} in {}'.format(files_phrase(touched(applied)), bundle.name),
        'They were written by an older version of ELSA. Open the bundle to see what changed.',
        reverse_bundle(bundle) + '#label_updates')


def _apply_one(bundle, migration, source):
    """Run one migration. Returns (BundleUpgrade or None, succeeded)."""
    from build.models import BundleUpgrade

    directory = bundle.directory()
    if not os.path.isdir(directory):
        # Nothing on disk to fix. A bundle that failed to create, usually.
        return None, True

    before = _tracked_files(directory)
    parsed_before = {relative for relative in before
                     if not relative.lower().endswith('.xml')
                     or _parses(os.path.join(directory, relative))}
    snapshot = _take_snapshot(bundle, migration, before)

    try:
        with transaction.atomic():
            notes = migration.apply(bundle)
            broken = [relative for relative in _tracked_files(directory)
                      if relative.lower().endswith('.xml')
                      and (relative in parsed_before or relative not in before)
                      and not _parses(os.path.join(directory, relative))]
            if broken:
                raise RuntimeError('left labels that do not parse: {}'.format(', '.join(broken)))
    except Exception as error:                           # noqa: BLE001 - recorded on the row
        _restore_snapshot(snapshot, directory)
        shutil.rmtree(snapshot, ignore_errors=True)
        first_failure = not BundleUpgrade.objects.filter(
            bundle=bundle, number=migration.number, status=BundleUpgrade.STATUS_FAILED).exists()
        row = BundleUpgrade.objects.create(
            bundle=bundle, number=migration.number, name=migration.name, title=migration.title,
            status=BundleUpgrade.STATUS_FAILED, changes=[], files=[], source=source,
            # ASCII only, since the column may be latin1 (see the ELSA charset notes).
            error='{}: {}'.format(type(error).__name__, error).encode(
                'ascii', 'backslashreplace').decode('ascii')[:4000],
            seen_at=timezone.now())
        if first_failure:
            _tell_staff(row)
        return row, False

    after = _tracked_files(directory)
    changed = sorted(relative for relative in after
                     if relative not in before or after[relative]['sha256'] != before[relative]['sha256'])
    removed = sorted(relative for relative in before if relative not in after)

    # A rewrite with the same content shouldn't make the validation result look out of date.
    for relative, stats in after.items():
        if relative in before and stats['sha256'] == before[relative]['sha256'] \
                and stats['mtime_ns'] != before[relative]['mtime_ns']:
            original = before[relative]['mtime_ns']
            os.utime(os.path.join(directory, relative), ns=(original, original))

    if not changed and not removed:
        shutil.rmtree(snapshot, ignore_errors=True)
        return None, True

    row = BundleUpgrade.objects.create(
        bundle=bundle, number=migration.number, name=migration.name, title=migration.title,
        status=BundleUpgrade.STATUS_APPLIED, changes=[str(note) for note in notes],
        files=changed + ['{} (removed)'.format(relative) for relative in removed],
        source=source, snapshot=snapshot)
    return row, True


def undo(row):
    """Put a bundle's files back to how they were before this upgrade.

    Only the newest applied upgrade of a bundle can be undone, so a later one isn't lost.
    The revision stays, so it won't run again by itself.
    """
    from build.models import BundleUpgrade

    if row.status != BundleUpgrade.STATUS_APPLIED:
        raise ValueError('Only an applied change can be undone.')
    newest = BundleUpgrade.objects.filter(
        bundle=row.bundle, status=BundleUpgrade.STATUS_APPLIED).order_by('-applied_at', '-pk').first()
    if newest is None or newest.pk != row.pk:
        raise ValueError('Undo the newer change on this bundle first.')
    if not row.snapshot or not os.path.isdir(row.snapshot):
        raise ValueError('The copy from before this change is no longer kept.')

    with _BundleLock(row.bundle_id, 30):
        _restore_snapshot(row.snapshot, row.bundle.directory())
    row.status = BundleUpgrade.STATUS_RESTORED
    row.save(update_fields=['status'])
    return row


def _tell_staff(row):
    recipients = getattr(settings, 'LABEL_UPGRADE_RECIPIENTS', ['rupakdey@nmsu.edu'])
    if not recipients:
        return
    bundle = row.bundle
    body = (
        'ELSA could not apply label migration {:04d}_{} to bundle "{}" (#{}, owner {}).\n\n'
        '{}\n\n'
        'The bundle was put back as it was and is still on revision {}. Page views won\'t retry '
        'for {} hours; `manage.py upgrade_bundles --bundle {} --apply` retries now.\n'
    ).format(row.number, row.name, bundle.name, bundle.pk, bundle.user.username, row.error,
             bundle.label_revision, PAGE_RETRY_HOURS, bundle.pk)
    try:
        EmailMessage(subject='ELSA label upgrade failed: {}'.format(bundle.name), body=body,
                     from_email='atm-elsa@nmsu.edu', to=list(recipients)).send()
    except Exception as error:                           # noqa: BLE001 - the row has it
        print('label upgrade: could not email staff: {}'.format(error))


# -- what the owner sees --

def unseen_for(bundle):
    from build.models import BundleUpgrade
    return BundleUpgrade.objects.filter(
        bundle=bundle, status=BundleUpgrade.STATUS_APPLIED, seen_at__isnull=True)


def history_for(bundle):
    from build.models import BundleUpgrade
    return BundleUpgrade.objects.filter(bundle=bundle).exclude(status=BundleUpgrade.STATUS_FAILED)


def mark_seen(bundle):
    """The owner has seen the changes. Clears the bell notices about them too."""
    from staff import hooks
    hooks.mark_label_notices_read(bundle, reverse_bundle(bundle) + '#label_updates')
    return unseen_for(bundle).update(seen_at=timezone.now())


def reverse_bundle(bundle):
    from django.urls import reverse
    return reverse('build:bundle', kwargs={'pk_bundle': bundle.pk})


def failed_update(bundle):
    """The failed attempt that is holding this bundle back, or None.

    It stops counting once that migration goes through, since the revision moves past it.
    """
    from build.models import BundleUpgrade
    revision = bundle.label_revision or 0
    if revision >= label_migrations.head():
        return None
    return BundleUpgrade.objects.filter(
        bundle=bundle, status=BundleUpgrade.STATUS_FAILED, number__gt=revision).first()


def failed_bundles(queryset=None):
    """Bundles with a failed label update that hasn't gone through since."""
    from django.db.models import Exists, OuterRef

    from build.models import Bundle, BundleUpgrade
    failed = BundleUpgrade.objects.filter(bundle=OuterRef('pk'), status=BundleUpgrade.STATUS_FAILED,
                                          number__gt=OuterRef('label_revision'))
    return (queryset if queryset is not None else Bundle.objects.all()).filter(Exists(failed))


# Migration notes in words an owner can follow. None hides a note the title already says.
PLAIN_NOTES = {
    'AMA investigation reference': 'Pointed the AMA investigation at its correct PDS identifier',
    'AMA investigation added': 'Added the AMA investigation',
    'blank ROR ID': 'Removed a blank ROR ID from a citation organization',
    'blank parent organization': 'Removed a blank parent organization from the citation',
    'nil time coordinates added': 'Marked the start and stop times of a model collection as not applicable',
    'nan value': 'Removed "nan" values from variable descriptions',
    'placeholder comment': 'Removed a comment left over from the label template',
    'title': 'Fixed the capitalization of a title',
    'inventory': "Rewrote a collection's inventory table so it lists the files that are there",
    'citation copied into product labels': 'Copied the citation into the product labels so they match the bundle',
    'targets copied into product labels': 'Copied the targets into the product labels so they match the bundle',
    'bundle member entry: listed more than once': 'Removed a collection listed twice in the bundle label',
    'bundle member entry: no such collection': 'Removed a collection from the bundle label that is no longer there',
    'data product label rebuilt': None,
    'citation': None,
}

# Element names, like Time_Coordinates: an empty one was removed.
ELEMENT = re.compile(r'^[A-Z][A-Za-z0-9]*(_[A-Za-z0-9]+)*$')


def describe(notes):
    """A migration's notes as sentences, repeats counted, e.g. ['Removed ... (2)']."""
    empty, counts = [], {}
    for note in notes or []:
        note = str(note)
        if ELEMENT.match(note) and note not in PLAIN_NOTES:
            if note not in empty:
                empty.append(note)
            continue
        if note.startswith('citation copied into'):
            note = 'citation copied into product labels'
        plain = PLAIN_NOTES.get(note, note)
        if plain:
            counts[plain] = counts.get(plain, 0) + 1
    lines = ['Removed empty fields PDS4 does not allow: {}'.format(', '.join(empty))] if empty else []
    lines.extend('{} ({})'.format(text, count) if count > 1 else text for text, count in counts.items())
    return lines


def _is_label(path):
    return path.lower().replace(' (removed)', '').endswith('.xml')


def files_phrase(paths):
    """'2 labels and 1 inventory table', for the files a change touched."""
    paths = list(paths)
    labels = sum(1 for path in paths if _is_label(path))
    tables = len(paths) - labels
    parts = []
    if labels:
        parts.append('{} label{}'.format(labels, '' if labels == 1 else 's'))
    if tables:
        parts.append('{} inventory table{}'.format(tables, '' if tables == 1 else 's'))
    return ' and '.join(parts) or 'nothing'


def touched(rows):
    """Every file these changes touched, once."""
    seen = []
    for row in rows:
        for path in row.files:
            if path not in seen:
                seen.append(path)
    return seen


def safe_upgrade(bundle, source=SOURCE_PAGE, include_submitted=False, wait_seconds=0):
    """upgrade_bundle for callers that must not fail because of it, like page views."""
    try:
        return upgrade_bundle(bundle, source=source, include_submitted=include_submitted,
                              wait_seconds=wait_seconds)
    except UpgradeBusy:
        return []
    except Exception as error:                           # noqa: BLE001 - see docstring
        print('label upgrade: bundle {}: {}: {}'.format(bundle.pk, type(error).__name__, error))
        return []


# -- preview --

def _mirror(source, target):
    """Copy a bundle folder: labels and tables copied, data files linked."""
    for dirpath, dirnames, filenames in os.walk(source):
        relative_dir = os.path.relpath(dirpath, source)
        os.makedirs(os.path.join(target, relative_dir), exist_ok=True)
        for filename in filenames:
            path = os.path.join(dirpath, filename)
            destination = os.path.join(target, relative_dir, filename)
            if _tracked(os.path.join(relative_dir, filename)):
                shutil.copy2(path, destination)
            else:
                os.symlink(os.path.realpath(path), destination)


def preview_bundle(bundle):
    """What the pending migrations would change, as {'number', 'title', 'notes', 'diff'} dicts.

    Runs on a copy of the bundle folder inside a transaction that's rolled back.
    """
    from django.test.utils import override_settings

    from build.models import Bundle

    work = tempfile.mkdtemp(prefix='label-preview-')
    results = []
    try:
        relative = os.path.relpath(bundle.directory(), settings.ARCHIVE_DIR)
        copy_root = os.path.join(work, 'archive')
        copy = os.path.join(copy_root, relative)
        if os.path.isdir(bundle.directory()):
            _mirror(bundle.directory(), copy)
        with override_settings(ARCHIVE_DIR=copy_root, LABEL_UPGRADE_DIR=os.path.join(work, 'snapshots')):
            with transaction.atomic():
                working = Bundle.objects.get(pk=bundle.pk)
                for migration in label_migrations.pending(working):
                    if not migration.applies_to(working) or not os.path.isdir(copy):
                        continue
                    before = {path: open(os.path.join(copy, path), encoding='utf-8', errors='replace').read()
                              for path in _tracked_files(copy)}
                    try:
                        notes = migration.apply(working)
                    except Exception as error:           # noqa: BLE001 - reported
                        results.append({'number': migration.number, 'title': migration.title,
                                        'notes': [], 'diff': '', 'error': str(error)})
                        break
                    after = {path: open(os.path.join(copy, path), encoding='utf-8', errors='replace').read()
                             for path in _tracked_files(copy)}
                    diff = []
                    for path in sorted(set(before) | set(after)):
                        if before.get(path) != after.get(path):
                            diff.extend(difflib.unified_diff(
                                (before.get(path) or '').splitlines(True),
                                (after.get(path) or '').splitlines(True),
                                fromfile='a/' + path, tofile='b/' + path))
                    results.append({'number': migration.number, 'title': migration.title,
                                    'notes': notes, 'diff': ''.join(diff), 'error': ''})
                transaction.set_rollback(True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return results
