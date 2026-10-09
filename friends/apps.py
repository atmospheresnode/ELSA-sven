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

        # The admin's own login form takes a password and nothing else, which
        # made it a way past the second factor for exactly the accounts that
        # matter most.  Staff sign in through ELSA like everyone else.
        admin.site.login = admin_login
