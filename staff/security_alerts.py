# -*- coding: utf-8 -*-
"""Emails the ELSA developers when something in the Admin Console looks wrong:

- admin mode showing up in another browser or network
- a burst of failed passkey taps on a staff account
- a burst of downloads or exports by one person
- someone starting "view as user"
- a console setting changed
- the audit log failing its tamper check

Each kind goes out at most once an hour per person. All of them are in the audit log too.
"""
from __future__ import unicode_literals

import logging

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import EmailMessage
from django.utils import timezone

from friends import throttle
from friends.devices import client_ip, device_label, user_agent

logger = logging.getLogger(__name__)

FROM = 'atm-elsa@nmsu.edu'
QUIET = 3600

FAILED_TAPS = 5            # in 15 minutes, on one staff account
FAILED_TAP_WINDOW = 15 * 60
DOWNLOAD_ALERT = 40        # downloads and exports in 10 minutes, by one person
DOWNLOAD_WINDOW = 10 * 60


def recipients():
    to = sorted({u.email for u in User.objects.filter(is_superuser=True, is_active=True) if u.email})
    if not to:
        to = sorted({address for _name, address in getattr(settings, 'ADMINS', ())})
    return to


def _where(request):
    if request is None:
        return 'on the server'
    return 'from {0} (address {1})'.format(device_label(user_agent(request)), client_ip(request) or 'unknown')


def send(kind, about, subject, lines, request=None):
    """Send one alert, unless the same one went out in the last hour."""
    if throttle.hit('security-alert-{0}-{1}'.format(kind, about), QUIET) > 1:
        return False
    from . import audit
    try:
        audit.record(request, 'security.alert', reason=subject, changes={'kind': kind, 'about': str(about)},
                     actor_name='' if request is not None else 'system')
    except Exception:
        logger.exception('could not record security alert %s', kind)
    to = recipients()
    if not to:
        logger.warning('security alert %s with nobody to tell: %s', kind, subject)
        return False
    body = '\n'.join(list(lines) + [
        '', 'When: {0:%B %d, %Y %I:%M %p %Z}'.format(timezone.localtime()),
        'Where: {0}'.format(_where(request)), '',
        'The audit log in the Admin Console has the details. This email is sent at most once an hour '
        'for the same thing.'])
    try:
        EmailMessage(subject='[ELSA security] ' + ' '.join(subject.split())[:150], body=body,
                     from_email=FROM, to=to).send()
    except Exception:
        logger.exception('security alert %s could not be sent', kind)
        return False
    return True


def admin_mode_moved(request):
    user = request.user
    send('moved', user.pk, 'Admin mode for {0} turned up in another browser or network'.format(user.username), [
        'The session of {0} was in the Admin Console and was then used from a different browser '
        'or network address.'.format(user.username),
        'ELSA ended admin mode on that session. If {0} did not just change networks or '
        'browsers, the session cookie may have been copied: ask them to sign out everywhere '
        'from their Account page.'.format(user.username),
    ], request)


def failed_passkey(request, user):
    """Called on every failed passkey tap. Only alerts for staff accounts, on a burst."""
    if user is None or not getattr(user, 'pk', None):
        return
    from . import roles
    if not roles.is_member(user):
        return
    failures = throttle.hit('security-taps-{0}'.format(user.pk), FAILED_TAP_WINDOW)
    if failures >= FAILED_TAPS:
        send('taps', user.pk, '{0} failed passkey taps on the staff account {1}'.format(failures, user.username), [
            'There were {0} failed passkey attempts on {1} within 15 minutes.'.format(failures, user.username),
            'If it was not them fumbling with a security key, someone may be trying to get in.',
        ], request)


def counted_download(request):
    """Count a download or export. False once the person is over the hard limit."""
    user = request.user
    count = throttle.hit('security-downloads-{0}'.format(user.pk), DOWNLOAD_WINDOW)
    if count >= DOWNLOAD_ALERT:
        send('downloads', user.pk, '{0} has downloaded or exported {1} things in 10 minutes'.format(
            user.username, count), [
            '{0} downloaded or exported {1} files or lists from the Admin Console within 10 minutes.'.format(
                user.username, count),
            'ELSA stops them at {0} until the 10 minutes pass. If this is not expected, check the '
            'audit log.'.format(DOWNLOAD_LIMIT),
        ], request)
    return count <= DOWNLOAD_LIMIT


DOWNLOAD_LIMIT = 80


def view_as_started(request, target):
    send('view-as-{0}'.format(target.pk), request.user.pk,
         '{0} is viewing ELSA as {1}'.format(request.user.username, target.username), [
             '{0} started "view as user" for {1}. It is read-only and ends in 15 minutes.'.format(
                 request.user.username, target.username),
             'This email is so a second person always knows; nothing needs doing if it was expected.',
         ], request)


def setting_changed(request, key, before, after):
    send('setting-{0}'.format(key), request.user.pk,
         '{0} changed the console setting "{1}"'.format(request.user.username, key), [
             '{0} changed "{1}" from {2} to {3}.'.format(request.user.username, key, before, after),
         ], request)


def audit_broken(problem):
    send('audit', 'chain', 'The Admin Console audit log failed its tamper check', [
        'The nightly check of the audit log found a problem:', '', '    ' + problem, '',
        'Entries in the audit log are sealed as they are written, so this means the log was '
        'changed outside ELSA (in the database, for example). Find out who had database access, '
        'and keep a copy of the database as it is now.',
    ])
