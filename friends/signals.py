# -*- coding: utf-8 -*-
"""Every completed sign-in and sign-out, however it happened, lands here."""
from __future__ import unicode_literals

import logging

from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver

from . import devices
from .models import AccountEvent


logger = logging.getLogger(__name__)

# Views set request.elsa_sign_in_method before login(); anything else (an
# admin's force login, a test) is described generically.
DEFAULT_METHOD = ''


@receiver(user_logged_in)
def on_sign_in(sender, request, user, **kwargs):
    if request is None or not hasattr(request, 'session'):
        return
    try:
        new_device = devices.track_sign_in(request, user, getattr(request, 'elsa_sign_in_method', DEFAULT_METHOD))
    except Exception:
        logger.exception('could not track the sign-in of %s', user.pk)
        return
    if new_device and user.email:
        # A notice that cannot be built (no host on a synthetic request) or
        # sent must never undo the sign-in that triggered it.
        try:
            from .views import _send_security_notice
            ip = devices.client_ip(request)
            _send_security_notice(
                request, user, [user.email],
                "ELSA: new sign-in to your account",
                "New sign-in to your account",
                "your ELSA account ({0}) was just signed in to from {1}{2}, which has not been "
                "used with it recently.".format(
                    user.username, devices.device_label(devices.user_agent(request)),
                    ' (IP address {0})'.format(ip) if ip else ''))
        except Exception:
            logger.exception('new-device notice for %s failed', user.pk)


@receiver(user_logged_out)
def on_sign_out(sender, request, user, **kwargs):
    if request is None or user is None:
        return
    devices.forget_session(request)
    devices.record(user, AccountEvent.SIGN_OUT, request)
