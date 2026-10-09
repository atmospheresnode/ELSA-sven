# -*- coding: utf-8 -*-
"""
Where an account is signed in, what has happened to it, and which browsers
it trusts.

- UserSession: one row per signed-in browser, so the owner can see them and
  sign any of them out.
- AccountEvent: the security log on the account page.
- TrustedDevice: "remember this device", which lets a browser skip the emailed
  code after a correct password for REMEMBER_DAYS.

Everything here is best effort around the request it serves: a failure to log
an event never blocks a sign-in.
"""
from __future__ import unicode_literals

import hashlib
import logging
import re
import secrets
from datetime import timedelta
from importlib import import_module

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from .models import AccountEvent, TrustedDevice, UserProfile, UserSession


logger = logging.getLogger(__name__)

GENERATION_KEY = 'elsa_session_generation'
REMEMBER_COOKIE = 'elsa_trusted_device'
REMEMBER_DAYS = 30
SEEN_EVERY = timedelta(minutes=5)
NEW_DEVICE_LOOKBACK = timedelta(days=180)
EVENTS_SHOWN = 20


# ---------------------------------------------------------------------------
# Request details
# ---------------------------------------------------------------------------

def client_ip(request):
    from .views import _client_ip
    ip = _client_ip(request)
    return ip if ip and ip != 'unknown' else None


def user_agent(request):
    return (request.META.get('HTTP_USER_AGENT') or '')[:300]


_BROWSERS = (
    ('Edge', r'Edg(e|A|iOS)?/'),
    ('Opera', r'OPR/|Opera'),
    ('Firefox', r'Firefox/|FxiOS/'),
    ('Chrome', r'Chrome/|CriOS/'),
    ('Safari', r'Safari/'),
)
_SYSTEMS = (
    ('iPhone', r'iPhone'),
    ('iPad', r'iPad'),
    ('Android', r'Android'),
    ('ChromeOS', r'CrOS'),
    ('Windows', r'Windows'),
    ('macOS', r'Macintosh|Mac OS X'),
    ('Linux', r'Linux'),
)


def device_label(agent):
    """'Chrome on macOS' from a User-Agent string, or 'Unknown browser'."""
    agent = agent or ''
    browser = next((name for name, pattern in _BROWSERS if re.search(pattern, agent)), None)
    system = next((name for name, pattern in _SYSTEMS if re.search(pattern, agent)), None)
    if browser and system:
        return '{0} on {1}'.format(browser, system)
    return browser or system or 'Unknown browser'


# ---------------------------------------------------------------------------
# The activity log
# ---------------------------------------------------------------------------

def record(user, kind, request=None, detail=''):
    try:
        AccountEvent.objects.create(
            user=user, kind=kind, detail=(detail or '')[:255],
            ip=client_ip(request) if request is not None else None,
            user_agent=user_agent(request) if request is not None else '')
    except Exception:
        logger.exception('could not record %s for %s', kind, getattr(user, 'pk', None))


def recent_events(user):
    events = list(user.account_events.all()[:EVENTS_SHOWN])
    for event in events:
        event.label = device_label(event.user_agent) if event.user_agent else ''
    return events


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def _session_store():
    return import_module(settings.SESSION_ENGINE).SessionStore


def _generation(user):
    return (UserProfile.objects.filter(user=user)
            .values_list('session_generation', flat=True).first()) or 0


def track_sign_in(request, user, method):
    """Called once a sign-in has completed.  Returns True for a new device."""
    session = request.session
    if not session.session_key:
        session.save()
    session[GENERATION_KEY] = _generation(user)

    agent = user_agent(request)
    label = device_label(agent)
    since = timezone.now() - NEW_DEVICE_LOOKBACK
    known = any(device_label(previous) == label for previous in
                AccountEvent.objects.filter(user=user, kind=AccountEvent.SIGN_IN, created_at__gte=since)
                .values_list('user_agent', flat=True)[:200])
    first_ever = not AccountEvent.objects.filter(user=user, kind=AccountEvent.SIGN_IN).exists()

    UserSession.objects.update_or_create(
        session_key=session.session_key,
        defaults={'user': user, 'ip': client_ip(request), 'user_agent': agent,
                  'last_seen_at': timezone.now()})
    record(user, AccountEvent.SIGN_IN, request, detail=method)
    return not known and not first_ever


