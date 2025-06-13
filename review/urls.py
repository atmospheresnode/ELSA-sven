from django.urls import re_path
from . import views

app_name = 'review'

urlpatterns = [
    re_path(r'^$', views.index, name='index'),
    re_path(r'^save-draft/(?P<draft_id>[0-9a-fA-F\-]{36})/$', views.save_draft, name='save_draft'),
    re_path(r'^load-draft/(?P<draft_id>[0-9a-fA-F\-]{36})/$', views.load_draft, name='load_draft'),
]