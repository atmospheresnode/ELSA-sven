"""Resumable NetCDF uploads, using the tus protocol (https://tus.io/protocols/resumable-upload).

A NetCDF file goes up in pieces instead of one huge POST, so a dropped connection costs one
piece, not the whole file, and every request stays far below Apache's body limit.

We implement tus 1.0.0 with the creation, termination, checksum and expiration extensions, so
any tus client works against these URLs. Two additions of ours:

- A piece may be sent with Content-Encoding: gzip. We unpack it and store the original bytes.
  The unpacked size is capped at what the piece is allowed to hold, so a gzip bomb stops after
  one extra byte.
- Upload-Checksum is checked against the unpacked bytes, the bytes of the user's file.

Once every byte is in, the page calls finish(), which turns the upload into a NetCDFFile and runs
the same label harvest as the form upload.

Each upload is a row (NetCDFUpload) plus a staging file named after its id. The row's offset is the
truth. A piece is written, flushed to disk, and only then is the offset moved, so a crash can lose
a piece but never claim bytes we don't have.
"""
import base64
import binascii
import contextlib
import errno
import fcntl
import hashlib
import io
import logging
import os
import re
import shutil
import stat
import time
import unicodedata
import zlib
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation
from django.db import transaction
from django.db.models import F, Sum
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.utils.http import http_date

from build.chocolate import is_netcdf_file

logger = logging.getLogger(__name__)

TUS_VERSION = '1.0.0'
TUS_EXTENSIONS = 'creation,termination,checksum,expiration'
CHECKSUM_ALGORITHMS = {'sha256': hashlib.sha256, 'sha1': hashlib.sha1, 'md5': hashlib.md5}
OFFSET_CONTENT_TYPE = 'application/offset+octet-stream'

GIB = 1024 ** 3
MIB = 1024 ** 2

# Smallest file we take. Every NetCDF flavour has an 8 byte signature.
MIN_FILE_BYTES = 8
READ_BLOCK = 1 * MIB
MAX_METADATA_CHARS = 4096
MAX_FILENAME_CHARS = 200


def max_file_bytes():
    return getattr(settings, 'NETCDF_UPLOAD_MAX_BYTES', 40 * GIB)


def max_patch_bytes():
    """Most one request may carry, before or after unpacking. Room for other tus clients."""
    return getattr(settings, 'NETCDF_UPLOAD_MAX_PATCH_BYTES', 128 * MIB)


def expiry():
    """How long an unfinished upload is kept after its last piece."""
    return timedelta(days=getattr(settings, 'NETCDF_UPLOAD_EXPIRY_DAYS', 7))


def max_active_per_user():
    return getattr(settings, 'NETCDF_UPLOAD_MAX_ACTIVE_PER_USER', 20)


def max_staged_bytes_per_user():
    return getattr(settings, 'NETCDF_UPLOAD_MAX_STAGED_BYTES_PER_USER', 200 * GIB)


def create_limit_per_hour():
    return getattr(settings, 'NETCDF_UPLOAD_CREATE_LIMIT_PER_HOUR', 120)


def max_parallel_per_user():
    """Pieces one user may send at once. mod_wsgi has few threads, and they're shared by everyone."""
    return getattr(settings, 'NETCDF_UPLOAD_MAX_PARALLEL_PER_USER', 3)


def piece_seconds():
    """Longest one piece may take. 50 MB needs about 20 seconds at 20 Mbit/s."""
    return getattr(settings, 'NETCDF_UPLOAD_PIECE_SECONDS', 15 * 60)


