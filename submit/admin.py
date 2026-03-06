from django.contrib import admin
from .models import BundleSubmission

@admin.register(BundleSubmission)
class BundleSubmissionAdmin(admin.ModelAdmin):
    list_display = ('filename', 'user', 'bundle_type', 'status', 'submitted_at')
    list_filter = ('bundle_type', 'status', 'user')