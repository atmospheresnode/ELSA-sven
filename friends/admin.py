"""
K. Sweebe

elsa.friends.admin is used to display elsa's backend on a GUI to see and manipulate data (given the proper permissions).

"""
# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.contrib import admin
from .models import Passkey, UserProfile


class ClosedFilter(admin.SimpleListFilter):
    title = 'account'
    parameter_name = 'account'

    def lookups(self, request, model_admin):
        return (('open', 'Open'), ('closed', 'Closed by user'))

    def queryset(self, request, queryset):
        if self.value() == 'open':
            return queryset.filter(closed_at__isnull=True)
        if self.value() == 'closed':
            return queryset.filter(closed_at__isnull=False)
        return queryset


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'agency', 'email_verified_at', 'closed_at', 'otp_locked_until')
    list_filter = ('agency', ClosedFilter)
    search_fields = ('user__username', 'user__email')
    # Code hashes are credentials, so they are neither shown nor editable.
    exclude = ('otp_hash', 'pending_email_hash')
    readonly_fields = ('otp_created_at', 'otp_attempts', 'pending_email', 'pending_email_created_at',
                       'pending_email_attempts', 'password_changed_at', 'closed_at')


@admin.register(Passkey)
class PasskeyAdmin(admin.ModelAdmin):
    # Read-only apart from deletion: revoking a lost authenticator is the one
    # thing an admin needs to do here.
    list_display = ('name', 'user', 'backed_up', 'created_at', 'last_used_at')
    search_fields = ('user__username', 'name')
    readonly_fields = ('user', 'credential_id', 'public_key', 'sign_count', 'transports',
                       'name', 'backed_up', 'created_at', 'last_used_at')

    def has_add_permission(self, request):
        return False
