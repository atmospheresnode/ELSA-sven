# -*- coding: utf-8 -*-
"""Bundle and file downloads, shared by the bundle page and the Admin Console.

A bundle goes out as a plain .tar made while it is sent. Nothing is copied to disk first, the
exact size is known before the first byte, and the bytes come out the same every time, so a
dropped download can carry on where it stopped (HTTP range requests, RFC 9110 section 14).

    bundle_response(request, folder, part, collection)   the bundle, or part of it
    file_response(request, path, filename, content_type)  one file
    choices(folder)                                        the parts, with sizes, for a menu

Files are opened one folder at a time without following links, so nothing outside the bundle
can be sent even if a file is swapped for a link mid-download. A file that changes while it is
being sent stops the download instead of sending a mixed-up copy.
"""
from __future__ import unicode_literals

import fcntl
import hashlib
import os
import re
import stat
import tarfile
import tempfile

from django.conf import settings
from django.contrib import messages
from django.http import HttpResponse, HttpResponseRedirect, StreamingHttpResponse
from django.utils.http import content_disposition_header, http_date

CHUNK = 1024 * 1024
BLOCK = tarfile.BLOCKSIZE
END = b'\0' * (2 * BLOCK)

PART_ALL = 'all'
PART_NO_NETCDF = 'no-netcdf'
PART_LABELS = 'labels'
PART_COLLECTION = 'collection'

UNSATISFIABLE = 'unsatisfiable'
RANGE = re.compile(r'^bytes=(\d*)-(\d*)$', re.ASCII)


class DownloadsBusy(Exception):
    """All the download slots are taken."""


class DownloadChanged(OSError):
    """A file changed or went away while it was being sent."""


# -- what goes in --

def _is_netcdf(name):
    return name.lower().endswith('.nc')


def _is_label(name):
    lower = name.lower()
    return lower.endswith('.xml') or (lower.startswith('collection_') and lower.endswith('.csv'))


def bundle_folder(bundle):
    """The bundle's folder, or None if it is missing or not under its owner's archive folder.

    A folder that is a link to someone else's bundle doesn't count.
    """
    folder = os.path.realpath(bundle.directory())
    if not bundle.directory_is_contained() or not os.path.isdir(folder):
        return None
    return folder


def _listing(folder):
    """[(relative path, stat, is_dir)] in archive order. Links and special files are left out.

    No recursion, so a folder of any depth is fine.
    """
    found = []
    pending = [(folder, '')]
    while pending:
        path, relative = pending.pop()
        with os.scandir(path) as entries:
            for entry in entries:
                name = relative + '/' + entry.name if relative else entry.name
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    found.append((name, entry.stat(follow_symlinks=False), True))
                    pending.append((entry.path, name))
                elif entry.is_file(follow_symlinks=False):
                    found.append((name, entry.stat(follow_symlinks=False), False))
    return _order(found)


def _order(found):
    """Each folder followed by its contents, names sorted, so the archive reads like a tree."""
    children = {}
    for item in found:
        parent = item[0].rpartition('/')[0]
        children.setdefault(parent, []).append(item)
    ordered, stack = [], [('', None)]
    while stack:
        name, item = stack.pop()
        if item is not None:
            ordered.append(item)
        if item is None or item[2]:
            kids = sorted(children.get(name, []), key=lambda kid: kid[0].rpartition('/')[2])
            stack.extend(reversed([(kid[0], kid) for kid in kids]))
    return ordered


def collections(folder):
    """The bundle's top folders, which are its collections."""
    return [name for name, _info, is_dir in _listing(folder) if is_dir and '/' not in name]


def _select(entries, part, collection):
    if part == PART_COLLECTION:
        return [entry for entry in entries
                if entry[0] == collection or entry[0].startswith(collection + '/')]
    if part == PART_ALL:
        return entries
    keep = _is_label if part == PART_LABELS else (lambda name: not _is_netcdf(name))
    wanted = [entry for entry in entries if not entry[2] and keep(entry[0].rsplit('/', 1)[-1])]
    # Only the folders those files are in.
    folders = set()
    for name, _info, _is_dir in wanted:
        steps = name.split('/')[:-1]
        for depth in range(1, len(steps) + 1):
            folders.add('/'.join(steps[:depth]))
    kept = set(name for name, _info, _is_dir in wanted)
    return [entry for entry in entries if entry[0] in kept or (entry[2] and entry[0] in folders)]


