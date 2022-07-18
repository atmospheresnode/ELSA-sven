# Stdlib imports

# Core Django imports
from django.conf.urls import url

# Third-party app imports

# Imports from apps
from . import views

app_name='tutorial'
urlpatterns = [
    url(r'^$', views.index, name='index'),
    url(r'^build_a_bundle/$', views.build_a_bundle, name='build_a_bundle'),
    url(r'^build_a_bundle/bundle_and_collections/$', views.bundle_and_collections, name='bundle_and_collections'),
    url(r'^build_a_bundle/(?P<pk_bundle>\d+)/collection_context/$', views.collection_context, name='collection_context'),
    url(r'^build_a_bundle/(?P<pk_bundle>\d+)/collection_data/$', views.collection_data, name='collection_data'),
    url(r'^build_a_bundle/(?P<pk_bundle>\d+)/collection_document/$', views.collection_document, name='collection_document'),

]
