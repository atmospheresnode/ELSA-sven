# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.apps import AppConfig







# Normal Django configuration for an app
class FriendsConfig(AppConfig):
    name = 'friends'

    def ready(self):
        from django.contrib import admin

        from . import checks, signals  # noqa: F401  (registers checks and receivers)
        from .views import admin_login

        # The admin login form only asks for a password, which skips the second factor.
        # Staff sign in through ELSA.
        admin.site.login = admin_login

        # Django admin has no audit trail or passkey step, so it's developers only.
        # Staff use the Admin Console.
        admin.site.has_permission = _developers_only


def _developers_only(request):
    return request.user.is_active and request.user.is_superuser