def choices(folder):
    """The ways to download this bundle, for a menu. Sizes are the files' total."""
    entries = _listing(folder)
    files = [(name, info.st_size) for name, info, is_dir in entries if not is_dir]
    total = sum(size for _name, size in files)
    netcdf = sum(size for name, size in files if _is_netcdf(name.rsplit('/', 1)[-1]))
    labels = sum(size for name, size in files if _is_label(name.rsplit('/', 1)[-1]))
    found = [{'part': PART_ALL, 'collection': '', 'title': 'Whole bundle', 'size': total,
              'note': 'Every file, data included.'}]
    if netcdf:
        found.append({'part': PART_NO_NETCDF, 'collection': '', 'title': 'Without NetCDF files',
                      'size': total - netcdf, 'note': 'Labels, inventories and documents. No NetCDF data.'})
    found.append({'part': PART_LABELS, 'collection': '', 'title': 'Labels only', 'size': labels,
                  'note': 'The .xml labels and collection inventories.'})
    for name in collections(folder):
        size = sum(file_size for path, file_size in files if path.startswith(name + '/'))
        found.append({'part': PART_COLLECTION, 'collection': name, 'title': 'Collection: {}'.format(name),
                      'size': size, 'note': 'Just this collection.'})
    return found


# -- the archive --

def _header(name, info, is_dir):
    entry = tarfile.TarInfo(name)
    entry.type = tarfile.DIRTYPE if is_dir else tarfile.REGTYPE
    entry.mode = 0o755 if is_dir else 0o644
    entry.size = 0 if is_dir else info.st_size
    entry.mtime = int(info.st_mtime)
    # The server's account names aren't the reader's business.
    entry.uid = entry.gid = 0
    entry.uname = entry.gname = ''
    # PAX, so files over 8 GiB and long names are fine.
    return entry.tobuf(tarfile.PAX_FORMAT, 'utf-8', 'surrogateescape')


class _Piece(object):
    __slots__ = ('length', 'data', 'relative', 'size', 'mtime_ns')

    def __init__(self, length, data=None, relative=None, size=0, mtime_ns=0):
        self.length = length
        self.data = data
        self.relative = relative
        self.size = size
        self.mtime_ns = mtime_ns


class TarPlan(object):
    """Where every byte of the archive comes from, worked out before sending any."""

    def __init__(self, folder, entries, tag):
        self.folder = folder
        self.pieces = []
        root = os.path.basename(folder)
        digest = hashlib.sha256('elsa-tar-1|{}'.format(tag).encode('utf-8', 'surrogateescape'))
        newest = 0
        top = os.stat(folder)
        for relative, info, is_dir in [('', top, True)] + list(entries):
            name = root + '/' + relative if relative else root
            header = _header(name, info, is_dir)
            self.pieces.append(_Piece(len(header), data=header))
            digest.update('{}|{}|{}|{}|{}\n'.format(name, is_dir, 0 if is_dir else info.st_size,
                                                    info.st_mtime_ns, info.st_ino)
                          .encode('utf-8', 'surrogateescape'))
            newest = max(newest, int(info.st_mtime))
            if is_dir or not info.st_size:
                continue
            self.pieces.append(_Piece(info.st_size, relative=relative, size=info.st_size,
                                      mtime_ns=info.st_mtime_ns))
            padding = -info.st_size % BLOCK
            if padding:
                self.pieces.append(_Piece(padding, data=b'\0' * padding))
        self.pieces.append(_Piece(len(END), data=END))
        self.total = sum(piece.length for piece in self.pieces)
        self.etag = '"{}"'.format(digest.hexdigest()[:40])
        self.last_modified = newest

    def chunks(self, start, end):
        """The bytes from start to end, inclusive."""
        position = 0
        for piece in self.pieces:
            if position + piece.length <= start:
                position += piece.length
                continue
            if position > end:
                break
            low = max(start - position, 0)
            high = min(end - position, piece.length - 1)
            if piece.data is not None:
                yield piece.data[low:high + 1]
            else:
                fd = _open_inside(self.folder, piece.relative)
                try:
                    for chunk in _read(fd, piece.relative, piece.size, piece.mtime_ns, low, high - low + 1):
                        yield chunk
                finally:
                    os.close(fd)
            position += piece.length


