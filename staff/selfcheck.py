# -*- coding: utf-8 -*-
"""Server security check, for the Health page and `manage.py staff_selfcheck`.

Read-only. Not a Django system check since those can block migrate.
"""
from __future__ import unicode_literals

import os
import stat
from datetime import timedelta

import django
from django.conf import settings
from django.utils import timezone

OK, WARN, FAIL = 'ok', 'warn', 'fail'

# The newest 4.2 security release this check knows about.  Newer is fine.
DJANGO_MINIMUM = (4, 2, 30)


def _row(name, level, found, fix=''):
    return {'name': name, 'level': level, 'found': found, 'fix': fix}


def _django():
    current = django.VERSION[:3]
    found = 'Django {0}'.format(django.get_version())
    # Whether this series still gets security fixes, from the daily update check.
    from .models import WatchedSource
    row = WatchedSource.objects.filter(key='django').first()
    details = (row.details if row else None) or {}
    if details.get('supported') is False:
        return _row('Django security updates', FAIL,
                    '{0}: the {1} series stopped getting security fixes on {2}'.format(
                        found, details.get('cycle', ''), details.get('eol', '')),
                    'Move to Django {0} (the supported long-term release); the "Outside update" request in '
                    'the Admin Console has the rollout checklist.'.format(details.get('newest_lts_latest') or 'a supported version'))
    if current < DJANGO_MINIMUM:
        return _row('Django security updates', FAIL, found,
                    'Upgrade to Django {0} or later in the 4.2 series: '
                    'pip install "Django>={0},<5.0"'.format('.'.join(map(str, DJANGO_MINIMUM))))
    return _row('Django security updates', OK, found)


def _debug():
    if settings.DEBUG:
        return _row('Debug mode', WARN, 'DEBUG is on',
                    'Fine on a development machine. On the live site set DEBUG = False in settings.py.')
    return _row('Debug mode', OK, 'DEBUG is off')


def _walk(patterns):
    for pattern in patterns:
        if hasattr(pattern, 'url_patterns'):
            for inner in _walk(pattern.url_patterns):
                yield inner
        else:
            yield pattern


def _stock_login():
    """Django's own login page skips ELSA's second factor, so it can't be routed."""
    from django.contrib.auth import views as auth_views
    from django.urls import get_resolver
    bad = []
    for pattern in _walk(get_resolver().url_patterns):
        view = getattr(pattern.callback, 'view_class', None)
        if view is not None and view.__module__ == auth_views.__name__ and view.__name__ in (
                'LoginView', 'PasswordResetConfirmView'):
            bad.append(str(pattern.pattern))
    if bad:
        return _row('Two-step sign-in cannot be skipped', FAIL,
                    "Django's own sign-in pages are reachable: " + ', '.join(bad),
                    "Remove include('django.contrib.auth.urls') from elsa/urls.py.")
    return _row('Two-step sign-in cannot be skipped', OK, "Django's own sign-in pages are not routed")


def _login_url():
    """A LOGIN_URL written as a path ignores the mount, so prod sends visitors to a 404."""
    from django.urls import NoReverseMatch, reverse
    name = 'Sign-in links stay inside ELSA'
    try:
        reverse(settings.LOGIN_URL)
    except NoReverseMatch:
        return _row(name, WARN if settings.DEBUG else FAIL,
                    'LOGIN_URL is the path {0}, which ignores the /elsa mount'.format(settings.LOGIN_URL),
                    "Set LOGIN_URL = 'friends:login' in settings.py.")
    return _row(name, OK, 'LOGIN_URL is {0}'.format(settings.LOGIN_URL))


def _cookies():
    if settings.DEBUG:
        return _row('Cookies only over HTTPS', OK, 'Not required on a development machine')
    missing = [name for name in ('SESSION_COOKIE_SECURE', 'CSRF_COOKIE_SECURE') if not getattr(settings, name, False)]
    if missing:
        return _row('Cookies only over HTTPS', FAIL, ', '.join(missing) + ' off',
                    'Set {0} = True in settings.py.'.format(' and '.join(missing)))
    return _row('Cookies only over HTTPS', OK, 'Session and CSRF cookies are HTTPS-only')


