from django.urls import re_path, path, include
from django.contrib.auth import views as auth_views
from . import views


##need to repath 
##instead using views, use include

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
    re_path(r'^(?P<pk_user>\d+)/settings/$', views.friend_settings, name='settings'),
    re_path(r'^useraccount/$', views.friend_useraccount, name='useraccount'),
    re_path(r'^password_reset/$', 
            auth_views.PasswordResetView.as_view(template_name='friends/registration/password_reset_form.html',
                                                 success_url='/elsa/accounts/password_reset/done/'), 
            name='password_reset'),
    re_path(r'^password_reset/done/$', 
            auth_views.PasswordResetDoneView.as_view(template_name='friends/registration/password_reset_done.html'), 
            name='password_reset_done'),
    re_path(r'^reset/(?P<uidb64>[0-9A-Za-z_\-]+)/(?P<token>[0-9A-Za-z]{1,13}-[0-9A-Za-z]{1,20})/$',
            auth_views.PasswordResetConfirmView.as_view(template_name='friends/registration/password_reset_confirm.html', success_url='/elsa/accounts/reset/done/'), 
            name='password_reset_confirm'),
    
    re_path(r'^reset/done/$', 
            auth_views.PasswordResetCompleteView.as_view(template_name='friends/registration/password_reset_complete.html'), 
            name='password_reset_complete'),

    re_path(r'^delete-bundles/$', views.delete_bundles, name='delete_bundles'),
    re_path(r'^bundles/$', views.bundle_hub, name='bundle_hub'),


]