class UploadError(Exception):
    """A refusal with a status code and a message the page can show."""

    def __init__(self, status, message, headers=None, reason=None, extra=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.headers = headers or {}
        self.reason = reason
        # More fields for the JSON reply, e.g. what the storage dialog shows.
        self.extra = extra or {}


# ---- staging area -----------------------------------------------------------------------------

def staging_dir():
    """Where pieces collect. Outside every web-served folder, and only ELSA can read it.

    Lives next to MEDIA_ROOT by default so finishing is a rename, not a copy of gigabytes.
    """
    path = getattr(settings, 'NETCDF_UPLOAD_STAGING_DIR', None) or os.path.join(
        os.path.dirname(os.path.abspath(settings.MEDIA_ROOT)), 'netcdf_staging')
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        logger.error('NetCDF upload area %s cannot be created: %s', path, exc)
        raise UploadError(500, 'The upload area is not set up on the server. Please contact the ELSA team.')
    if not os.access(path, os.W_OK | os.X_OK):
        logger.error('NetCDF upload area %s is not writable', path)
        raise UploadError(500, 'The upload area is not set up on the server. Please contact the ELSA team.')
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise UploadError(500, 'The upload area is misconfigured. Please contact the ELSA team.')
    return path


def staging_path(upload_id):
    # The name comes from the UUID alone, never from anything the user sent.
    return os.path.join(staging_dir(), '{}.part'.format(upload_id.hex))


def _open_staged(path, create=False):
    flags = os.O_RDWR | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0)
    if create:
        flags |= os.O_CREAT | os.O_EXCL
    return os.open(path, flags, 0o600)


@contextlib.contextmanager
def locked(upload):
    """The staging file, open and locked, so two requests never write one upload at once."""
    try:
        fd = _open_staged(upload.staging_path())
    except FileNotFoundError:
        raise UploadError(404, 'This upload is no longer on the server. Please start it again.')
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            # Someone swapped the staging file for a symlink. Never follow it.
            logger.error('NetCDF upload %s: staging file is a symlink, refusing', upload.id)
            raise UploadError(500, 'This upload cannot continue. Please contact the ELSA team.')
        raise
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise UploadError(423, 'This upload is busy with another request. Try again shortly.')
        yield fd
    finally:
        os.close(fd)


@contextlib.contextmanager
def user_slot(user):
    """One of the user's few sending slots, or 429. Lock files, so it holds across processes
    and frees itself when a request dies."""
    directory = staging_dir()
    for slot in range(max_parallel_per_user()):
        path = os.path.join(directory, 'user-{}-{}.lock'.format(int(user.pk), slot))
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0), 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            continue
        try:
            yield
        finally:
            os.close(fd)
        return
    raise UploadError(429, 'Too many pieces are on their way at once. Try again shortly.',
                      headers={'Retry-After': '5'})


# ---- strict header parsing ----------------------------------------------------------------------

_DIGITS = re.compile(r'\A[0-9]{1,20}\Z')
_META_KEY = re.compile(r'\A[A-Za-z0-9_.-]{1,64}\Z')


def parse_int_header(request, name, required=True):
    raw = request.headers.get(name)
    if raw is None:
        if required:
            raise UploadError(400, 'Missing {} header.'.format(name))
        return None
    raw = raw.strip()
    if not _DIGITS.match(raw):
        raise UploadError(400, 'Bad {} header.'.format(name))
    return int(raw)


def parse_metadata(raw):
    """Upload-Metadata: comma separated 'key base64value' pairs, the value optional."""
    if not raw:
        return {}
    if len(raw) > MAX_METADATA_CHARS:
        raise UploadError(400, 'Upload-Metadata is too long.')
    result = {}
    for pair in raw.split(','):
        parts = pair.strip().split(' ')
        if not parts[0] or len(parts) > 2 or not _META_KEY.match(parts[0]):
            raise UploadError(400, 'Bad Upload-Metadata header.')
        key = parts[0]
        if key in result:
            raise UploadError(400, 'Upload-Metadata repeats {}.'.format(key))
        value = ''
        if len(parts) == 2:
            try:
                value = base64.b64decode(parts[1], validate=True).decode('utf-8')
            except (binascii.Error, ValueError):
                raise UploadError(400, 'Bad Upload-Metadata value for {}.'.format(key))
        result[key] = value
    return result


