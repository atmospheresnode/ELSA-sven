# -*- coding: utf-8 -*-
"""Email to a group of users. Sent in batches over one connection.

Runs in its own process since the web server would cut off a large group.
"""
from __future__ import unicode_literals

import logging
import os
import secrets
import subprocess  # nosec B404: launch() only, the same detached pattern as validate
import sys

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import get_connection
from django.db.models import F, Q
from django.utils import timezone

from friends.mail import build_branded

from . import roles
from .models import Broadcast


logger = logging.getLogger(__name__)

BATCH = 50


def dev_limited():
    """On a dev server only staff get a broadcast unless ELSA_DEV_BROADCAST_OK is set.

    Dev sends real email and has real users in its database.
    """
    return settings.DEBUG and not getattr(settings, 'ELSA_DEV_BROADCAST_OK', False)


def audience(segment):
    """Active accounts with an address, in the chosen group, one row each."""
    if dev_limited():
        segment = 'staff'
    people = User.objects.filter(is_active=True).exclude(email='')
    if segment == 'external':
        people = people.filter(bundle__bundle_type='External')
    elif segment == 'archive':
        people = people.filter(bundle__bundle_type='Archive')
    elif segment == 'submitted':
        people = people.filter(bundle__submitted_at__isnull=False)
    elif segment == 'staff':
        people = people.filter(Q(is_superuser=True) | Q(groups__name__in=roles.GROUP_NAMES))
    elif segment != 'all':
        return User.objects.none()
    # People can turn off news and updates (Account page or the link in each email).
    people = people.exclude(elsa_notification_preference__news=False)
    return people.distinct().order_by('pk')


def opted_out():
    """Active accounts that turned news and updates off."""
    return User.objects.filter(is_active=True, elsa_notification_preference__news=False).count()


def _message(broadcast, user, test=False):
    from . import comms
    subject = broadcast.subject if not test else '[Test] ' + broadcast.subject
    unsubscribe = comms.unsubscribe_url(user)
    message = build_branded([user.email], subject, 'staff/email/to_user', {
        'headline': broadcast.subject,
        'lead': 'Hello {0},'.format(user.first_name or user.username),
        'text': broadcast.body,
        'signature': 'The ELSA team\nPDS Atmospheres Node',
        'link': '', 'reference': '', 'email_reply': False,
        'unsubscribe_url': unsubscribe,
    })
    # RFC 8058 one-click unsubscribe, so mail programs show their own Unsubscribe button.
    message.extra_headers = dict(message.extra_headers or {}, **{
        'List-Unsubscribe': '<{0}>'.format(unsubscribe),
        'List-Unsubscribe-Post': 'List-Unsubscribe=One-Click'})
    return message


def send_one(broadcast, user, test=False):
    _message(broadcast, user, test=test).send()


def launch(broadcast):
    """Start `manage.py send_broadcast <id>` as its own process, same as PDS validate.

    Returns False if it couldn't start, so the broadcast can be resumed right away.
    """
    command = [sys.executable, os.path.join(settings.BASE_DIR, 'manage.py'), 'send_broadcast',
               str(int(broadcast.pk))]
    environment = os.environ.copy()
    settings_module = os.environ.get('DJANGO_SETTINGS_MODULE') or settings.SETTINGS_MODULE
    if settings_module:
        # The child must open the same database as the request that queued it.
        environment['DJANGO_SETTINGS_MODULE'] = settings_module
    try:
        subprocess.Popen(command, cwd=settings.BASE_DIR, env=environment,  # nosec B603: fixed argv, no shell
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        logger.exception('broadcast %s: could not start the sender', broadcast.pk)
        Broadcast.objects.filter(pk=broadcast.pk, status=Broadcast.SENDING).update(heartbeat_at=None)
        return False
    return True


def _claim(pk):
    """Take the lease on a broadcast, or return None if a live sender already has it."""
    token = secrets.token_hex(16)
    now = timezone.now()
    claimed = (Broadcast.objects.filter(pk=pk, status=Broadcast.SENDING)
               .filter(Q(worker='') | Q(heartbeat_at__isnull=True)
                       | Q(heartbeat_at__lt=now - Broadcast.STALL_AFTER))
               .update(worker=token, heartbeat_at=now))
    return token if claimed else None


def _advance(pk, token, last_pk, sent, failed):
    """Move the cursor past ``last_pk``. False if another sender took over."""
    return bool(Broadcast.objects.filter(pk=pk, worker=token).update(
        cursor=last_pk, recipient_count=F('recipient_count') + sent,
        failed_count=F('failed_count') + failed, heartbeat_at=timezone.now()))


def run(pk):
    """Send to everyone after the cursor, in batches, moving the cursor after each message.

    Returns (sent, failed), or None if it didn't get the lease.
    """
    token = _claim(pk)
    if token is None:
        return None
    broadcast = Broadcast.objects.get(pk=pk)
    people = list(audience(broadcast.segment).filter(pk__gt=broadcast.cursor))
    sent = failed = 0
    for start in range(0, len(people), BATCH):
        batch = people[start:start + BATCH]
        try:
            connection = get_connection()
            connection.open()
        except Exception:
            logger.exception('broadcast %s: could not open a mail connection', pk)
            failed += len(batch)
            if not _advance(pk, token, batch[-1].pk, 0, len(batch)):
                return sent, failed
            continue
        try:
            for user in batch:
                ok = True
                try:
                    message = _message(broadcast, user)
                    message.connection = connection
                    message.send()
                except Exception:
                    logger.exception('broadcast %s to user %s failed', pk, user.pk)
                    ok = False
                sent, failed = sent + ok, failed + (not ok)
                if not _advance(pk, token, user.pk, int(ok), int(not ok)):
                    logger.warning('broadcast %s: another sender took over; stopping', pk)
                    return sent, failed
        finally:
            try:
                connection.close()
            except Exception:
                pass
    finished = Broadcast.objects.filter(pk=pk, worker=token).update(
        status=Broadcast.SENT, sent_at=timezone.now(), worker='', heartbeat_at=timezone.now())
    if finished:
        broadcast.refresh_from_db()
        from . import audit
        audit.record(None, 'broadcast.done', actor_name='system', changes={
            'id': pk, 'subject': broadcast.subject, 'sent': broadcast.recipient_count,
            'failed': broadcast.failed_count})
    return sent, failed
