from django.conf.urls import url
from . import views



app_name='friends'
urlpatterns = [
    #url(r'^$', views.FriendList.as_view(), name='friends'),
    url(r'^(?P<pk_user>\d+)/$', views.profile, name='profile'),
    url(r'^login/$', views.friend_login, name='login'),
    url(r'^logout/$', views.friend_logout, name='logout'),
    url(r'^register/$', views.register, name='register'),
    url(r'^(?P<pk_user>\d+)/settings/$', views.friend_settings, name='settings'),

]
