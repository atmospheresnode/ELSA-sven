from django.urls import re_path, path
from django.conf import settings
from django.contrib.auth import views as auth_views
from . import views

app_name='friends'
urlpatterns = [
    #url(r'^$', views.FriendList.as_view(), name='friends'),
    re_path(r'^(?P<pk_user>\d+)/$', views.profile, name='profile'),
    re_path(r'^login/$', views.friend_login, name='login'),
    # Identifier-first sign-in: name the account, then one method per screen.
    re_path(r'^login/identify/$', views.login_identify, name='login_identify'),
    re_path(r'^login/password/$', views.login_password, name='login_password'),
    re_path(r'^login/passkey/$', views.login_passkey, name='login_passkey'),
    re_path(r'^login/use-password/$', views.login_use_password_instead, name='login_use_password'),
    re_path(r'^otp-verify/$', views.otp_verify, name='otp_verify'), #OTP verify view
    re_path(r'^otp-resend/$', views.otp_resend, name='otp_resend'), #OTP resend view

    # Passkeys (WebAuthn)
    re_path(r'^passkey/verify/$', views.passkey_verify, name='passkey_verify'),
    re_path(r'^passkey/use-email/$', views.passkey_use_email_instead, name='passkey_use_email'),
    re_path(r'^passkey/register/options/$', views.passkey_register_options, name='passkey_register_options'),
    re_path(r'^passkey/register/verify/$', views.passkey_register_verify, name='passkey_register_verify'),
    re_path(r'^passkey/auth/options/$', views.passkey_auth_options, name='passkey_auth_options'),
    re_path(r'^passkey/auth/verify/$', views.passkey_auth_verify, name='passkey_auth_verify'),
    re_path(r'^passkey/(?P<pk>\d+)/delete/$', views.passkey_delete, name='passkey_delete'),
    re_path(r'^passkey/offer/$', views.passkey_offer, name='passkey_offer'),
    re_path(r'^passkey/offer/dismiss/$', views.passkey_offer_dismiss, name='passkey_offer_dismiss'),
    re_path(r'^logout/$', views.friend_logout, name='logout'),
    re_path(r'^register/$', views.register, name='register'),
    path('welcome/', views.welcome, name='welcome'),
    re_path(r'^(?P<pk_user>\d+)/settings/$', views.friend_settings, name='settings'),
    re_path(r'^useraccount/$', views.friend_useraccount, name='useraccount'),
    # The account page's modals post here.
    path('account/name/', views.account_name, name='account_name'),
    path('account/email/', views.account_email_start, name='account_email_start'),
    path('account/email/verify/', views.account_email_verify, name='account_email_verify'),
    path('account/email/resend/', views.account_email_resend, name='account_email_resend'),
    path('account/email/cancel/', views.account_email_cancel, name='account_email_cancel'),
    path('account/password/', views.account_password, name='account_password'),
    path('account/username/', views.account_username, name='account_username'),
    path('account/research/', views.account_research, name='account_research'),
    path('account/sessions/<int:pk>/end/', views.account_session_end, name='account_session_end'),
    path('account/sessions/end-others/', views.account_sessions_end_others, name='account_sessions_end_others'),
    path('account/devices/<int:pk>/forget/', views.account_device_forget, name='account_device_forget'),
    path('account/agency/draft/', views.account_agency_draft, name='account_agency_draft'),
    path('account/agency/send/', views.account_agency_send, name='account_agency_send'),
    path('account/close/', views.account_close, name='account_close'),
    path('passkey/<int:pk>/rename/', views.passkey_rename, name='passkey_rename'),

    # Password reset.  Path converters rather than a hand-written token regex:
    # Django 4.2 tokens carry a 32-character hash, which the old {1,20}
    # pattern refused, so every branded reset link was a 404.
    path('password_reset/', views.ElsaPasswordResetView.as_view(), name='password_reset'),
    path('password_reset/done/',
         auth_views.PasswordResetDoneView.as_view(
             template_name='friends/registration/password_reset_done.html',
             extra_context={'reset_days': max(1, settings.PASSWORD_RESET_TIMEOUT // 86400)}),
         name='password_reset_done'),
    path('reset/<uidb64>/<token>/', views.ElsaPasswordResetConfirmView.as_view(),
         name='password_reset_confirm'),
    path('reset/done/',
         auth_views.PasswordResetCompleteView.as_view(template_name='friends/registration/password_reset_complete.html'),
         name='password_reset_complete'),

    re_path(r'^delete-bundles/$', views.delete_bundles, name='delete_bundles'),
    re_path(r'^bundles/$', views.bundle_hub, name='bundle_hub'),


]