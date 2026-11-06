# -*- coding: utf-8 -*-
"""Routes for non-staff, mounted at /support/. Separate since staff/urls.py guards everything."""
from __future__ import unicode_literals

from django.urls import path

from . import public_views

app_name = 'staff_public'

urlpatterns = [
    path('requests/', public_views.requests, name='requests'),
    path('requests/new/', public_views.new_request, name='new_request'),
    path('requests/<int:pk>/', public_views.request_detail, name='request'),
    path('notifications/', public_views.notifications, name='notifications'),
    path('notifications/<int:pk>/', public_views.open_notification, name='open_notification'),
    path('notifications/email/', public_views.notification_email, name='notification_email'),
    path('notifications/news/', public_views.notification_news, name='notification_news'),
    path('unsubscribe/<str:token>/', public_views.unsubscribe, name='unsubscribe'),
    path('liens/<int:pk>/fixed/', public_views.lien_fixed, name='lien_fixed'),
    path('announcements/<int:pk>/dismiss/', public_views.dismiss_announcement, name='dismiss_announcement'),
    path('view-as/stop/', public_views.view_as_stop, name='view_as_stop'),
    path('peer-review/<str:token>/', public_views.review_invite, name='review_invite'),
]
