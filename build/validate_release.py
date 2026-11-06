"""Which validate release a host runs, and moving to a newer one.

* Java: `java_home_for()` reads the class version from the release's jar and picks an
  installed Java that can run it, so `VALIDATE_JAVA_HOME = 'auto'` keeps working.
* Releases: `latest_release()` asks GitHub for the newest stable one, and `install()` downloads
  it, checks the SHA-256 and unpacks it next to the others.
* The switch: hosts follow the `VALIDATE_CURRENT_LINK` symlink. `switch()` checks the target
  starts, then replaces the link in one rename. `rollback()` reads the switch record.

Deciding whether to switch is up to validate_qualify and a person.
"""
import datetime
import functools
import glob
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile

from django.conf import settings

GITHUB_RELEASES = 'https://api.github.com/repos/NASA-PDS/validate/releases'
JAVA_SEARCH_DIRS = ('/usr/lib/jvm',)

# The class every release launches. Its class file version is the Java it needs.
LAUNCHER_CLASS = 'gov/nasa/pds/validate/ValidateLauncher.class'
JAR_NAME = re.compile(r'^validate-(\d+(?:\.\d+)*)\.jar$')
VERSION_LINE = re.compile(r'Version\s+(\S+)')


class ReleaseError(RuntimeError):
    """Something about a release, or the Java to run it, is not as it must be."""


# -- versions ----------------------------------------------------------------------

def version_key(version):
    """'5.0.0' -> (5, 0, 0), for ordering. Anything unparseable sorts first."""
    try:
        return tuple(int(part) for part in str(version).split('.'))
    except ValueError:
        return ()


def release_jar(home):
    """The validate jar inside a release directory, or None."""
    for path in sorted(glob.glob(os.path.join(home, 'lib', 'validate-*.jar'))):
        if JAR_NAME.match(os.path.basename(path)):
            return path
    return None


def release_version(home):
    """The release a directory holds, read from its jar's name. '' if it holds none."""
    jar = release_jar(home) if home else None
    return JAR_NAME.match(os.path.basename(jar)).group(1) if jar else ''


# -- Java --------------------------------------------------------------------------

def required_java(home):
    """Lowest Java major version that can run this release.

    Bytes 6-7 of the launcher's class file are the Java version plus 44 (61 is Java 17, 69 is Java 25).
    """
    jar = release_jar(home)
    if jar is None:
        raise ReleaseError('No validate jar under {}/lib.'.format(home))
    try:
        with zipfile.ZipFile(jar) as archive:
            header = archive.read(LAUNCHER_CLASS)[:8]
    except (OSError, KeyError, zipfile.BadZipFile) as error:
        raise ReleaseError('Could not read {} from {}: {}'.format(
            LAUNCHER_CLASS, jar, error))
    if len(header) < 8 or header[:4] != b'\xca\xfe\xba\xbe':
        raise ReleaseError('{} in {} is not a Java class file.'.format(LAUNCHER_CLASS, jar))
    return int.from_bytes(header[6:8], 'big') - 44


