"""
K. Sweebe

elsa.main.urls shows the listing of all current urls associated with elsa's main app.

"""

from django.conf.urls import url
from . import views

app_name='main'
urlpatterns = [

    # elsa's main views.
    url(r'^$', views.index, name='index'),
    url(r'^about/$', views.about, name='about'),
    url(r'^services/$', views.services, name='services'),
    url(r'^construction/$', views.construction, name='construction'),
    url(r'^contact/$', views.contact, name='contact'),
    url(r'^error/$', views.error, name='error'),
    url(r'^restricted_access/$', views.restricted_access, name='restricted_access'),
    url(r'^simple_upload/$', views.simple_upload, name='simple_upload'),

    # I use this for development (k).
    url(r'^success/$', views.success, name='success'),
]
