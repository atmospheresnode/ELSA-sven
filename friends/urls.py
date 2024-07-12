from django.urls import re_path, path
from django.contrib.auth import views as auth_views
from . import views


##need to repath 
##instead using views, use include

app_name='friends'
urlpatterns = [
    #url(r'^$', views.FriendList.as_view(), name='friends'),
    re_path(r'^(?P<pk_user>\d+)/$', views.profile, name='profile'),
    re_path(r'^login/$', views.redirect_to_elsa_home, name='login'),
    re_path(r'^logout/$', views.friend_logout, name='logout'),
    re_path(r'^register/$', views.register, name='register'),
    re_path(r'^(?P<pk_user>\d+)/settings/$', views.friend_settings, name='settings'),
    re_path(r'^password_reset/$', auth_views.PasswordResetView.as_view(), name='password_reset'),
    re_path(r'^password_reset/done/$', auth_views.PasswordResetDoneView.as_view(), name='password_reset_done'),
    re_path(r'^reset/(?P<uidb64>[0-9A-Za-z_\-]+)/(?P<token>[0-9A-Za-z]{1,13}-[0-9A-Za-z]{1,20})/$',
        auth_views.PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    re_path(r'^reset/done/$', auth_views.PasswordResetCompleteView.as_view(), name='password_reset_complete'),
]