# -*- coding: utf-8 -*-
"""Guards on the Admin Console views.

console_view: signed in, active, a member, in admin mode, with the named permission.
member_view: the first three only. staff/urls.py wraps any view with neither in console_view.
"""
from __future__ import unicode_literals

from functools import wraps

from django.contrib import messages
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import urlencode

from . import elevation, roles


def _came_from(request):
    """Console page a form was sent from, to return there after the passkey step."""
    from urllib.parse import urlparse
    from django.utils.http import url_has_allowed_host_and_scheme
    referer = request.META.get('HTTP_REFERER', '')
    if referer and url_has_allowed_host_and_scheme(referer, allowed_hosts={request.get_host()},
                                                   require_https=request.is_secure()):
        path = urlparse(referer).path
        if path.startswith(reverse('staff:home')):
            return path
    return reverse('staff:today')


def _harden(response):
    # Admin pages have personal data, so keep them out of caches.
    response['Cache-Control'] = 'no-store'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response


def _wants_json(request):
    return (request.headers.get('Accept', '').startswith('application/json')
            or request.content_type == 'application/json')


def _refuse_member(request):
    if not request.user.is_authenticated:
        if _wants_json(request):
            return JsonResponse({'error': 'Please sign in again.'}, status=401)
        messages.info(request, 'Sign in to ELSA first.')
        if request.method == 'GET':
            # A link from an email leads back here after signing in.
            from friends import next_page
            next_page.remember(request, request.get_full_path())
        return HttpResponseRedirect(reverse('main:index'))
    if not roles.is_member(request.user):
        if _wants_json(request):
            return JsonResponse({'error': 'Not allowed.'}, status=403)
        return redirect('main:restricted_access')
    return None


def member_view(view):
    @wraps(view)
    def inner(request, *args, **kwargs):
        refusal = _refuse_member(request)
        if refusal is not None:
            return refusal
        return _harden(view(request, *args, **kwargs))
    inner.staff_guarded = True
    return inner


def console_view(codename=None, touch=True):
    """Guard a console page.

    ``codename`` is the permission needed beyond view_console. Use ``touch=False`` for
    background polling so it doesn't keep admin mode alive.
    """
    def decorator(view):
        @wraps(view)
        def inner(request, *args, **kwargs):
            refusal = _refuse_member(request)
            if refusal is not None:
                return refusal

            state, _ = elevation.status(request)
            if state != 'active':
                if _wants_json(request):
                    return JsonResponse({'error': 'Admin mode has ended. Reload the page.'},
                                        status=401)
                if state == 'expired':
                    messages.info(request, 'Admin mode ended. Confirm with your passkey to '
                                           'continue.')
                elif state == 'moved':
                    messages.warning(request, 'Admin mode ended because your browser or network '
                                              'changed. Confirm with your passkey to continue.')
                # Only come back to a GET. Half-sent forms aren't replayed after the passkey step.
                back = request.get_full_path() if request.method == 'GET' else _came_from(request)
                return HttpResponseRedirect('{0}?{1}'.format(reverse('staff:verify'),
                                                             urlencode({'next': back})))

            if touch:
                elevation.touch(request)
                from . import sessions
                sessions.touched(request)

            if codename and not roles.has(request.user, codename):
                if _wants_json(request):
                    return JsonResponse({'error': 'Your role does not allow this.'}, status=403)
                return _harden(render(request, 'staff/forbidden.html', status=403))

            return _harden(view(request, *args, **kwargs))
        inner.staff_guarded = True
        inner.staff_perm = codename
        return inner
    return decorator


LOOKUPS_PER_WINDOW = 400
LOOKUP_WINDOW = 10 * 60


def limited(kind, when=None, json=False):
    """Limit how fast one person can pull data out of the console.

    ``kind`` is 'download' (counted, alerted on, capped) or 'lookup' (capped). ``when`` limits
    which requests count. ``json`` gives page scripts a JSON refusal. Goes inside console_view.
    """
    def decorator(view):
        @wraps(view)
        def inner(request, *args, **kwargs):
            if when is None or when(request):
                if kind == 'download':
                    from . import security_alerts
                    allowed = security_alerts.counted_download(request)
                else:
                    from friends import throttle
                    allowed = throttle.hit('staff-lookup-{0}'.format(request.user.pk),
                                           LOOKUP_WINDOW) <= LOOKUPS_PER_WINDOW
                if not allowed:
                    message = 'That is a lot of {0} in a few minutes. Wait ten minutes and try again.'.format(
                        'downloads' if kind == 'download' else 'searches')
                    if json or _wants_json(request):
                        return JsonResponse({'error': message}, status=429)
                    response = render(request, 'staff/slow_down.html', {'message': message}, status=429)
                    response['Retry-After'] = '600'
                    return response
            return view(request, *args, **kwargs)
        return inner
    return decorator