def parse_checksum(request):
    """(algorithm name, expected digest) from Upload-Checksum, or None."""
    raw = request.headers.get('Upload-Checksum')
    if raw is None:
        return None
    parts = raw.strip().split(' ')
    if len(parts) != 2 or parts[0] not in CHECKSUM_ALGORITHMS:
        raise UploadError(400, 'Unsupported Upload-Checksum.')
    try:
        expected = base64.b64decode(parts[1], validate=True)
    except (binascii.Error, ValueError):
        raise UploadError(400, 'Bad Upload-Checksum value.')
    if len(expected) != CHECKSUM_ALGORITHMS[parts[0]]().digest_size:
        raise UploadError(400, 'Bad Upload-Checksum value.')
    return parts[0], expected


def clean_filename(raw):
    """(display name, name on disk) for what the user picked, or UploadError.

    Strips any folder part. Refuses control characters, and anything the database can't store:
    its tables are latin1, and it's far better to say so now than after the bytes are up.
    """
    from build.models import NetCDFFile
    name = unicodedata.normalize('NFC', (raw or '').replace('\\', '/')).split('/')[-1].strip()
    if not name or name in ('.', '..'):
        raise UploadError(400, 'The file needs a name.')
    if len(name) > MAX_FILENAME_CHARS:
        raise UploadError(400, 'That file name is too long. Please keep it under {} characters.'.format(
            MAX_FILENAME_CHARS))
    if any(unicodedata.category(ch).startswith('C') for ch in name):
        raise UploadError(400, 'That file name has characters ELSA cannot use. Please rename the file.')
    try:
        name.encode('latin-1')
    except UnicodeEncodeError:
        raise UploadError(400, '"{}" has characters ELSA cannot store yet. Please rename the file '
                               'using plain letters, digits, spaces, dots, dashes or underscores.'.format(name))
    storage = NetCDFFile._meta.get_field('file').storage
    try:
        stored = storage.get_valid_name(name)
    except SuspiciousFileOperation:
        raise UploadError(400, 'That file name cannot be used. Please rename the file.')
    if not stored or stored.startswith('.') or stored in ('.', '..'):
        raise UploadError(400, 'That file name cannot be used. Please rename the file.')
    return name, stored


def _label_name(stored_name):
    stem = stored_name[:-3] if stored_name.endswith('.nc') else stored_name
    return stem + '.xml'


def name_conflict(collection, filename, stored_name, exclude_upload=None, include_pending=True,
                  short=False):
    """Why this name can't go into the collection, or None.

    Checks other NetCDF files, uploads still on their way, and what is already in the folder.
    A file named like an existing label or inventory would otherwise overwrite it.
    short gives the one-line reason shown next to a file in the upload list.
    """
    from build.models import NetCDFFile, NetCDFUpload
    storage = NetCDFFile._meta.get_field('file').storage
    for name in NetCDFFile.objects.filter(collection=collection).values_list('file', flat=True):
        if name and storage.get_valid_name(os.path.basename(name)) == stored_name:
            if short:
                return 'Already in this collection. Delete the old one first, or rename this file.'
            return ('A file named "{}" is already in the "{}" collection. Delete it first if you want '
                    'to replace it, or rename the file, since two files cannot share a name inside one '
                    'collection.'.format(filename, collection.collection_name))
    pending = NetCDFUpload.objects.filter(
        collection=collection, stored_name=stored_name, finished_at__isnull=True,
        expires_at__gt=timezone.now())
    if exclude_upload is not None:
        pending = pending.exclude(pk=exclude_upload.pk)
    if include_pending and pending.exists():
        if short:
            return 'Already being uploaded.'
        return ('A file named "{}" is already being uploaded to the "{}" collection.'.format(
            filename, collection.collection_name))
    directory = collection.directory()
    for candidate in (stored_name, _label_name(stored_name)):
        if os.path.lexists(os.path.join(directory, candidate)):
            if short:
                return 'Would overwrite "{}" in this collection. Please rename the file.'.format(candidate)
            return ('The "{}" collection already has a file called "{}", so "{}" would overwrite it. '
                    'Please rename the file.'.format(collection.collection_name, candidate, filename))
    return None


# ---- responses -----------------------------------------------------------------------------------

