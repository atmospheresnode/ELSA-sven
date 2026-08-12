# Settings for running the test suite locally.
#
# The MariaDB account ELSA runs under cannot CREATE the `test_<db>` database that Django's test
# runner wants, which makes the suite unrunnable against the normal settings. This module keeps
# everything else intact and swaps in an in-memory SQLite database for tests only.
#
# It lives at the repo root rather than next to settings.py because /elsa/* is gitignored.
#
# Usage: python manage.py test build --settings=test_settings

from elsa.settings import *  # noqa: F401,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
        'TEST': {'NAME': ':memory:'},
    }
}

# Tests must never send mail or share a cache with the running site.
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'elsa-test-cache',
    }
}

PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