def _hsts():
    seconds = getattr(settings, 'SECURE_HSTS_SECONDS', 0) or 0
    if seconds:
        return _row('HTTPS enforced by browsers (HSTS)', OK, 'On, for {0} days'.format(seconds // 86400))
    return _row('HTTPS enforced by browsers (HSTS)', WARN, 'Off',
                'HSTS applies to every page on the host name, not only ELSA. When the whole host '
                'is HTTPS-only, set SECURE_HSTS_SECONDS = 31536000 in settings.py (and ask before '
                'adding includeSubDomains).')


def _private_file(label, path):
    try:
        mode = os.stat(path).st_mode
    except OSError:
        return None
    if mode & (stat.S_IROTH | stat.S_IWOTH):
        return _row(label, FAIL, '{0} can be read by every account on the server'.format(os.path.basename(path)),
                    'chmod o-rwx {0}'.format(path))
    return _row(label, OK, '{0} is private to the ELSA accounts'.format(os.path.basename(path)))


def _secrets():
    folder = os.path.join(settings.BASE_DIR, 'elsa')
    rows = [_private_file('Settings file is private', os.path.join(folder, 'settings.py')),
            _private_file('Secrets file is private', os.path.join(folder, 'secrets.py'))]
    return [row for row in rows if row]


def _releases():
    from .releases import release_root
    try:
        root = release_root()
    except OSError as error:
        return _row('Release packages are private', WARN, 'Cannot open the releases folder: {0}'.format(error))
    row = _private_file('Release packages are private', root)
    if row and row['level'] == FAIL:
        row['found'] = 'The releases folder can be read by every account on the server'
    return row


def _audit():
    from .models import AuditChain
    chain = AuditChain.objects.filter(pk=1).first()
    if chain is None or chain.verified_at is None:
        return _row('Audit log is untouched', WARN, 'Not checked yet',
                    'Run "manage.py verify_audit", and schedule it nightly (see the deploy notes).')
    if not chain.verified_ok:
        return _row('Audit log is untouched', FAIL, chain.problem or 'The last check failed',
                    'The developers were emailed. Keep a copy of the database before changing anything.')
    if chain.verified_at < timezone.now() - timedelta(days=2):
        return _row('Audit log is untouched', WARN,
                    'Last checked {0:%B %d, %Y}'.format(timezone.localtime(chain.verified_at)),
                    'The nightly "manage.py verify_audit" job does not seem to be running.')
    return _row('Audit log is untouched', OK, '{0} entr{1}, checked {2:%B %d %I:%M %p}'.format(
        chain.count, 'y' if chain.count == 1 else 'ies', timezone.localtime(chain.verified_at)))


def _headers():
    if 'friends.middleware.AccountSessionMiddleware' not in settings.MIDDLEWARE:
        return _row('Browser security headers', FAIL, 'The ELSA account middleware is not installed',
                    "Add 'friends.middleware.AccountSessionMiddleware' to MIDDLEWARE in settings.py.")
    return _row('Browser security headers', OK, 'Content-Security-Policy and Permissions-Policy on every page')


def _members_without_passkeys():
    from django.contrib.auth.models import User
    from django.db.models import Q
    from . import roles
    members = (User.objects.filter(is_active=True)
               .filter(Q(is_superuser=True) | Q(groups__name__in=roles.GROUP_NAMES)).distinct())
    missing = [u.username for u in members if not u.passkeys.exists()]
    if missing:
        return _row('Every staff member has a passkey', WARN, 'No passkey yet: ' + ', '.join(sorted(missing)),
                    'They cannot open the Admin Console until they add one on their Account page.')
    count = members.count()
    return _row('Every staff member has a passkey', OK, '{0} member{1}'.format(count, '' if count == 1 else 's'))


def _background_python():
    """Background jobs start manage.py with sys.executable, which under mod_wsgi may not be ELSA's Python.

    This has to run inside the web server to mean anything.
    """
    import subprocess  # nosec B404: fixed argument list, no shell
    import sys
    name = 'Background jobs can start'
    fix = ('Point mod_wsgi at ELSA\'s Python (python-home on WSGIDaemonProcess). Until then, email to users, '
           'Check now and PDS validate do not run from the web.')
    try:
        done = subprocess.run([sys.executable, '-c', 'import django'], timeout=20,  # nosec B603: fixed argv, no shell
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        return _row(name, FAIL, 'Could not run {0}: {1}'.format(sys.executable or 'Python', error), fix)
    if done.returncode:
        return _row(name, FAIL, '{0} cannot load Django'.format(sys.executable), fix)
    return _row(name, OK, 'From here they run with {0}'.format(sys.executable))


def _alert_recipients():
    """Alerts go to superusers, then settings.ADMINS. With neither nobody gets them."""
    from .security_alerts import recipients
    name = 'Security alerts reach someone'
    count = len(recipients())
    if not count:
        return _row(name, WARN, 'Nobody receives them: no active superuser has an email address, and ADMINS is empty',
                    'Add ADMINS = [("ELSA developers", "address")] to settings.py. Alerts stay in the audit log either way.')
    return _row(name, OK, '{0} address{1}'.format(count, '' if count == 1 else 'es'))


def _text_storage():
    """Check the staff tables and connection can take any character.

    latin1 refuses Greek and CJK, utf8mb3 refuses emoji, and strict mode fails the whole save.
    """
    from django.db import connection
    name = 'Messages can hold any text (Greek, symbols, emoji)'
    if connection.vendor != 'mysql':
        return _row(name, OK, 'Not MariaDB; nothing to check')
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() "
                       "AND TABLE_NAME LIKE 'staff\\_%%' AND TABLE_COLLATION NOT LIKE 'utf8mb4%%'")
        narrow_staff = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() "
                       "AND TABLE_COLLATION NOT LIKE 'utf8mb4%%'")
        narrow_all = cursor.fetchone()[0]
        cursor.execute('SELECT @@character_set_connection')
        charset = cursor.fetchone()[0]
    if narrow_staff:
        return _row(name, FAIL, '{0} Admin Console table{1} cannot store Greek letters, arrows or CJK'.format(
            narrow_staff, '' if narrow_staff == 1 else 's'), 'Run "manage.py migrate staff" (migration 0009).')
    if charset == 'utf8mb4' and narrow_all:
        # Emoji reach the database but comparing with a narrower column fails on them.
        return _row(name, FAIL, 'The connection is utf8mb4 but {0} table{1} are not; any search or sign-in '
                                'with an emoji fails'.format(narrow_all, '' if narrow_all == 1 else 's'),
                    'Convert every table to utf8mb4 first, or take the utf8mb4 charset out of settings.py.')
    if charset != 'utf8mb4':
        return _row(name, WARN, 'The database connection is {0}: emoji in a message cannot be saved'.format(charset),
                    'Needs every ELSA table converted to utf8mb4 and then the connection; '
                    'switching the connection alone breaks searches and sign-in.')
    return _row(name, OK, 'utf8mb4 tables and connection')


def run():
    rows = [_django(), _debug(), _stock_login(), _login_url(), _cookies(), _hsts()]
    rows.extend(_secrets())
    rows.extend(row for row in (_releases(), _audit(), _headers(), _members_without_passkeys(),
                                _background_python(), _alert_recipients(), _text_storage()) if row)
    return rows


def worst(rows):
    levels = [row['level'] for row in rows]
    return FAIL if FAIL in levels else WARN if WARN in levels else OK
