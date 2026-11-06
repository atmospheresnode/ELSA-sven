# -*- coding: utf-8 -*-
"""System checks for sign-in.

elsa/urls.py is per server, so this fails `manage.py check`, `migrate` and `runserver`
if a server still routes to Django's stock credential views.
"""
from __future__ import unicode_literals

from django.contrib.auth import views as auth_views
from django.core import checks
from django.urls import URLPattern, URLResolver, get_resolver


# These sign in or set a password without ELSA's second factor or throttles.
STOCK_CREDENTIAL_VIEWS = (
    auth_views.LoginView,
    auth_views.PasswordChangeView,
    auth_views.PasswordResetView,
    auth_views.PasswordResetConfirmView,
)


def _walk(patterns, prefix='', namespace=None):
    for entry in patterns:
        if isinstance(entry, URLResolver):
            inner = entry.namespace or namespace
            yield from _walk(entry.url_patterns, prefix + str(entry.pattern), inner)
        elif isinstance(entry, URLPattern):
            yield prefix + str(entry.pattern), namespace, entry.callback


@checks.register(checks.Tags.security, checks.Tags.urls)
def check_stock_auth_routes(app_configs, **kwargs):
    errors = []
    for route, namespace, callback in _walk(get_resolver().url_patterns):
        view_class = getattr(callback, 'view_class', None)
        if view_class is None or not issubclass(view_class, STOCK_CREDENTIAL_VIEWS):
            continue
        # Our reset views are subclasses with our throttles and mail. Anything else is stock.
        if namespace == 'friends':
            continue
        errors.append(checks.Error(
            'Django\'s {0} is routed at "{1}". It signs users in or sets passwords '
            'without ELSA\'s second factor and rate limits.'.format(view_class.__name__, route),
            hint='Remove `re_path(\'^\', include(\'django.contrib.auth.urls\'))` (or the '
                 'route above) from elsa/urls.py. ELSA\'s own sign-in and password '
                 'reset live under accounts/.',
            id='friends.E001',
        ))
    return errors


MIDDLEWARE_PATH = 'friends.middleware.AccountSessionMiddleware'


@checks.register(checks.Tags.security)
def check_account_middleware(app_configs, **kwargs):
    """Without the middleware, "sign out everywhere else" wouldn't sign anyone out."""
    from django.conf import settings
    stack = list(getattr(settings, 'MIDDLEWARE', []))
    auth = 'django.contrib.auth.middleware.AuthenticationMiddleware'
    if MIDDLEWARE_PATH in stack and auth in stack and stack.index(MIDDLEWARE_PATH) > stack.index(auth):
        return []
    return [checks.Error(
        '{0} is not in MIDDLEWARE after AuthenticationMiddleware.'.format(MIDDLEWARE_PATH),
        hint="Add '{0}' to MIDDLEWARE in elsa/settings.py, after "
             "'django.contrib.auth.middleware.AuthenticationMiddleware'.".format(MIDDLEWARE_PATH),
        id='friends.E002',
    )]
