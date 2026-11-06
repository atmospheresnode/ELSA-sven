# -*- coding: utf-8 -*-
"""Account housekeeping on each request, after AuthenticationMiddleware:

- ends sessions retired by "sign out everywhere else"
- keeps the session's row in the device list current
- shows times in the browser's time zone (elsa_tz cookie from base-derk.html)
- adds the security headers (security_headers)
"""
from __future__ import unicode_literals

import zoneinfo
from urllib.parse import unquote

from django.contrib import messages
from django.contrib.auth import logout
from django.utils import timezone

from . import devices, security_headers
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
        # Inline <script> blocks carry this; see security_headers.
        request.csp_nonce = security_headers.new_nonce()
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
        # Staff "view as user". Applied last and skipped if the staff app isn't installed.
        from staff.hooks import view_as
        refusal = view_as(request)
        if refusal is not None:
            timezone.deactivate()
            return security_headers.apply(request, refusal)
        try:
            return security_headers.apply(request, self.get_response(request))
        finally:
            timezone.deactivate()
