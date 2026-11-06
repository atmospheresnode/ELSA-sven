# -*- coding: utf-8 -*-
""""View as user": staff see a few pages as one user would, for support.

Read-only: GET and HEAD only, on an allowlist of pages. Everything else is refused.
15 minutes at most, needs a passkey tap, and is audited. The switch happens in
friends.middleware.AccountSessionMiddleware.
"""
from __future__ import unicode_literals

from datetime import timedelta

from django.contrib.auth.models import User
from django.http import HttpResponseForbidden
from django.template.loader import render_to_string
from django.urls import Resolver404, resolve
from django.utils import timezone

SESSION_KEY = 'staff_view_as'
LIMIT = timedelta(minutes=15)

ALLOWED = {
    ('friends', 'bundle_hub'),
    ('friends', 'useraccount'),
    ('build', 'bundle'),
    ('staff_public', 'requests'),
    ('staff_public', 'request'),
    ('staff_public', 'notifications'),
}
# Always run as the staff member, never refused: the ways out.
EXITS = {('staff_public', 'view_as_stop'), ('staff', 'leave'), ('friends', 'logout')}


def begin(request, target):
    request.session[SESSION_KEY] = {'staff': request.user.pk, 'target': target.pk,
                                    'until': (timezone.now() + LIMIT).timestamp()}


def end(request):
    return request.session.pop(SESSION_KEY, None)


def state(request):
    data = request.session.get(SESSION_KEY)
    if not data:
        return None
    from . import elevation, roles
    # Checked on every request, so losing the role or admin mode ends this right away.
    if (data.get('staff') != getattr(request.user, 'pk', None)
            or timezone.now().timestamp() > data.get('until', 0)
            or not roles.has(request.user, roles.MANAGE_ACCOUNTS)
            or not elevation.is_elevated(request)):
        request.session.pop(SESSION_KEY, None)
        return None
    return data


def apply(request):
    """None to carry on, or a response to send instead. Swaps request.user on allowed pages."""
    if not getattr(request, 'user', None) or not request.user.is_authenticated:
        return None
    data = state(request)
    if data is None:
        return None
    try:
        match = resolve(request.path_info)
        name = (match.namespace, match.url_name)
    except Resolver404:
        name = (None, None)
    if name in EXITS:
        return None
    target = User.objects.filter(pk=data['target'], is_active=True).first()
    if target is None:
        end(request)
        return None
    request.staff_viewing_as = {'target': target, 'staff': request.user,
                                'until': timezone.datetime.fromtimestamp(data['until'], tz=timezone.utc)}
    if request.method not in ('GET', 'HEAD') or name not in ALLOWED:
        return HttpResponseForbidden(render_to_string('staff/view_as_blocked.html', {
            'target': target, 'request': request}, request=request))
    # Every page seen, recorded against the staff member before the switch.
    from . import audit
    audit.record(request, 'user.view_as_page', target, changes={'path': request.get_full_path()[:300]})
    request.user = target
    request._cached_user = target
    return None
