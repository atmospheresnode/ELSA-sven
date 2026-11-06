# -*- coding: utf-8 -*-
"""Remembers where someone was going when they had to sign in.

There are several ways to sign in, so the page is kept in the session and each route
picks it up from there.
"""
from __future__ import unicode_literals

from django.urls import get_script_prefix, reverse
from django.utils.http import url_has_allowed_host_and_scheme

SESSION_KEY = 'login_next'
LONGEST = 500


def _safe(request, target):
    """Only allow pages on this ELSA, and not the sign-in pages."""
    if not target or len(target) > LONGEST or not target.startswith(get_script_prefix()):
        return False
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()},
                                           require_https=request.is_secure()):
        return False
    path = target.split('?', 1)[0]
    return path not in (reverse('main:index'), reverse('friends:login'), reverse('friends:logout'))


def remember(request, target):
    """Keeps ``target`` for after sign-in.  Returns whether it was kept."""
    if _safe(request, target):
        request.session[SESSION_KEY] = target
        return True
    return False


def take(request):
    """Returns the saved page once, or '' if there isn't one or it isn't safe."""
    target = request.session.pop(SESSION_KEY, '')
    return target if _safe(request, target) else ''