def tus_response(status=204, headers=None, payload=None, reason=None):
    if payload is not None:
        response = JsonResponse(payload, status=status)
    else:
        response = HttpResponse(status=status)
    if reason:
        response.reason_phrase = reason
    response['Tus-Resumable'] = TUS_VERSION
    response['Cache-Control'] = 'no-store'
    for key, value in (headers or {}).items():
        response[key] = value
    return response


def error_response(exc):
    headers = dict(exc.headers)
    if exc.status == 412:
        headers['Tus-Version'] = TUS_VERSION
    payload = dict(exc.extra)
    payload['error'] = exc.message
    return tus_response(exc.status, headers=headers, payload=payload, reason=exc.reason)


def options_response():
    return tus_response(204, headers={
        'Tus-Version': TUS_VERSION,
        'Tus-Extension': TUS_EXTENSIONS,
        'Tus-Max-Size': str(max_file_bytes()),
        'Tus-Checksum-Algorithm': ','.join(CHECKSUM_ALGORITHMS),
    })


def check_tus_version(request):
    if request.method == 'OPTIONS':
        return
    if request.headers.get('Tus-Resumable') != TUS_VERSION:
        raise UploadError(412, 'Unsupported tus version.')


def _expires_header(upload):
    return {'Upload-Expires': http_date(upload.expires_at.timestamp())}


# ---- housekeeping --------------------------------------------------------------------------------

def purge_expired(limit=None):
    """Delete unfinished uploads past their expiry, files included. Returns how many."""
    from build.models import NetCDFUpload
    stale = NetCDFUpload.objects.filter(finished_at__isnull=True, expires_at__lte=timezone.now())
    ids = list(stale.values_list('pk', flat=True)[:limit] if limit else stale.values_list('pk', flat=True))
    removed = 0
    for upload in NetCDFUpload.objects.filter(pk__in=ids):
        # One at a time, so each post_delete removes its own staging file.
        upload.delete()
        removed += 1
    return removed


def purge_orphans(min_age=timedelta(days=1)):
    """Staging files with no row behind them, from a crash between create and save. Returns how many."""
    from build.models import NetCDFUpload
    directory = staging_dir()
    known = {upload_id.hex for upload_id in NetCDFUpload.objects.values_list('pk', flat=True)}
    cutoff = timezone.now().timestamp() - min_age.total_seconds()
    removed = 0
    for entry in os.scandir(directory):
        stem, dot, ext = entry.name.partition('.')
        if ext != 'part' or stem in known:
            continue
        try:
            if entry.is_file(follow_symlinks=False) and entry.stat(follow_symlinks=False).st_mtime < cutoff:
                os.remove(entry.path)
                removed += 1
        except FileNotFoundError:
            pass
    return removed


# ---- the protocol --------------------------------------------------------------------------------

def _replace_own_unfinished(user, collection, filename, stored_name):
    """Starting a file again replaces the user's own unfinished upload of it, e.g. one whose
    cancel never reached us, or one left in another browser. Not one still receiving pieces."""
    from build.models import NetCDFUpload
    for old in NetCDFUpload.objects.filter(user=user, collection=collection, stored_name=stored_name,
                                           finished_at__isnull=True):
        try:
            with locked(old):
                old.delete()
        except UploadError as exc:
            if exc.status == 423:
                raise UploadError(409, '"{}" is still being uploaded in another window or tab. Let it '
                                       'finish there, or cancel it, before starting it again.'.format(filename))
            if exc.status != 404:
                raise
            old.delete()


def _busy(upload):
    """True while a request is writing this upload or finishing it."""
    try:
        with locked(upload):
            return False
    except UploadError as exc:
        return exc.status == 423


MAX_CHECK_FILES = 500


