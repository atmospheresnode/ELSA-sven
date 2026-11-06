# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.apps import AppConfig
from django.db.models.signals import post_migrate


class StaffConfig(AppConfig):
    """Admin Console app.

    Optional, since settings.py is per server. Other apps only reach it through staff.hooks.
    """
    name = 'staff'
    verbose_name = 'Admin Console'

    def ready(self):
        from .roles import sync_groups
        # After auth's own receiver has created this app's permissions.
        post_migrate.connect(sync_groups, sender=self, dispatch_uid='staff.sync_groups')
