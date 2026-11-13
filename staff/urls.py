# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.urls import path

from . import archive_views, console_views, guide_views, team_views, views
from .decorators import console_view

app_name = 'staff'

urlpatterns = [
    # Getting in and out: membership only.
    path('choose/', views.choose, name='choose'),
    path('enter/', views.enter, name='enter'),
    path('verify/', views.verify, name='verify'),
    path('passkey/options/', views.passkey_options, name='passkey_options'),
    path('passkey/verify/', views.passkey_verify, name='passkey_verify'),
    path('leave/', views.leave, name='leave'),
    path('stay/', views.stay, name='stay'),

    # The console: admin mode required.
    path('', views.home, name='home'),
    path('cases/<int:pk>/', views.case_detail, name='case'),
    path('cases/<int:pk>/act/', views.case_action, name='case_action'),
    path('cases/bulk/', views.bulk_action, name='bulk_action'),
    path('users/', views.users, name='users'),
    path('users/<int:pk>/', views.user_detail, name='user'),
    path('users/<int:pk>/act/', views.user_action, name='user_action'),
    path('bundles/', views.bundles, name='bundles'),
    path('bundles/<int:pk>/', views.bundle_detail, name='bundle'),
    path('bundles/<int:pk>/download/', views.bundle_download, name='bundle_download'),
    path('validation/<int:pk_run>/', views.validation_report, name='validation_report'),
    path('validation/<int:pk_run>/report/', views.validation_raw, name='validation_raw'),
    path('audit/', views.audit_log, name='audit'),
    path('audit/export.csv', console_views.audit_csv, name='audit_csv'),
    path('health/', views.health, name='health'),

    # v2
    path('search/', console_views.search, name='search'),
    path('replies/', console_views.saved_replies, name='saved_replies'),
    path('preferences/', console_views.preferences, name='preferences'),
    path('spam/', console_views.spam, name='spam'),
    path('bundles/<int:pk>/files/', console_views.bundle_files, name='bundle_files'),
    path('bundles/<int:pk>/files/download/', console_views.bundle_file_download, name='bundle_file_download'),
    path('bundles/<int:pk>/validate/', console_views.bundle_validate, name='bundle_validate'),
    path('bundles/<int:pk>/label-updates/', console_views.bundle_label_upgrade, name='bundle_label_upgrade'),
    path('bundles/<int:pk>/label-updates/preview/', views.bundle_label_preview, name='bundle_label_preview'),
    path('bundles/<int:pk>/label-updates/<int:pk_upgrade>/undo/', console_views.bundle_label_upgrade_undo, name='bundle_label_upgrade_undo'),
    path('validation/<int:pk_run>/cancel/', console_views.run_cancel, name='run_cancel'),
    path('validation/reap/', console_views.runs_reap, name='runs_reap'),
    path('assistant/', console_views.assistant_switch, name='assistant_switch'),
    path('reviews/', console_views.campaign_list, name='campaigns'),
    path('reviews/<int:pk>/', console_views.campaign_detail, name='campaign'),
    path('reviews/<int:pk>/act/', console_views.campaign_action, name='campaign_action'),
    path('reviews/<int:pk>/report.pdf', console_views.campaign_pdf, name='campaign_pdf'),
    path('announcements/', console_views.announcements, name='announcements'),
    path('email/', console_views.broadcast_list, name='broadcasts'),
    path('email/new/', console_views.broadcast_edit, name='broadcast_new'),
    path('email/<int:pk>/', console_views.broadcast_edit, name='broadcast'),
    path('mail-log/', console_views.mail_log, name='mail_log'),
    path('reports/', console_views.reports, name='reports'),

    # Running the archive
    path('pipeline/', archive_views.pipeline_board, name='pipeline'),
    path('projects/', archive_views.project_list, name='projects'),
    path('projects/<int:pk>/', archive_views.project_detail, name='project'),
    path('cases/<int:pk>/release/', archive_views.release_start, name='release_start'),
    path('releases/<int:pk>/', archive_views.release_detail, name='release'),
    path('releases/<int:pk>/package/', archive_views.release_package, name='release_package'),

    # Helping users, team work
    path('stalled/', team_views.stalled, name='stalled'),
    path('insights/', team_views.insights, name='insights'),
    path('knowledge/', team_views.knowledge, name='knowledge'),
    path('knowledge/<int:pk>/', team_views.knowledge_note, name='knowledge_note'),
    path('workload/', team_views.workload, name='workload'),
    path('settings/', team_views.console_settings, name='settings'),
    path('views/', team_views.saved_views, name='saved_views'),
    path('cases/<int:pk>/dossier.pdf', team_views.case_dossier, name='case_dossier'),
    path('bundles/<int:pk>/dossier.pdf', team_views.bundle_dossier, name='bundle_dossier'),
    path('cases/<int:pk>/pulse/', team_views.case_pulse, name='case_pulse'),
    path('today/', guide_views.today, name='today'),
    path('getting-started/', guide_views.help_page, name='help'),
    path('jump/', team_views.jump, name='jump'),
    path('refresh/<slug:key>/', team_views.refresh_snapshot, name='refresh_snapshot'),
    path('updates/', team_views.updates, name='updates'),
]

# Fail closed: a view added above without a guard gets the full one.
for pattern in urlpatterns:
    if not getattr(pattern.callback, 'staff_guarded', False):
        pattern.callback = console_view()(pattern.callback)
