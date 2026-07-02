"""Test settings: in-memory SQLite, since the MariaDB user can't create test databases.

Usage: python3 manage.py test assistant --settings=assistant.test_settings
"""
from elsa.settings import *  # noqa: F401,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

# Keep tests hermetic
GEMINI_API_KEY = ''
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