def _open_inside(folder, relative):
    """Open a file under folder one step at a time, refusing links at every step."""
    steps = relative.split('/')
    if any(step in ('', '.', '..') for step in steps):
        raise DownloadChanged('Refused to open {}'.format(relative))
    current = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for step in steps[:-1]:
            following = os.open(step, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = following
        return os.open(steps[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=current)
    except OSError as error:
        raise DownloadChanged('{} could not be opened: {}'.format(relative, error))
    finally:
        os.close(current)


def _same(info, size, mtime_ns):
    return stat.S_ISREG(info.st_mode) and info.st_size == size and info.st_mtime_ns == mtime_ns


def _read(fd, relative, size, mtime_ns, offset, count):
    if not _same(os.fstat(fd), size, mtime_ns):
        raise DownloadChanged('{} changed after the download started'.format(relative))
    while count:
        data = os.pread(fd, min(CHUNK, count), offset)
        if not data:
            raise DownloadChanged('{} got shorter while it was being sent'.format(relative))
        offset += len(data)
        count -= len(data)
        yield data
    if not _same(os.fstat(fd), size, mtime_ns):
        raise DownloadChanged('{} changed while it was being sent'.format(relative))


def tar_plan(folder, part=PART_ALL, collection=''):
    """The plan for a part of the bundle, or None if that part doesn't exist."""
    entries = _listing(folder)
    if part == PART_COLLECTION:
        if collection not in [name for name, _info, is_dir in entries if is_dir and '/' not in name]:
            return None
    elif part not in (PART_ALL, PART_NO_NETCDF, PART_LABELS):
        return None
    return TarPlan(folder, _select(entries, part, collection),
                   '{}|{}'.format(part, collection if part == PART_COLLECTION else ''))


def tar_name(folder, part, collection=''):
    base = os.path.basename(folder)
    if part == PART_NO_NETCDF:
        return '{}_without_netcdf.tar'.format(base)
    if part == PART_LABELS:
        return '{}_labels.tar'.format(base)
    if part == PART_COLLECTION:
        return '{}_{}.tar'.format(base, collection)
    return '{}.tar'.format(base)


# -- how many big downloads at once --

def max_downloads():
    return getattr(settings, 'DOWNLOAD_MAX_CONCURRENT', 5)


def slot_min_bytes():
    """Downloads smaller than this never wait for a slot."""
    return getattr(settings, 'DOWNLOAD_SLOT_MIN_BYTES', 64 * 1024 * 1024)


def _slot_folder():
    return getattr(settings, 'DOWNLOAD_SLOT_DIR', '') or os.path.join(
        tempfile.gettempdir(), 'elsa-download-slots-{}'.format(os.geteuid()))


NO_LIMIT = -1


def _take_slot():
    """A held lock file, None when all are taken, NO_LIMIT when the slots can't be kept.

    A lock is let go when its file is closed, including when the process dies, so a crash
    can't leave a slot taken.
    """
    folder = _slot_folder()
    try:
        os.makedirs(folder, mode=0o700, exist_ok=True)
        info = os.lstat(folder)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise OSError('{} is not a private folder of this account'.format(folder))
        for number in range(max_downloads()):
            fd = os.open(os.path.join(folder, 'slot-{}'.format(number)),
                         os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except BlockingIOError:
                os.close(fd)
    except OSError as error:
        # Better to let the download through than to stop every download.
        print('download: not limiting downloads at once: {}'.format(error))
        return NO_LIMIT
    return None


class _Stream(object):
    """The response body. Closing it lets go of the files and the slot."""

    def __init__(self, chunks, slot, closing=None):
        self.chunks = chunks
        self.slot = slot
        self.closing = closing

    def __iter__(self):
        return self

    def __next__(self):
        try:
            return next(self.chunks)
        except DownloadChanged as error:
            print('download stopped: {}'.format(error))
            raise

    def close(self):
        try:
            self.chunks.close()
            closing, self.closing = self.closing, None
            if closing is not None:
                closing()
        finally:
            slot, self.slot = self.slot, None
            if slot is not None and slot != NO_LIMIT:
                os.close(slot)

    def __del__(self):
        # Django drops a response without closing it when a middleware fails after the view,
        # so let go of the slot and files when it is thrown away too.
        self.close()


# -- ranges --

def wanted_range(request, total, etag, last_modified):
    """(start, end) for a Range the server should honour, UNSATISFIABLE, or None for all of it.

    One range only. Anything else is answered with the whole file, which RFC 9110 allows.
    """
    header = request.META.get('HTTP_RANGE', '')
    if request.method != 'GET' or not header:
        return None
    validator = request.META.get('HTTP_IF_RANGE')
    # Only the exact ETag carries on. A date is only good to the second, so a file changed in
    # the same second could join two versions; starting again is always safe.
    if validator is not None and validator.strip() != etag:
        return None
    match = RANGE.match(header.strip())
    if match is None:
        return None
    first, last = match.groups()
    if first == '' and last == '':
        return None
    if first == '':
        suffix = int(last)
        if suffix == 0 or total == 0:
            return UNSATISFIABLE
        return max(total - suffix, 0), total - 1
    start = int(first)
    if last and int(last) < start:
        return None
    if start >= total:
        return UNSATISFIABLE
    end = int(last) if last else total - 1
    return start, min(end, total - 1)


def _respond(request, total, etag, last_modified, filename, content_type, body, as_attachment=True):
    wanted = wanted_range(request, total, etag, last_modified)
    if wanted == UNSATISFIABLE:
        response = HttpResponse(status=416)
        response['Content-Range'] = 'bytes */{}'.format(total)
    else:
        start, end = wanted or (0, total - 1)
        length = end - start + 1
        if request.method == 'HEAD':
            response = HttpResponse(status=200, content_type=content_type)
            response['Content-Length'] = str(total)
        else:
            slot = None
            if length >= slot_min_bytes():
                slot = _take_slot()
                if slot is None:
                    raise DownloadsBusy()
            try:
                stream = body(start, end, slot)
            except BaseException:
                if slot is not None and slot != NO_LIMIT:
                    os.close(slot)
                raise
            _close_with_request(request, stream)
            response = StreamingHttpResponse(stream, status=206 if wanted else 200,
                                             content_type=content_type)
            response['Content-Length'] = str(length)
            if wanted:
                response['Content-Range'] = 'bytes {}-{}/{}'.format(start, end, total)
        response['Content-Disposition'] = content_disposition_header(as_attachment, filename)
        # A new download, not a HEAD or one carrying on from where it stopped. Worth logging.
        response.fresh = request.method == 'GET' and start == 0
    response['Accept-Ranges'] = 'bytes'
    response['ETag'] = etag
    response['Last-Modified'] = http_date(last_modified)
    response['Cache-Control'] = 'private, no-cache'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def _close_with_request(request, stream):
    """Close the stream when the request is done, even if Django never sends it.

    Django adds request.close to whatever response finally goes out, an error page included,
    so a download dropped by a failing middleware still lets go of its slot and files.
    """
    streams = request.__dict__.setdefault('_download_streams', [])
    if not streams:
        original = request.close

        def close():
            try:
                original()
            finally:
                for item in request._download_streams:
                    item.close()

        request.close = close
    streams.append(stream)


def busy_response(request, back):
    """What to send when every slot is taken: a message for a person, a 503 for a resume."""
    if request.META.get('HTTP_RANGE'):
        response = HttpResponse('Too many downloads are running. Try again in a few minutes.',
                                status=503, content_type='text/plain')
        response['Retry-After'] = '120'
        return response
    messages.warning(request, '{} large downloads are already running, which is as many as the '
                              'server allows at once. Try again in a few minutes.'.format(max_downloads()))
    return HttpResponseRedirect(back)


# -- the two kinds of download --

def bundle_response(request, folder, part=PART_ALL, collection=''):
    """The bundle, or part of it, as a .tar. None if that part doesn't exist.

    Raises DownloadsBusy when every slot is taken.
    """
    plan = tar_plan(folder, part, collection)
    if plan is None:
        return None

    def body(start, end, slot):
        return _Stream(plan.chunks(start, end), slot)

    return _respond(request, plan.total, plan.etag, plan.last_modified,
                    tar_name(folder, part, collection), 'application/x-tar', body)


def file_response(request, path, filename, content_type='application/octet-stream', as_attachment=True):
    """One file, with resume. path must already be checked; the last step mustn't be a link.

    Raises DownloadsBusy when every slot is taken, OSError if the file can't be opened.
    """
    handle = _Handle(os.open(path, os.O_RDONLY | os.O_NOFOLLOW))
    try:
        info = os.fstat(handle.fd)
        if not stat.S_ISREG(info.st_mode):
            raise OSError('{} is not a regular file'.format(path))
        etag = '"{:x}-{:x}-{:x}"'.format(info.st_ino, info.st_size, info.st_mtime_ns)

        def body(start, end, slot):
            chunks = _read(handle.fd, filename, info.st_size, info.st_mtime_ns, start, end - start + 1)
            return _Stream(chunks, slot, closing=handle.close)

        response = _respond(request, info.st_size, etag, int(info.st_mtime), filename, content_type, body,
                            as_attachment=as_attachment)
    except BaseException:
        handle.close()
        raise
    if not isinstance(response, StreamingHttpResponse):
        handle.close()
    return response


class _Handle(object):
    """An open file that is closed once, however many times close() is called."""

    def __init__(self, fd):
        self.fd = fd

    def close(self):
        fd, self.fd = self.fd, None
        if fd is not None:
            os.close(fd)

    def __del__(self):
        self.close()