@functools.lru_cache(maxsize=64)
def _asked_java_version(java_home):
    """`java -version` string, for an install with no `release` file. Cached since it starts a JVM."""
    try:
        said = subprocess.run([os.path.join(java_home, 'bin', 'java'), '-version'],
                              capture_output=True, text=True, errors='replace', timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ''
    match = re.search(r'version "([^"]+)"', said.stderr + said.stdout)
    return match.group(1) if match else ''


def _java_major(java_home):
    """The major version of a Java installation, or None if it cannot be told."""
    version = ''
    try:
        with open(os.path.join(java_home, 'release'), encoding='utf-8') as handle:
            match = re.search(r'^JAVA_VERSION="([^"]+)"', handle.read(), re.M)
            version = match.group(1) if match else ''
    except OSError:
        pass
    version = version or _asked_java_version(java_home)
    if not version:
        return None
    parts = version.split('.')
    # Java 8 and earlier call themselves 1.8.0_x.
    major = parts[1] if parts[0] == '1' and len(parts) > 1 else parts[0]
    try:
        return int(re.match(r'\d+', major).group(0))
    except (AttributeError, ValueError):
        return None


def installed_javas(search_dirs=None):
    """{major version: java home} for every Java we can run.

    The same runtime can have several names, so resolve and de-dupe them. Prefer a JDK to a JRE.
    """
    search_dirs = search_dirs or getattr(settings, 'VALIDATE_JAVA_SEARCH_DIRS', JAVA_SEARCH_DIRS)
    found = {}
    for root in search_dirs:
        for entry in sorted(glob.glob(os.path.join(root, '*'))):
            home = os.path.realpath(entry)
            if not os.access(os.path.join(home, 'bin', 'java'), os.X_OK):
                continue
            major = _java_major(home)
            if major is None:
                continue
            has_compiler = os.path.exists(os.path.join(home, 'bin', 'javac'))
            current = found.get(major)
            if current is None or (has_compiler and not current[1]):
                found[major] = (home, has_compiler)
    return {major: home for major, (home, _) in found.items()}


def java_home_for(home, search_dirs=None):
    """The Java to use for this release: the oldest one new enough."""
    needed = required_java(home)
    javas = installed_javas(search_dirs)
    usable = sorted(major for major in javas if major >= needed)
    if not usable:
        raise ReleaseError(
            'validate {} needs Java {} or newer, and this server has {}. Someone with '
            'root has to install Java {} (for example Temurin {}).'.format(
                release_version(home) or 'at ' + home, needed,
                ', '.join('Java {}'.format(m) for m in sorted(javas)) or 'no Java at all',
                needed, needed))
    return javas[usable[0]]


# -- where releases live -----------------------------------------------------------

def _work_dir():
    return getattr(settings, 'VALIDATE_WORK_DIR', os.path.join(settings.BASE_DIR, 'validation'))


def releases_dir():
    return getattr(settings, 'VALIDATE_RELEASES_DIR', '') or os.path.join(_work_dir(), 'releases')


def current_link():
    return getattr(settings, 'VALIDATE_CURRENT_LINK', '') or os.path.join(_work_dir(), 'validate-current')


def release_home(version):
    return os.path.join(releases_dir(), 'validate-{}'.format(version))


def installed_releases():
    """{version: directory} for every release install() has unpacked here."""
    found = {}
    for path in glob.glob(os.path.join(releases_dir(), 'validate-*')):
        version = release_version(path)
        if version and os.path.isdir(path) and os.path.exists(_receipt_path(path)):
            found[version] = path
    return dict(sorted(found.items(), key=lambda item: version_key(item[0])))


def _receipt_path(home):
    return os.path.join(home, '.elsa-release.json')


def receipt(home):
    """What install() recorded about a release: where it came from and its checksum."""
    try:
        with open(_receipt_path(home), encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


# -- GitHub ------------------------------------------------------------------------

def _stable(release):
    tag = release.get('tag_name') or ''
    return (not release.get('draft') and not release.get('prerelease')
            and 'SNAPSHOT' not in tag.upper() and bool(version_key(tag.lstrip('v'))))


def _describe(release):
    version = release['tag_name'].lstrip('v')
    asset_name = 'validate-{}-bin.tar.gz'.format(version)
    asset = next((a for a in release.get('assets') or [] if a.get('name') == asset_name), None)
    if asset is None:
        raise ReleaseError('Release {} has no {}.'.format(version, asset_name))
    digest = asset.get('digest') or ''
    return {
        'version': version,
        'published_at': release.get('published_at') or '',
        'url': asset['browser_download_url'],
        'size': asset.get('size') or 0,
        # GitHub gives each asset's SHA-256. Refuse a release without one.
        'sha256': digest.split(':', 1)[1] if digest.startswith('sha256:') else '',
        'notes_url': release.get('html_url') or '',
        'notes': release.get('body') or '',
    }


def _github(url):
    import requests
    response = requests.get(url, timeout=30, headers={'Accept': 'application/vnd.github+json'})
    response.raise_for_status()
    return response.json()


def latest_release():
    """The newest stable release on GitHub, described by _describe()."""
    releases = [r for r in _github(GITHUB_RELEASES + '?per_page=30') if _stable(r)]
    if not releases:
        raise ReleaseError('GitHub lists no stable validate release.')
    newest = max(releases, key=lambda r: version_key(r['tag_name'].lstrip('v')))
    return _describe(newest)


def release_info(version):
    """One named release, described by _describe()."""
    release = _github('{}/tags/v{}'.format(GITHUB_RELEASES, version))
    if not _stable(release):
        raise ReleaseError('{} is not a stable release.'.format(version))
    return _describe(release)


# -- installing --------------------------------------------------------------------

def _download(url, destination):
    import requests
    with requests.get(url, stream=True, timeout=60) as response:
        response.raise_for_status()
        with open(destination, 'wb') as handle:
            for chunk in response.iter_content(1 << 20):
                handle.write(chunk)


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _make_readable(root):
    """World-readable, directories traversable, launchers executable, since checks run as apache."""
    for directory, dirnames, filenames in os.walk(root):
        os.chmod(directory, os.stat(directory).st_mode | 0o755)
        for name in filenames:
            path = os.path.join(directory, name)
            mode = os.stat(path).st_mode | 0o644
            if os.path.basename(directory) == 'bin':
                mode |= 0o755
            os.chmod(path, mode)


def install(info, fetch=None):
    """Download, verify and unpack a release. Returns its directory.

    Fine if it's already installed. Unpacked in a scratch directory and renamed into place.
    `fetch(url, destination)` is the downloader, replaceable for tests.
    """
    if not info.get('sha256'):
        raise ReleaseError('GitHub states no SHA-256 for validate {}, so it cannot be '
                           'verified. Not installing it.'.format(info['version']))
    target = release_home(info['version'])
    if os.path.isdir(target) and receipt(target).get('sha256') == info['sha256']:
        return target
    if os.path.exists(target):
        raise ReleaseError('{} exists but was not installed by ELSA (or its checksum '
                           'differs). Move it aside and run this again.'.format(target))

    os.makedirs(releases_dir(), exist_ok=True)
    # Created under the installer's umask, which on atmos leaves it closed to apache.
    os.chmod(releases_dir(), os.stat(releases_dir()).st_mode | 0o755)
    scratch = tempfile.mkdtemp(prefix='.installing-', dir=releases_dir())
    try:
        tarball = os.path.join(scratch, 'release.tar.gz')
        (fetch or _download)(info['url'], tarball)
        actual = _sha256(tarball)
        if actual != info['sha256']:
            raise ReleaseError('Checksum mismatch for {}: GitHub states {}, the download is '
                               '{}. Nothing was installed.'.format(info['url'], info['sha256'], actual))
        unpacked = os.path.join(scratch, 'unpacked')
        with tarfile.open(tarball) as archive:
            # The 'data' filter refuses absolute paths, '..', device files and links outside the destination.
            archive.extractall(unpacked, filter='data')
        tops = os.listdir(unpacked)
        if len(tops) != 1:
            raise ReleaseError('Expected one top-level directory in the release, found {}.'
                               .format(tops))
        home = os.path.join(unpacked, tops[0])
        if not os.path.exists(os.path.join(home, 'bin', 'validate')):
            raise ReleaseError('The release has no bin/validate.')
        if release_version(home) != info['version']:
            raise ReleaseError('The release says it is {} but was published as {}.'.format(
                release_version(home) or 'nothing', info['version']))
        with open(_receipt_path(home), 'w', encoding='utf-8') as handle:
            json.dump({'version': info['version'], 'sha256': info['sha256'],
                       'url': info['url'], 'published_at': info.get('published_at', ''),
                       'installed_at': _now()}, handle, indent=1)
        _make_readable(home)
        os.rename(home, target)
        return target
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


# -- proving a release runs --------------------------------------------------------

def smoke_test(home, java_home):
    """Start the release under this Java and run it on an empty directory.

    Returns the version it reports, or raises ReleaseError with what it printed.
    """
    from build.validate_runner import _environment

    scratch = tempfile.mkdtemp(prefix='elsa-validate-smoke-')
    try:
        environment = _environment(java_home=java_home)
        executable = os.path.join(home, 'bin', 'validate')
        said = subprocess.run([executable, '--version'], env=environment, capture_output=True,
                              text=True, errors='replace', timeout=300)
        output = said.stdout + said.stderr
        match = VERSION_LINE.search(output)
        if not match:
            raise ReleaseError('validate --version did not report a version under {}. '
                               'It said: {}'.format(java_home, output.strip()[-400:]))

        target = os.path.join(scratch, 'empty')
        os.makedirs(target)
        report = os.path.join(scratch, 'report.json')
        ran = subprocess.run([executable, '-t', target, '-r', report, '-s', 'json'],
                             env=environment, capture_output=True, text=True,
                             errors='replace', timeout=300)
        if not os.path.exists(report):
            raise ReleaseError('validate started but wrote no report. It said: {}'.format(
                (ran.stdout + ran.stderr).strip()[-400:]))
        return match.group(1)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


# -- switching ---------------------------------------------------------------------

def _ancestors(path):
    path = os.path.abspath(path)
    while True:
        yield path
        parent = os.path.dirname(path)
        if parent == path:
            return
        path = parent


def writable_by_others(home, link):
    """Why another user could change what the web server runs, or ''.

    Checks for world-writable, non-sticky directories on the way to the link or release.
    """
    paths = list(_ancestors(os.path.dirname(link))) + list(_ancestors(os.path.realpath(home)))
    for directory in paths:
        if os.path.isdir(directory):
            mode = os.stat(directory).st_mode
            if mode & stat.S_IWOTH and not mode & stat.S_ISVTX:
                return ('{} is writable by every user on this server, so anyone could replace '
                        'the validate the web server runs. Remove that (chmod o-w) first.'.format(directory))
    return ''


def unreachable_by_others(home, link):
    """Why the web server couldn't run this release through this link, or ''.

    Checks run as apache, so every directory has to be traversable and the release readable.
    """
    for directory in list(_ancestors(os.path.realpath(home))) + list(_ancestors(os.path.dirname(link))):
        if os.path.isdir(directory) and not os.stat(directory).st_mode & 0o001:
            return '{} is not traversable by other users (the web server).'.format(directory)
    for directory, _dirnames, filenames in os.walk(home):
        if not os.stat(directory).st_mode & 0o005 == 0o005:
            return '{} is not readable by other users.'.format(directory)
        for name in filenames:
            if not os.stat(os.path.join(directory, name)).st_mode & 0o004:
                return '{} is not readable by other users.'.format(os.path.join(directory, name))
    return ''


def _history_path():
    return os.path.join(_work_dir(), 'upgrades', 'history.json')


def history():
    try:
        with open(_history_path(), encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return []


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')


def linked_release():
    """The release the link currently points at, or '' when there is no link."""
    link = current_link()
    return release_version(os.path.realpath(link)) if os.path.islink(link) else ''


def switch(version, who='', private=False):
    """Point the link at an installed release after checking it runs. Returns a record.

    One atomic rename. The old release stays on disk for running checks and rollback.
    """
    releases = installed_releases()
    if version not in releases:
        raise ReleaseError('validate {} is not installed here. Install it first: '
                           'manage.py validate_release install {}'.format(version, version))
    home = releases[version]
    exposed = writable_by_others(home, current_link())
    if exposed:
        raise ReleaseError(exposed)
    if not private:
        problem = unreachable_by_others(home, current_link())
        if problem:
            raise ReleaseError(problem + ' Fix the permissions, or pass --private on a '
                               'development host whose server runs as you.')
    java_home = java_home_for(home)
    reported = smoke_test(home, java_home)
    if reported != version:
        raise ReleaseError('{} reports itself as {}.'.format(home, reported))

    link = current_link()
    if os.path.exists(link) and not os.path.islink(link):
        raise ReleaseError('{} is a real directory, not a link. Move it aside first.'.format(link))
    previous = linked_release()

    os.makedirs(os.path.dirname(link), exist_ok=True)
    staging = '{}.switching-{}'.format(link, os.getpid())
    if os.path.lexists(staging):
        os.remove(staging)
    os.symlink(home, staging)
    os.replace(staging, link)

    record = {'at': _now(), 'from': previous, 'to': version, 'java_home': java_home, 'by': who}
    entries = history() + [record]
    os.makedirs(os.path.dirname(_history_path()), exist_ok=True)
    with open(_history_path(), 'w', encoding='utf-8') as handle:
        json.dump(entries, handle, indent=1)
    return record


def rollback(who='', private=False):
    """Switch back to the release in use before the last switch."""
    for entry in reversed(history()):
        if entry.get('to') == linked_release() and entry.get('from'):
            return switch(entry['from'], who=who, private=private)
    raise ReleaseError('There is no earlier release recorded to go back to.')


def host_follows_link():
    """True when this host's VALIDATE_HOME is the switchable link."""
    configured = getattr(settings, 'VALIDATE_HOME', '')
    return bool(configured) and os.path.abspath(configured) == os.path.abspath(current_link())


# -- cleaning up -------------------------------------------------------------------

# Interrupted installs leave a scratch directory. A younger one might still be running.
STALE_INSTALL_SECONDS = 24 * 3600


def in_use_release():
    """The release checks run with: the link's target, or else VALIDATE_HOME's. '' if neither."""
    return linked_release() or release_version(
        os.path.realpath(getattr(settings, 'VALIDATE_HOME', '') or ''))


def releases_to_keep():
    """{version: why} for every release that has to stay, or None if we can't tell.

    Keeps the one in use, the previous ones for rollback (VALIDATE_RELEASES_KEEP_PREVIOUS,
    default 1), and any newer one not switched to yet.
    """
    current = in_use_release()
    if not current:
        return None
    keep = {current: 'in use'}

    wanted = getattr(settings, 'VALIDATE_RELEASES_KEEP_PREVIOUS', 1)
    previous = []
    for entry in reversed(history()):
        for version in (entry.get('from'), entry.get('to')):
            if version and version != current and version not in previous:
                previous.append(version)
    for version in previous[:wanted]:
        keep.setdefault(version, 'rollback target')

    for version in installed_releases():
        if version_key(version) > version_key(current):
            keep.setdefault(version, 'newer, not switched to yet')
    return keep


def prune(dry_run=False):
    """Remove releases nothing needs. Returns [(path, why removed)].

    Only touches directories install() created. Renamed out of the way before deleting.
    """
    keep = releases_to_keep()
    removed = []
    if keep is None:
        return removed

    for version, home in installed_releases().items():
        if version in keep or os.path.realpath(home) == os.path.realpath(current_link()):
            continue
        removed.append((home, 'validate {}, older than every release kept'.format(version)))
        if not dry_run:
            doomed = os.path.join(releases_dir(), '.removing-{}-{}'.format(version, os.getpid()))
            os.rename(home, doomed)
            shutil.rmtree(doomed, ignore_errors=True)

    now = datetime.datetime.now().timestamp()
    for scratch in glob.glob(os.path.join(releases_dir(), '.installing-*')) + \
            glob.glob(os.path.join(releases_dir(), '.removing-*')):
        if now - os.stat(scratch).st_mtime > STALE_INSTALL_SECONDS:
            removed.append((scratch, 'left by an install or removal that did not finish'))
            if not dry_run:
                shutil.rmtree(scratch, ignore_errors=True)
    return removed