def check_files(user, bundle, collection_id, files):
    """For the page, right after files are picked: which of them create() would refuse, and why.

    Same rules as create(), without making anything. Bytes aren't sent here, so whether a file
    really is NetCDF is checked by the page from its first bytes, and again by the server.
    """
    from build import storage_report
    from build.models import AdditionalCollections, NetCDFUpload
    if not _DIGITS.match(str(collection_id or '')):
        raise UploadError(400, 'Could not tell which collection this upload belongs to.')
    collection = AdditionalCollections.objects.filter(bundle=bundle, pk=int(collection_id)).first()
    if collection is None:
        raise UploadError(400, 'Could not tell which collection this upload belongs to.')
    if not isinstance(files, list) or len(files) > MAX_CHECK_FILES:
        raise UploadError(400, 'Pick at most {} files at a time.'.format(MAX_CHECK_FILES))

    results = []
    seen = set()
    for item in files:
        item = item if isinstance(item, dict) else {}
        size = item.get('size')
        try:
            filename, stored_name = clean_filename(str(item.get('name') or '')[:1000])
            if isinstance(size, bool) or not isinstance(size, int) or size < MIN_FILE_BYTES:
                problem = 'Too small to be a NetCDF file.'
            elif size > max_file_bytes():
                problem = 'Larger than the {} limit for one file.'.format(
                    storage_report.human_size(max_file_bytes()))
            elif stored_name in seen:
                problem = 'Picked twice.'
            else:
                seen.add(stored_name)
                problem = None
                # Your own unfinished upload of it gets replaced, unless it's still going elsewhere.
                for old in NetCDFUpload.objects.filter(user=user, collection=collection,
                                                       stored_name=stored_name, finished_at__isnull=True):
                    if _busy(old):
                        problem = 'Still being uploaded in another window or tab.'
                problem = problem or name_conflict(collection, filename, stored_name,
                                                   include_pending=False, short=True)
        except UploadError as exc:
            problem = exc.message
        results.append({'problem': problem})
    return results


def create(request, bundle, location_for):
    """POST: start an upload. location_for(upload) gives its URL."""
    from build import storage_report
    from build.models import AdditionalCollections, NetCDFUpload

    if request.headers.get('Upload-Defer-Length') is not None:
        raise UploadError(400, 'The file size must be sent up front.')
    length = parse_int_header(request, 'Upload-Length')
    if length > max_file_bytes():
        raise UploadError(413, 'That file is {}. ELSA takes NetCDF files up to {} each.'.format(
            storage_report.human_size(length), storage_report.human_size(max_file_bytes())))
    if length < MIN_FILE_BYTES:
        raise UploadError(400, 'That file is too small to be a NetCDF file.')
    # creation-with-upload is not offered. Any other body is never read.
    content_type = (request.headers.get('Content-Type') or '').split(';')[0].strip().lower()
    if content_type == OFFSET_CONTENT_TYPE and parse_int_header(request, 'Content-Length', required=False):
        raise UploadError(400, 'Send the file with PATCH requests after creating the upload.')

    metadata = parse_metadata(request.headers.get('Upload-Metadata'))
    filename, stored_name = clean_filename(metadata.get('filename'))
    collection_id = metadata.get('collection', '')
    if not _DIGITS.match(collection_id):
        raise UploadError(400, 'Could not tell which collection this upload belongs to. '
                               'Please reload the page and try again.')
    collection = AdditionalCollections.objects.filter(bundle=bundle, pk=int(collection_id)).first()
    if collection is None:
        raise UploadError(400, 'Could not tell which collection this upload belongs to. '
                               'Please reload the page and try again.')

    if storage_report.rate_limited('netcdf-create', request.user, create_limit_per_hour()):
        raise UploadError(429, 'Too many uploads started in the last hour. Please wait a little.')

    purge_expired(limit=50)

    now = timezone.now()
    active = NetCDFUpload.objects.filter(
        user=request.user, finished_at__isnull=True, expires_at__gt=now)
    if active.count() >= max_active_per_user():
        raise UploadError(429, 'You have {} unfinished uploads. Let them finish, or cancel some, '
                               'before starting more.'.format(active.count()))
    staged = active.aggregate(total=Sum('length'))['total'] or 0
    if staged + length > max_staged_bytes_per_user():
        raise UploadError(413, 'Your unfinished uploads would add up to more than {}. Let some '
                               'finish first.'.format(storage_report.human_size(max_staged_bytes_per_user())))

    _replace_own_unfinished(request.user, collection, filename, stored_name)
    conflict = name_conflict(collection, filename, stored_name)
    if conflict:
        raise UploadError(409, conflict)

    # Bytes every unfinished upload still has to send land on the same disk, so count them too.
    outstanding = NetCDFUpload.objects.filter(
        finished_at__isnull=True, expires_at__gt=now).aggregate(
        rest=Sum(F('length') - F('offset')))['rest'] or 0
    directory = staging_dir()
    space = storage_report.check_space(
        length + outstanding, paths=[settings.ARCHIVE_DIR, settings.MEDIA_ROOT, directory])
    if not space['ok']:
        logger.warning('NetCDF upload refused for %s: needs %s bytes, %s usable',
                       request.user.username, space['needed'], space['available'])
        raise UploadError(507, 'The server does not have enough storage space for this upload right '
                               'now. Nothing was added to your bundle.', extra={
                              'code': 'insufficient_storage',
                              'needed': storage_report.human_size(space['needed']),
                              'available': storage_report.human_size(space['available']),
                              'available_bytes': space['available']})

    upload = NetCDFUpload(
        user=request.user, bundle=bundle, collection=collection, filename=filename,
        stored_name=stored_name, length=length, offset=0, updated_at=now, expires_at=now + expiry())
    fd = _open_staged(staging_path(upload.id), create=True)
    os.close(fd)
    try:
        upload.save(force_insert=True)
    except Exception:
        os.remove(staging_path(upload.id))
        raise
    logger.info('NetCDF upload %s started by %s: %s, %s bytes', upload.id, request.user.username,
                stored_name, length)
    headers = {'Location': location_for(upload), 'Upload-Offset': '0'}
    headers.update(_expires_header(upload))
    return tus_response(201, headers=headers)