def touch(request):
    """Keeps the current session's row current; at most one write per SEEN_EVERY."""
    key = request.session.session_key
    if not key:
        return
    now = timezone.now()
    marker = request.session.get('elsa_seen_at')
    if marker and now.timestamp() - marker < SEEN_EVERY.total_seconds():
        return
    request.session['elsa_seen_at'] = now.timestamp()
    updated = UserSession.objects.filter(session_key=key, user=request.user).update(
        last_seen_at=now, ip=client_ip(request))
    if not updated:
        # A session from before tracking began: start tracking it now.
        UserSession.objects.update_or_create(
            session_key=key, defaults={'user': request.user, 'ip': client_ip(request),
                                       'user_agent': user_agent(request), 'last_seen_at': now})


def session_is_current(request, generation):
    """False if "sign out everywhere else" has ended this session."""
    return request.session.get(GENERATION_KEY, 0) >= (generation or 0)


def active_sessions(request):
    """The account's tracked sessions that still exist, current one first."""
    store = _session_store()
    rows = []
    for row in UserSession.objects.filter(user=request.user):
        if row.session_key != request.session.session_key and not store().exists(row.session_key):
            row.delete()
            continue
        row.is_current = row.session_key == request.session.session_key
        row.label = device_label(row.user_agent)
        rows.append(row)
    rows.sort(key=lambda r: (not r.is_current, -r.last_seen_at.timestamp()))
    return rows


def end_session(user, session_key):
    store = _session_store()
    store(session_key).delete()
    UserSession.objects.filter(user=user, session_key=session_key).delete()


def end_other_sessions(request):
    """Signs out every other browser, tracked or not.  Returns how many were listed."""
    user = request.user
    UserProfile.objects.filter(user=user).update(session_generation=F('session_generation') + 1)
    request.session[GENERATION_KEY] = _generation(user)

    others = UserSession.objects.filter(user=user).exclude(session_key=request.session.session_key)
    count = others.count()
    store = _session_store()
    for key in list(others.values_list('session_key', flat=True)):
        store(key).delete()
    others.delete()
    return count


def forget_session(request):
    """On sign-out: the row goes with the session."""
    key = getattr(request, 'session', None) and request.session.session_key
    if key:
        UserSession.objects.filter(session_key=key).delete()


# ---------------------------------------------------------------------------
# Remembered devices
# ---------------------------------------------------------------------------

def _hash(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def remember_device(request, response, user):
    token = secrets.token_urlsafe(32)
    TrustedDevice.objects.create(
        user=user, token_hash=_hash(token), user_agent=user_agent(request),
        expires_at=timezone.now() + timedelta(days=REMEMBER_DAYS))
    response.set_signed_cookie(
        REMEMBER_COOKIE, '{0}:{1}'.format(user.pk, token), salt=REMEMBER_COOKIE,
        max_age=REMEMBER_DAYS * 86400, httponly=True, samesite='Lax',
        secure=getattr(settings, 'SESSION_COOKIE_SECURE', False))
    record(user, AccountEvent.DEVICE_REMEMBERED, request, detail=device_label(user_agent(request)))
    return response


def trusted_device_for(request, user):
    """The TrustedDevice this browser proves, for this user, or None."""
    try:
        raw = request.get_signed_cookie(REMEMBER_COOKIE, default=None, salt=REMEMBER_COOKIE,
                                        max_age=REMEMBER_DAYS * 86400)
    except Exception:
        return None
    if not raw or ':' not in raw:
        return None
    user_pk, token = raw.split(':', 1)
    if user_pk != str(user.pk):
        return None
    device = TrustedDevice.objects.filter(user=user, token_hash=_hash(token),
                                          expires_at__gt=timezone.now()).first()
    if device is not None:
        TrustedDevice.objects.filter(pk=device.pk).update(last_used_at=timezone.now())
    return device


def current_trusted_device(request):
    if not request.user.is_authenticated:
        return None
    raw = None
    try:
        raw = request.get_signed_cookie(REMEMBER_COOKIE, default=None, salt=REMEMBER_COOKIE)
    except Exception:
        pass
    if not raw or ':' not in raw:
        return None
    return TrustedDevice.objects.filter(user=request.user, token_hash=_hash(raw.split(':', 1)[1])).first()


def trusted_devices(request):
    current = current_trusted_device(request)
    devices = list(TrustedDevice.objects.filter(user=request.user, expires_at__gt=timezone.now()))
    for device in devices:
        device.label = device_label(device.user_agent)
        device.is_current = current is not None and device.pk == current.pk
    return devices


def forget_all_devices(user):
    TrustedDevice.objects.filter(user=user).delete()


def prune(now=None):
    """Housekeeping for purge_unverified_accounts: expired trust, year-old events."""
    now = now or timezone.now()
    expired, _ = TrustedDevice.objects.filter(expires_at__lte=now).delete()
    old, _ = AccountEvent.objects.filter(created_at__lt=now - timedelta(days=365)).delete()
    return expired, old
