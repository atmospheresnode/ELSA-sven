# Stdlib imports

# Core Django imports
from django.urls import re_path, include

# Third-party app imports

# Imports from apps
from . import views



app_name='build'
urlpatterns = [
    # Alias
    re_path(r'^(?P<pk_bundle>\d+)/alias/$', views.alias, name='alias'),
    re_path(r'^(?P<pk_bundle>\d+)/alias_edit/(?P<pk_alias>[-\w]+)/$', views.alias_edit, name='alias_edit'),

    #re_path(r'^(?P<pk_bundle>\d+)/alias_delete/(?P<pk_alias>[-\w]+)/$', views.alias_delete, name='alias_delete'),

    # Alias_Delete
    re_path(r'^(?P<pk_bundle>\d+)/(?P<pk_alias>[-\w]+)/alias_delete/$', views.alias_delete, name='alias_delete'),

    #Citation Information Delete
    re_path(r'^(?P<pk_bundle>\d+)/(?P<pk_citation_information>[-\w]+)/citation_delete/$', views.delete_citation_information, name='citation_information_delete'),

    #Modification History Delete
    re_path(r'^(?P<pk_bundle>\d+)/(?P<pk_modification_history>[-\w]+)/modification_history/$', views.delete_modification_history, name='modification_history_delete'),

    # Build
    re_path(r'^$', views.build, name='build'),
    re_path(r'^(?P<bundle>\d+)/data_prep/$', views.data_prep, name='data_prep'),

    # Bundle
    re_path(r'^(?P<pk_bundle>\d+)/$', views.bundle, name='bundle'), # Secure
    re_path(r'^(?P<pk_bundle>\d+)/confirm_delete/$', views.bundle_delete_new, name='bundle_delete'), # Secure
    re_path(r'^(?P<pk_bundle>\d+)/download/$', views.bundle_download, name='bundle_download'), # Need to secure.
    re_path(r'^success_delete/$', views.success_delete, name='bundle_delete'),

    # Citation_Information
    re_path(r'^(?P<pk_bundle>\d+)/citation_information/$', views.citation_information, name='citation_information'),
    # Modification_History
    re_path(r'^(?P<pk_bundle>\d+)/modification_history/$', views.modification_history, name='modification_history'),
    # Collections


    # Context
    # re_path(r'^$', views.context, name='context'),        
    re_path(r'^(?P<pk_bundle>\d+)/context_search/$', views.context_search, name='context_search'),
    re_path(r'^(?P<pk_bundle>\d+)/contextsearch/investigation/$', views.context_search_investigation, name='context_search_investigation'),
    re_path(r'^(?P<pk_bundle>\d+)/contextsearch/investigation/(?P<pk_investigation>\d+)/instrument_host_or_facility/$', views.context_search_instrument_host_and_facility, name='context_search_instrument_host_and_facility'),
    re_path(r'^(?P<pk_bundle>\d+)/contextsearch/investigation/(?P<pk_investigation>\d+)/target/$', views.context_search_target_inv, name='context_search_target_investigation'),
    re_path(r'^(?P<pk_bundle>\d+)/contextsearch/investigation/(?P<pk_investigation>\d+)/instrument_host/(?P<pk_instrument_host>\d+)/instrument/$', views.context_search_instrument, name='context_search_instrument'),
    re_path(r'^(?P<pk_bundle>\d+)/contextsearch/facility/$', views.context_search_facility, name='context_search_facility'),
    re_path(r'^(?P<pk_bundle>\d+)/contextsearch/investigation/(?P<pk_investigation>\d+)/facility/(?P<pk_facility>\d+)/instrument/$', views.context_search_facility_instrument, name='context_search_facility_instrument'),
    re_path(r'^(?P<pk_bundle>\d+)/contextsearch/investigation/(?P<pk_investigation>\d+)/facility/(?P<pk_facility>\d+)/telescope/$', views.context_search_telescope, name='context_search_telescope'),
    re_path(r'^(?P<pk_bundle>\d+)/contextsearch/investigation/(?P<pk_investigation>\d+)/facility/(?P<pk_facility>\d+)/instrument_target/$', views.context_search_target_and_instrument, name='context_search_target_and_instrument'),
    re_path(r'^(?P<pk_bundle>\d+)/contextsearch/target/$', views.context_search_target, name='context_search_target'),

    re_path(r'^(?P<pk_bundle>\d+)/context/(?P<pk_target>\d+)/delete_target/$', views.delete_target, name='delete_target'),
    re_path(r'^(?P<pk_bundle>\d+)/context/(?P<pk_instrument>\d+)/delete_instrument/$', views.delete_instrument, name='delete_instrument'),
    re_path(r'^(?P<pk_bundle>\d+)/context/(?P<pk_instrument_host>\d+)/delete_instrument_host/$', views.delete_instrument_host, name='delete_instrument_host'),
    re_path(r'^(?P<pk_bundle>\d+)/context/(?P<pk_facility>\d+)/delete_facility/$', views.delete_facility, name='delete_facility'),
    re_path(r'^(?P<pk_bundle>\d+)/context/(?P<pk_investigation>\d+)/delete_investigation/$', views.delete_investigation, name='delete_investigation'),

    re_path(r'^investigations/$', views.investigations, name='investigations'),
    re_path(r'^instruments/$', views.instruments, name='instruments'),
    re_path(r'^instrument_hosts/$', views.instrument_hosts, name='instrument_hosts'),



    # Data
#    url(r'^(?P<pk_bundle>\d+)/data/$', views.data, name='data'),
    re_path(r'^(?P<pk_bundle>\d+)/data/(?P<pk_data>\d+)/$', views.data, name='data'),    
    re_path(r'^(?P<pk_bundle>\d+)/data/(?P<pk_product_observational>\d+)/$', views.product_observational, name='product_observational'),

    re_path(
        r'^(?P<pk_bundle>\d+)/data/(?P<pk_data>\d+)/product_observational/(?P<pk_product_observational>\d+)/array/$',
        views.array,
        name='array'
    ),


    re_path(r'^(?P<pk_bundle>\d+)/data/(?P<pk_data>\d+)/display_dictionary/(?P<pk_display_dictionary>\d+)/$', views.display_dictionary, name='display_dictionary'),




    re_path(r'^(?P<pk_bundle>\d+)/data/table/(?P<pk_product_observational>\d+)/$', views.table_detail, name='table_detail'),
    re_path(r'^(?P<pk_bundle>\d+)/(?P<pk_data>\d+)/table_creation/$', views.Table_Creation, name='table_creation'),
    re_path(r'^(?P<pk_bundle>\d+)/(?P<table>[-/w]+)/field_creation/$', views.Field_Creation, name='field_creation'),

    # Dictionary
    re_path(r'^(?P<pk_bundle>\d+)/data/array/display_dictionary/$', views.display_dictionary, name='display_dictionary'),
    
    # Document
    re_path(r'^(?P<pk_bundle>\d+)/document/$', views.document, name='document'),
    re_path(r'^(?P<pk_bundle>\d+)/document/product_document/(?P<pk_product_document>\d+)/$', views.product_document, name='product_document'),


    # XML_Schema --> A view that no one sees.  So no xml_schema url.  This might even be removed 
    # completely from PDS4





    # TEST
]