def _gone_if_expired(upload):
    if upload.finished_at is None and upload.expires_at <= timezone.now():
        upload.delete()
        raise UploadError(410, 'This upload expired. Please start it again.')


def head(upload):
    _gone_if_expired(upload)
    headers = {'Upload-Offset': str(upload.offset), 'Upload-Length': str(upload.length)}
    if upload.finished_at is None:
        headers.update(_expires_header(upload))
    return tus_response(200, headers=headers)


def _content_coding(request):
    coding = (request.headers.get('Content-Encoding') or 'identity').strip().lower()
    if coding not in ('identity', 'gzip'):
        raise UploadError(415, 'Pieces may be sent plain or gzip compressed, nothing else.')
    return coding


class _PieceWriter(object):
    """Writes the unpacked bytes of one PATCH at the upload's offset, within a hard cap."""

    def __init__(self, fd, start, cap, hasher):
        self.fd = fd
        self.position = start
        self.written = 0
        self.cap = cap
        self.hasher = hasher

    def write(self, data):
        if not data:
            return
        if self.written + len(data) > self.cap:
            raise UploadError(413, 'This piece holds more data than the upload has room for.')
        if self.hasher is not None:
            self.hasher.update(data)
        view = memoryview(data)
        while view:
            sent = os.pwrite(self.fd, view, self.position)
            view = view[sent:]
            self.position += sent
        self.written += len(data)


def _read_body(request, content_length, coding, writer):
    """Stream the request body through the writer, unpacking gzip on the way."""
    inflater = zlib.decompressobj(wbits=31) if coding == 'gzip' else None
    remaining = content_length
    deadline = time.monotonic() + piece_seconds()
    while remaining:
        if time.monotonic() > deadline:
            raise UploadError(408, 'This piece took too long. It will be sent again in smaller pieces.')
        try:
            block = request.read(min(READ_BLOCK, remaining))
        except (IOError, OSError) as exc:
            logger.info('NetCDF upload piece cut off: %s', exc)
            raise UploadError(400, 'The connection dropped during this piece.')
        if not block:
            raise UploadError(400, 'The connection dropped during this piece.')
        remaining -= len(block)
        if inflater is None:
            writer.write(block)
            continue
        try:
            # max_length one past the cap: a bomb is caught after a single extra byte.
            writer.write(inflater.decompress(block, writer.cap - writer.written + 1))
            while inflater.unconsumed_tail:
                writer.write(inflater.decompress(inflater.unconsumed_tail, writer.cap - writer.written + 1))
        except zlib.error:
            raise UploadError(460, 'This piece arrived damaged.', reason='Checksum Mismatch')
        if inflater.unused_data:
            raise UploadError(400, 'This piece has data after its compressed part.')
    if inflater is not None and not inflater.eof:
        raise UploadError(460, 'This piece arrived damaged.', reason='Checksum Mismatch')


