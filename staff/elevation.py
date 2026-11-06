# -*- coding: utf-8 -*-
"""Admin mode, a short elevation on top of a normal ELSA session.

Entering needs a recent passkey tap. It ends after a quiet half hour or a working day.
Dangerous actions ask for another tap, good for one use.

Stored in the session and tied to the user id, browser and network. If the session shows
up somewhere else it has no admin mode and the developers get an alert.
"""
from __future__ import unicode_literals

import hashlib
import secrets
from datetime import timedelta

from django.utils import timezone

from friends import throttle


PASSKEY_KEY = 'staff_passkey'
ELEVATED_KEY = 'staff_elevated'
CONFIRM_KEY = 'staff_confirm'

# A tap this recent can enter admin mode without another one.
ENTRY_WINDOW = timedelta(minutes=5)
IDLE_LIMIT = timedelta(minutes=30)
ABSOLUTE_LIMIT = timedelta(hours=8)
CONFIRM_WINDOW = timedelta(minutes=2)


def _now():
    return timezone.now().timestamp()


def binding_for(user_agent, address):
    """A short fingerprint of a browser and the address it connects from."""
    raw = '{0}|{1}'.format((user_agent or '')[:512], address or '')
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]


def binding(request):
    # Use REMOTE_ADDR, not X-Forwarded-For, since the browser can write that header.
    return binding_for(request.META.get('HTTP_USER_AGENT', ''), request.META.get('REMOTE_ADDR', ''))


def note_passkey(request, user):
    """Records that ``user`` has just proved themselves with a passkey."""
    request.session[PASSKEY_KEY] = {'uid': user.pk, 'at': _now(), 'bind': binding(request)}


def fresh_passkey(request, window=ENTRY_WINDOW):
    stamp = request.session.get(PASSKEY_KEY) or {}
    if stamp.get('uid') != getattr(request.user, 'pk', None):
        return False
    if stamp.get('bind') != binding(request):
        return False
    try:
        return _now() - float(stamp.get('at')) <= window.total_seconds()
    except (TypeError, ValueError):
        return False


def elevate(request):
    """Enter admin mode. Refused unless a passkey tap is fresh. Rotates the session key."""
    if not fresh_passkey(request):
        return False
    now = _now()
    old_key = request.session.session_key
    request.session.cycle_key()
    if old_key:
        # The account page lists sessions by key; keep this one listed.
        from friends.models import UserSession
        UserSession.objects.filter(session_key=old_key).update(
            session_key=request.session.session_key)
    request.session[ELEVATED_KEY] = {'uid': request.user.pk, 'since': now, 'seen': now,
                                     'bind': binding(request)}
    return True


def end(request):
    for key in (ELEVATED_KEY, CONFIRM_KEY):
        request.session.pop(key, None)


def status(request):
    """Returns ('active', state), ('expired', None), ('moved', None) or ('none', None).

    An ended elevation is cleared here so it's only reported once.
    """
    state = request.session.get(ELEVATED_KEY)
    if not state:
        return 'none', None
    try:
        since, seen = float(state['since']), float(state['seen'])
        uid = state['uid']
    except (KeyError, TypeError, ValueError):
        end(request)
        return 'none', None

    now = _now()
    if (uid != getattr(request.user, 'pk', None)
            or now - seen > IDLE_LIMIT.total_seconds()
            or now - since > ABSOLUTE_LIMIT.total_seconds()):
        end(request)
        return 'expired', None
    if state.get('bind') != binding(request):
        end(request)
        request.session.pop(PASSKEY_KEY, None)
        from . import security_alerts
        security_alerts.admin_mode_moved(request)
        return 'moved', None
    return 'active', state


def is_elevated(request):
    return status(request)[0] == 'active'


def touch(request):
    """Restarts the idle clock.  Called on every console request."""
    state = request.session.get(ELEVATED_KEY)
    if state:
        state = dict(state, seen=_now())
        request.session[ELEVATED_KEY] = state


def expires_at(request):
    """When admin mode ends if nothing else happens, as an aware datetime."""
    state = request.session.get(ELEVATED_KEY)
    if not state:
        return None
    try:
        ends = min(float(state['seen']) + IDLE_LIMIT.total_seconds(),
                   float(state['since']) + ABSOLUTE_LIMIT.total_seconds())
    except (KeyError, TypeError, ValueError):
        return None
    return timezone.datetime.fromtimestamp(ends, tz=timezone.utc)


def expiry_state(request):
    """(seconds left, True if the 8-hour limit is what ends it) or None."""
    state = request.session.get(ELEVATED_KEY)
    if not state:
        return None
    try:
        idle_end = float(state['seen']) + IDLE_LIMIT.total_seconds()
        hard_end = float(state['since']) + ABSOLUTE_LIMIT.total_seconds()
    except (KeyError, TypeError, ValueError):
        return None
    return max(0, min(idle_end, hard_end) - _now()), hard_end <= idle_end


def scope_of(request):
    """What a confirmation is for: the form's address plus its action."""
    return '{0}#{1}'.format(request.path, request.POST.get('action', ''))[:300]


def issue_confirmation(request, scope):
    """One tap for one action on one form (``scope``), good for two minutes."""
    request.session[CONFIRM_KEY] = {'uid': request.user.pk, 'at': _now(),
                                    'nonce': secrets.token_hex(16), 'scope': scope,
                                    'bind': binding(request)}


def spend_confirmation(request):
    """True once per tap. The nonce is also counted in the database to catch two requests at once."""
    stamp = request.session.pop(CONFIRM_KEY, None) or {}
    if stamp.get('uid') != getattr(request.user, 'pk', None) or not stamp.get('nonce'):
        return False
    if stamp.get('scope') != scope_of(request) or stamp.get('bind') != binding(request):
        return False
    try:
        fresh = _now() - float(stamp.get('at')) <= CONFIRM_WINDOW.total_seconds()
    except (TypeError, ValueError):
        return False
    window = int(CONFIRM_WINDOW.total_seconds()) + 60
    return fresh and throttle.hit('staff-confirm-' + stamp['nonce'], window) == 1
