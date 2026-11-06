# -*- coding: utf-8 -*-
"""Browsers in admin mode: who's in the console, and an email for a new browser."""
from __future__ import unicode_literals

import logging
from datetime import timedelta

from django.core.mail import EmailMessage
from django.utils import timezone

from friends.devices import client_ip, device_label, user_agent

from . import elevation
from .models import StaffSession


logger = logging.getLogger(__name__)

TOUCH_EVERY = timedelta(minutes=1)


def started(request):
    """Called when admin mode opens.  Records the browser; warns on a new one."""
    key = request.session.session_key
    if not key:
        return
    agent = user_agent(request)
    label = device_label(agent)
    previous = list(StaffSession.objects.filter(user=request.user)
                    .exclude(session_key=key).values_list('user_agent', flat=True)[:200])
    StaffSession.objects.update_or_create(session_key=key, defaults={
        'user': request.user, 'ip': client_ip(request), 'user_agent': agent,
        'elevated_at': timezone.now(), 'last_seen_at': timezone.now(), 'ended_at': None})
    if previous and label not in {device_label(a) for a in previous} and request.user.email:
        try:
            EmailMessage(
                subject='The Admin Console was opened on a new browser',
                body=('Your account {0} opened the Admin Console on {1} (address {2}) at {3:%B %d, %Y %I:%M %p %Z}.\n\n'
                      'If this was you, there is nothing to do. If not, sign out everywhere from your '
                      'Account page and tell the ELSA developers.\n').format(
                          request.user.username, label, client_ip(request) or 'unknown',
                          timezone.localtime()),
                from_email='atm-elsa@nmsu.edu', to=[request.user.email]).send()
        except Exception:
            logger.exception('new-browser notice for %s failed', request.user.username)


def touched(request):
    key = request.session.session_key
    if not key:
        return
    cutoff = timezone.now() - TOUCH_EVERY
    StaffSession.objects.filter(session_key=key, last_seen_at__lt=cutoff).update(last_seen_at=timezone.now())


def ended(request):
    key = request.session.session_key
    if key:
        StaffSession.objects.filter(session_key=key).update(ended_at=timezone.now())


def active_now():
    """Admin-mode sessions seen within the idle limit and not ended."""
    since = timezone.now() - elevation.IDLE_LIMIT
    return (StaffSession.objects.filter(ended_at__isnull=True, last_seen_at__gte=since)
            .select_related('user').order_by('-last_seen_at'))
