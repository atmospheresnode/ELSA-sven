# -*- coding: utf-8 -*-
"""
Closing an account at its owner's request.

The User row is kept, deactivated and stripped of personal details, rather than
deleted.  Bundle.user cascades, so deleting the row would delete every bundle,
and the archive keeps bundles the team still needs.  Nothing is moved on disk
either: ARCHIVE_DIR/<username>/ is assumed by bundle paths, downloads, label
regeneration and stored file names, so moving it would break the bundles for
the team as well.

Instead the account is made easy to find:

- UserProfile.closed_at, with an admin filter on both profiles and bundles;
- ARCHIVE_DIR/_closed_accounts/<username>, a relative symlink to the folder,
  so one directory lists every closed account's data;
- ACCOUNT_CLOSED.txt inside the folder, for anyone browsing the server.
"""
from __future__ import unicode_literals

import logging
import os

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from build.models import Bundle
from .models import UserProfile


logger = logging.getLogger(__name__)

# Not a possible username: friends.forms reserves it.
CLOSED_INDEX_DIRNAME = '_closed_accounts'
MARKER_FILENAME = 'ACCOUNT_CLOSED.txt'


def close_account(user):
    """
    Closes ``user``.  Returns a dict describing what was closed, for the
    notices.  The database part is atomic; the disk markers are best effort
    and never undo a closure.
    """
    now = timezone.now()
    record = {
        'username': user.username,
        'email': user.email,
        'full_name': user.get_full_name(),
        'closed_at': now,
        'bundles': list(Bundle.objects.filter(user=user).order_by('name')
                        .values_list('name', flat=True)),
    }

    with transaction.atomic():
        user.is_active = False
        # Also changes the session hash, which signs out every other device.
        user.set_unusable_password()
        user.first_name = ''
        user.last_name = ''
        user.email = ''
        user.save(update_fields=['is_active', 'password', 'first_name', 'last_name', 'email'])

        user.passkeys.all().delete()
        user.trusted_devices.all().delete()
        user.tracked_sessions.all().delete()

        UserProfile.objects.filter(user=user).update(
            closed_at=now,
            otp_hash=None, otp_created_at=None, otp_attempts=0, otp_locked_until=None,
            pending_email=None, pending_email_hash=None, pending_email_created_at=None,
            pending_email_attempts=0)

    record['archive_path'] = _mark_archive(record)
    return record


def _mark_archive(record):
    """Writes the marker and the index link.  Returns the folder, or ''."""
    from .views import archive_dir_for

    try:
        folder = archive_dir_for(record['username'])
    except ValueError:
        return ''
    if not os.path.isdir(folder):
        return ''

    try:
        with open(os.path.join(folder, MARKER_FILENAME), 'w', encoding='utf-8') as handle:
            handle.write(
                'This ELSA account was closed by its owner on {0:%Y-%m-%d %H:%M %Z}.\n'
                'Username: {1}\n\n'
                'Its bundles were kept for the ELSA team and are not visible to anyone\n'
                'else in ELSA. The owner\'s name and email were removed from the database;\n'
                'they are in the closure notice emailed to the team.\n\n'
                'Bundles retained ({2}):\n{3}\n'.format(
                    timezone.localtime(record['closed_at']), record['username'],
                    len(record['bundles']),
                    ''.join('  - {0}\n'.format(name) for name in record['bundles']) or '  (none)\n'))
    except OSError:
        logger.exception('could not write closure marker in %s', folder)

    index = os.path.join(settings.ARCHIVE_DIR, CLOSED_INDEX_DIRNAME)
    link = os.path.join(index, record['username'])
    try:
        os.makedirs(index, exist_ok=True)
        if not os.path.lexists(link):
            # Relative, so it survives the archive being moved or restored.
            os.symlink(os.path.join(os.pardir, record['username']), link)
    except OSError:
        logger.exception('could not link %s into %s', folder, index)

    return folder