def patch(request, upload):
    """PATCH: append one piece at Upload-Offset. Returns the new offset."""
    from build.models import NetCDFUpload

    _gone_if_expired(upload)
    content_type = (request.headers.get('Content-Type') or '').split(';')[0].strip().lower()
    if content_type != OFFSET_CONTENT_TYPE:
        raise UploadError(415, 'Pieces must be sent as {}.'.format(OFFSET_CONTENT_TYPE))
    claimed_offset = parse_int_header(request, 'Upload-Offset')
    content_length = parse_int_header(request, 'Content-Length', required=False)
    if content_length is None:
        raise UploadError(411, 'Pieces need a Content-Length.')
    if content_length > max_patch_bytes():
        raise UploadError(413, 'Pieces can be at most {} bytes.'.format(max_patch_bytes()))
    coding = _content_coding(request)
    checksum = parse_checksum(request)

    if upload.finished_at is not None or upload.complete:
        if claimed_offset == upload.offset and content_length == 0:
            return tus_response(204, headers={'Upload-Offset': str(upload.offset)})
        raise UploadError(409, 'This upload already has all its bytes.',
                          headers={'Upload-Offset': str(upload.offset)})

    with user_slot(upload.user), locked(upload) as fd:
        _refresh(upload)
        start = upload.offset
        if claimed_offset != start:
            raise UploadError(409, 'The upload is at a different point than this piece expects.',
                              headers={'Upload-Offset': str(start)})
        # Drop anything past the offset, left by a piece that never finished.
        os.ftruncate(fd, start)
        cap = min(upload.length - start, max_patch_bytes())
        hasher = CHECKSUM_ALGORITHMS[checksum[0]]() if checksum else None
        writer = _PieceWriter(fd, start, cap, hasher)
        try:
            _read_body(request, content_length, coding, writer)
            if checksum and not _digests_match(hasher.digest(), checksum[1]):
                raise UploadError(460, 'This piece arrived damaged.', reason='Checksum Mismatch')
            new_offset = start + writer.written
            if start < MIN_FILE_BYTES <= new_offset:
                if not is_netcdf_file(io.BytesIO(os.pread(fd, MIN_FILE_BYTES, 0))):
                    raise UploadError(415, '"{}" is not a valid NetCDF file. Only NetCDF classic, '
                                           '64-bit, or HDF5-based files are accepted.'.format(upload.filename),
                                      headers={'X-Elsa-Upload-Rejected': 'not-netcdf'})
            # On disk before the offset says so.
            os.fdatasync(fd)
        except BaseException as exc:
            os.ftruncate(fd, start)
            if isinstance(exc, UploadError) and exc.headers.get('X-Elsa-Upload-Rejected'):
                # Not a NetCDF file: no point keeping any of it.
                upload.delete()
            raise

        now = timezone.now()
        moved = NetCDFUpload.objects.filter(pk=upload.pk, offset=start).update(
            offset=new_offset, updated_at=now, expires_at=now + expiry())
        if not moved:
            os.ftruncate(fd, start)
            raise UploadError(409, 'The upload moved on while this piece was arriving.')
        upload.offset = new_offset
        upload.expires_at = now + expiry()

    headers = {'Upload-Offset': str(new_offset)}
    headers.update(_expires_header(upload))
    return tus_response(204, headers=headers)


def _digests_match(actual, expected):
    import hmac
    return hmac.compare_digest(actual, expected)


