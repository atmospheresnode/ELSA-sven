# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.contrib import admin

from .models import Bundle


class OwnerStatusFilter(admin.SimpleListFilter):
    """Whose bundles: active owners, owners who closed their account, or ones an admin switched off."""
    title = 'owner'
    parameter_name = 'owner'

    def lookups(self, request, model_admin):
        return (
            ('active', 'Active account'),
            ('closed', 'Closed by user'),
            ('deactivated', 'Deactivated by admin'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'active':
            return queryset.filter(user__is_active=True)
        if self.value() == 'closed':
            return queryset.filter(user__userprofile__closed_at__isnull=False)
        if self.value() == 'deactivated':
            return queryset.filter(user__is_active=False, user__userprofile__closed_at__isnull=True)
        return queryset


@admin.register(Bundle)
class BundleAdmin(admin.ModelAdmin):
    """
    Read-only.  Bundles are built and deleted through ELSA, which keeps the
    files on disk and the labels in step; an admin edit or delete here would
    change the database alone.  This exists so the team can find bundles,
    including those of closed accounts, which nobody else can open.
    """
    list_display = ('name', 'user', 'bundle_type', 'owner_status', 'archive_path', 'updated_at')
    list_filter = (OwnerStatusFilter, 'bundle_type', 'status')
    search_fields = ('name', 'bundleID', 'user__username')
    list_select_related = ('user', 'user__userprofile')

    def owner_status(self, bundle):
        user = bundle.user
        profile = getattr(user, 'userprofile', None)
        if profile is not None and profile.closed_at:
            return 'Closed by user ({0:%Y-%m-%d})'.format(profile.closed_at)
        return 'Active' if user.is_active else 'Deactivated'
    owner_status.short_description = 'Owner'

    def archive_path(self, bundle):
        return bundle.directory()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
