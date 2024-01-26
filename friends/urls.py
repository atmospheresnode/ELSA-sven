from django.conf.urls import url
from django.contrib.auth import views as auth_views
from . import views



app_name='friends'
urlpatterns = [
    #url(r'^$', views.FriendList.as_view(), name='friends'),
    url(r'^(?P<pk_user>\d+)/$', views.profile, name='profile'),
    url(r'^login/$', views.friend_login, name='login'),
    url(r'^logout/$', views.friend_logout, name='logout'),
    url(r'^register/$', views.register, name='register'),
    url(r'^(?P<pk_user>\d+)/settings/$', views.friend_settings, name='settings'),
    url(r'^password_reset/$', auth_views.PasswordResetView.as_view(), name='password_reset'),
    url(r'^password_reset/done/$', auth_views.PasswordResetDoneView.as_view(), name='password_reset_done'),
    url(r'^reset/(?P<uidb64>[0-9A-Za-z_\-]+)/(?P<token>[0-9A-Za-z]{1,13}-[0-9A-Za-z]{1,20})/$',
        auth_views.PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    url(r'^reset/done/$', auth_views.PasswordResetCompleteView.as_view(), name='password_reset_complete'),

]
