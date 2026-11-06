# -*- coding: utf-8 -*-
"""
Per-request account housekeeping, after AuthenticationMiddleware:

- ends a session that "sign out everywhere else" has retired;
- keeps the session's row in the device list current;
- renders times in the browser's own time zone, which base-derk.html puts in
  the elsa_tz cookie, and keeps the account's copy current for emails sent
  when no browser is involved.

One small query per signed-in request (the profile's generation and zone).
"""
from __future__ import unicode_literals

import zoneinfo
from urllib.parse import unquote

from django.contrib import messages
from django.contrib.auth import logout
from django.utils import timezone

from . import devices
from .models import UserProfile


BROWSER_ZONE_COOKIE = 'elsa_tz'


def zone_for(name):
    """A ZoneInfo for a stored name, or None for blank or unknown names."""
    if not name or len(name) > 64:
        return None
    try:
        return zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return None


class AccountSessionMiddleware(object):

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        browser_zone = unquote(request.COOKIES.get(BROWSER_ZONE_COOKIE, ''))
        if zone_for(browser_zone) is None:
            browser_zone = ''
        user = getattr(request, 'user', None)
        if user is None or not user.is_authenticated:
            if browser_zone:
                timezone.activate(zone_for(browser_zone))
        else:
            profile = (UserProfile.objects.filter(user=user)
                       .values('session_generation', 'timezone').first() or {})
            if not devices.session_is_current(request, profile.get('session_generation')):
                logout(request)
                messages.info(request, "You were signed out because your account was signed "
                                       "out on all other devices.")
            else:
                devices.touch(request)
                if browser_zone and browser_zone != profile.get('timezone'):
                    # Travel, or the first visit since this was added.
                    UserProfile.objects.filter(user=user).update(timezone=browser_zone)
                zone = zone_for(browser_zone or profile.get('timezone'))
                if zone is not None:
                    timezone.activate(zone)
        try:
            return self.get_response(request)
        finally:
            timezone.deactivate()
