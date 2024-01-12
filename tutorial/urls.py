# Stdlib imports

# Core Django imports
from django.urls import re_path

# Third-party app imports

# Imports from apps
from . import views

app_name='tutorial'
urlpatterns = [
    re_path(r'^$', views.index, name='index'),
    re_path(r'^build_a_bundle/$', views.build_a_bundle, name='build_a_bundle'),
    re_path(r'^build_a_bundle/bundle_and_collections/$', views.bundle_and_collections, name='bundle_and_collections'),
    re_path(r'^build_a_bundle/(?P<pk_bundle>\d+)/collection_context/$', views.collection_context, name='collection_context'),
    re_path(r'^build_a_bundle/(?P<pk_bundle>\d+)/collection_data/$', views.collection_data, name='collection_data'),
    re_path(r'^build_a_bundle/(?P<pk_bundle>\d+)/collection_document/$', views.collection_document, name='collection_document'),

]
