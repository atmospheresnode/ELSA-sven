"""
K. Sweebe

elsa.main.urls shows the listing of all current urls associated with elsa's main app.

"""

from django.urls import re_path
from . import views

app_name='main'
urlpatterns = [

    # elsa's main views.
    re_path(r'^$', views.index, name='index'),
    re_path(r'^about/$', views.about, name='about'),
    re_path(r'^services/$', views.services, name='services'),
    re_path(r'^construction/$', views.construction, name='construction'),
    re_path(r'^contact/$', views.contact, name='contact'),
    re_path(r'^context_products_contact/$', views.context_products_contact, name='context_products_contact'),
    re_path(r'^error/$', views.error, name='error'),
    re_path(r'^restricted_access/$', views.restricted_access, name='restricted_access'),
    re_path(r'^simple_upload/$', views.simple_upload, name='simple_upload'),
    re_path(r'^contact-from-login/$', views.contact_from_login, name='contact_from_login'),
    

    # I use this for development (k).
    re_path(r'^success/$', views.success, name='success'),
]