def terminate(upload):
    """DELETE: cancel an upload. A finished one has become a NetCDFFile and stays."""
    if upload.finished_at is not None:
        raise UploadError(409, 'This upload is already in the bundle. Delete the file there instead.')
    if upload.complete:
        # Every byte is in, so finish may be moving the file right now. Wait for it.
        try:
            with locked(upload):
                upload.delete()
        except UploadError as exc:
            if exc.status != 404:
                raise
            upload.delete()
    else:
        # No need to wait for a piece still arriving: once the row is gone it can't commit.
        upload.delete()
    return tus_response(204)


def _refresh(upload):
    from build.models import NetCDFUpload
    try:
        upload.refresh_from_db()
    except NetCDFUpload.DoesNotExist:
        raise UploadError(404, 'This upload was cancelled.')


# ---- after the last byte ---------------------------------------------------------------------------

def _place_without_overwrite(source, destination):
    """Move source to destination, refusing if destination exists. A rename on the same disk."""
    try:
        os.link(source, destination)
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            raise UploadError(409, 'A file with this name appeared in the meantime. Please rename '
                                   'the file and upload it again.')
        if exc.errno not in (errno.EXDEV, errno.EPERM, errno.ENOTSUP):
            raise
        # Different filesystems: copy, but still never over an existing file.
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        try:
            out = os.open(destination, flags, 0o644)
        except FileExistsError:
            raise UploadError(409, 'A file with this name appeared in the meantime. Please rename '
                                   'the file and upload it again.')
        try:
            with open(source, 'rb') as src, os.fdopen(out, 'wb') as dst:
                out = None
                shutil.copyfileobj(src, dst, 16 * MIB)
                dst.flush()
                os.fsync(dst.fileno())
        except BaseException:
            if out is not None:
                os.close(out)
            os.remove(destination)
            raise
    os.remove(source)


def finish(upload, process):
    """Turn a complete upload into a NetCDFFile. process(netcdf_file) runs the harvest.

    Safe to call twice: the second call returns the file the first one made.
    Returns (netcdf_file, errors).
    """
    from build.models import NetCDFFile, NetCDFUpload

    if upload.netcdf_file_id:
        netcdf_file = upload.netcdf_file
        return netcdf_file, ([netcdf_file.processing_error] if netcdf_file.processing_error else [])
    if upload.finished_at is not None:
        raise UploadError(410, 'This upload was finished, but its file has since been deleted.')
    _gone_if_expired(upload)
    if not upload.complete:
        raise UploadError(409, 'Not every byte of this file is on the server yet.',
                          headers={'Upload-Offset': str(upload.offset)})

    with locked(upload) as fd:
        _refresh(upload)
        if upload.netcdf_file_id:
            return upload.netcdf_file, []
        if os.fstat(fd).st_size != upload.length:
            raise UploadError(500, 'The uploaded file is not the size it should be. Please upload it again.')
        # Another upload of the same name still on its way doesn't stop this one: first to finish wins.
        conflict = name_conflict(upload.collection, upload.filename, upload.stored_name,
                                 exclude_upload=upload, include_pending=False)
        if conflict:
            raise UploadError(409, conflict)

        storage = NetCDFFile._meta.get_field('file').storage
        os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
        name = storage.get_available_name(upload.stored_name)
        destination = storage.path(name)
        _place_without_overwrite(upload.staging_path(), destination)
        try:
            os.chmod(destination, 0o644)
            with transaction.atomic():
                netcdf_file = NetCDFFile.objects.create(
                    bundle=upload.bundle, collection=upload.collection, file=name,
                    title=upload.filename[:100])
                NetCDFUpload.objects.filter(pk=upload.pk).update(
                    netcdf_file=netcdf_file, finished_at=timezone.now())
        except BaseException:
            # Put the bytes back so a retry can finish without uploading again.
            shutil.move(destination, upload.staging_path())
            raise
        upload.netcdf_file = netcdf_file

    logger.info('NetCDF upload %s finished as NetCDFFile %s', upload.id, netcdf_file.pk)
    return netcdf_file, process(netcdf_file)
