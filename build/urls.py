# Stdlib imports

# Core Django imports
from django.conf.urls import url, include

# Third-party app imports

# Imports from apps
from . import views



app_name='build'
urlpatterns = [
    # Alias
#    url(r'^(?P<pk_bundle>\d+)/alias/$', views.alias, name='alias'),
    url(r'^(?P<pk_bundle>\d+)/alias_edit/(?P<pk_alias>[-\w]+)/$', views.alias_edit, name='alias_edit'),

    url(r'^(?P<pk_bundle>\d+)/alias_delete/(?P<pk_alias>[-\w]+)/$', views.alias_delete, name='alias_delete'),

    # Alias_Delete
    url(r'^(?P<pk_bundle>\d+)/(?P<alias>[-\w]+)/alias_delete/$', views.alias_delete, name='alias_delete'),

    # Build
    url(r'^$', views.build, name='build'),
    url(r'^(?P<bundle>\d+)/data_prep/$', views.data_prep, name='data_prep'),

    # Bundle
    url(r'^(?P<pk_bundle>\d+)/$', views.bundle, name='bundle'), # Secure
    url(r'^(?P<pk_bundle>\d+)/confirm_delete/$', views.bundle_delete, name='bundle_delete'), # Secure
    url(r'^(?P<pk_bundle>\d+)/download/$', views.bundle_download, name='bundle_download'), # Need to secure.
    url(r'^success_delete/$', views.success_delete, name='bundle_delete'),

    # Citation_Information
    url(r'^(?P<pk_bundle>\d+)/citation_information/$', views.citation_information, name='citation_information'),
    # Modification_History
    url(r'^(?P<pk_bundle>\d+)/modification_story/$', views.modification_history, name='modification_history'),
    # Collections


    # Context
    url(r'^(?P<pk_bundle>\d+)/contextsearch/$', views.context_search, name='context_search'),
    url(r'^(?P<pk_bundle>\d+)/contextsearch/investigation/$', views.context_search_investigation, name='context_search_investigation'),
    url(r'^(?P<pk_bundle>\d+)/contextsearch/investigation/(?P<pk_investigation>\d+)/instrument_host/$', views.context_search_instrument_host, name='context_search_instrument_host'),
    url(r'^(?P<pk_bundle>\d+)/contextsearch/investigation/(?P<pk_investigation>\d+)/instrument_host/(?P<pk_instrument_host>\d+)/target/$', views.context_search_target, name='context_search_target'),
    url(r'^(?P<pk_bundle>\d+)/contextsearch/investigation/(?P<pk_investigation>\d+)/instrument_host/(?P<pk_instrument_host>\d+)/instrument/$', views.context_search_instrument, name='context_search_instrument'),
    url(r'^(?P<pk_bundle>\d+)/contextsearch/facility/$', views.context_search_facility, name='context_search_facility'),
    url(r'^(?P<pk_bundle>\d+)/contextsearch/facility/(?P<pk_facility>\d+)/instrument/$', views.context_search_facility_instrument, name='context_search_facility_instrument'),
    url(r'^(?P<pk_bundle>\d+)/contextsearch/telescope/$', views.context_search_telescope, name='context_search_telescope'),
    url(r'^$', views.context, name='context'),
    url(r'^investigations/$', views.investigations, name='investigations'),
    url(r'^instruments/$', views.instruments, name='instruments'),
    url(r'^instrument_hosts/$', views.instrument_hosts, name='instrument_hosts'),



    # Data
#    url(r'^(?P<pk_bundle>\d+)/data/$', views.data, name='data'),
    url(r'^(?P<pk_bundle>\d+)/data/(?P<pk_data>\d+)/$', views.data, name='data'),    
    url(r'^(?P<pk_bundle>\d+)/data/(?P<pk_product_observational>\d+)/$', views.product_observational, name='product_observational'),

    url(
        r'^(?P<pk_bundle>\d+)/data/(?P<pk_data>\d+)/product_observational/(?P<pk_product_observational>\d+)/array/$',
        views.array,
        name='array'
    ),


    url(r'^(?P<pk_bundle>\d+)/data/(?P<pk_data>\d+)/display_dictionary/(?P<pk_display_dictionary>\d+)/$', views.display_dictionary, name='display_dictionary'),




    url(r'^(?P<pk_bundle>\d+)/data/table/(?P<pk_product_observational>\d+)/$', views.table_detail, name='table_detail'),
    url(r'^(?P<pk_bundle>\d+)/(?P<data_object>\d+)/table_creation/$', views.Table_Creation, name='table_creation'),
    url(r'^(?P<pk_bundle>\d+)/(?P<table>[-/w]+)/field_creation/$', views.Field_Creation, name='field_creation'),

    # Dictionary
    url(r'^(?P<pk_bundle>\d+)/data/array/display_dictionary/$', views.display_dictionary, name='display_dictionary'),
    
    # Directory 
    url(r'^(?P<path>.*)/$', views.dir_index, name='directory_view'),

    # Document
    url(r'^(?P<pk_bundle>\d+)/document/$', views.document, name='document'),
    url(r'^(?P<pk_bundle>\d+)/document/product_document/(?P<pk_product_document>\d+)/$', views.product_document, name='product_document'),


    # XML_Schema --> A view that no one sees.  So no xml_schema url.  This might even be removed 
    # completely from PDS4





    # TEST
]
