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
]