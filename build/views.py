# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

from builtins import str
from .forms import *
from main.forms import *
from .models import *
from itertools import chain
# from context.models import *
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse, HttpRequest, JsonResponse
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from build import document_files, netcdf_header, preflight, validate_report, validate_rules, validate_runner
from staff import hooks as staff_hooks
from build.downloads import bundle_tarball_response
from build import bundle_setup, creation, label_upgrades, netcdf_uploads, walkthrough
from build.label_repair import AMA_LID
from django.shortcuts import render, redirect, get_object_or_404
from django.template import RequestContext
from django.template.defaultfilters import filesizeformat
from django.urls import reverse
from django import forms
from django.forms import modelformset_factory
from django.views.generic.edit import UpdateView, DeleteView
from django.contrib import messages
import datetime
import functools
import shutil
import lxml.etree as ET # for XML parsing- Added by Rupak
# By name: ET is rebound to the standard library's ElementTree further down this file.
from lxml.etree import XMLSyntaxError as LxmlSyntaxError
# from lxml import etree # debug product obs only

# Libraries for handilng netCDF files
import io
from urllib.parse import quote
from django.core.mail import EmailMessage
from friends.mail import build_branded
from django.core.cache import cache
from django.utils import timezone
from django.utils.timezone import localtime
import logging

logger = logging.getLogger(__name__)

from datetime import timezone as datetime_timezone
from django.utils import timezone

# -------------------------------------------------------------------------------------------------- #
# -------------------------------------------------------------------------------------------------- #
#
#                                            Create your views here
#
# -------------------------------------------------------------------------------------------------- #
@login_required
def rebuild_collection_inventories(bundle):
    """Rewrite every collection's inventory table and record counts.

    Both depend on membership, so call this whenever a product is added or removed.
    Safe to call more often than needed.
    """
    for collection in bundle_label_targets(bundle):
        try:
            collection.build_inventory()
        except Exception as error:
            # Keep going if one collection fails, and don't break the request adding the document.
            print('rebuild_collection_inventories: {} failed: {}'.format(
                collection, error))


def _submission_status(bundle):
    """The three components the Review & Submit button checks, from build.preflight."""
    from build import preflight

    return preflight.status(bundle)


def validation_summary(bundle):
    """What the node is told about validation when a bundle is submitted.

    Advisory items are listed since the gate already blocks anything serious. ELSA's own
    findings are only counted. ``tone`` is ok, attention, info or muted, for the HTML email.
    """
    from build import validate_rules

    summary = {'headline': '', 'tone': 'muted', 'groups': [], 'notes': []}

    # Use the latest finished run. Submitting queues a new one, which has no results yet.
    run = ValidationRun.objects.filter(
        bundle=bundle, tier=ValidationRun.TIER_STRUCTURE,
        status__in=[ValidationRun.STATUS_DONE, ValidationRun.STATUS_FAILED],
    ).order_by('-finished_at', '-requested_at').first()

    if run is None:
        if validate_runner.latest_run_for(bundle) is not None:
            summary['headline'] = 'still running when this was submitted.'
        else:
            summary['headline'] = 'not run for this bundle.'
        return summary

    if run.status == ValidationRun.STATUS_FAILED:
        summary['headline'] = ('could not run ({}). The bundle was accepted anyway; being '
                               'unable to check is not evidence of a problem.'.format(
                                   run.failure_reason or 'no reason recorded'))
        return summary

    findings = run.findings or []
    summary['headline'] = '{} label(s) checked{}.'.format(
        run.products_total, ', results out of date' if run.is_stale() else '')

    if not findings:
        summary['tone'] = 'ok'
        summary['notes'].append('Nothing reported.')
        return summary

    translated = validate_rules.translate(findings)

    if translated['user']:
        items = []
        for item in translated['user']:
            # Some items name what they're about, otherwise bad file names look like identical lines.
            subject = item.get('subject')
            items.append('{}{}'.format(item['title'], ' ({})'.format(subject) if subject else ''))
        summary['groups'].append({'title': 'Still outstanding for the submitter', 'items': items})

    if translated['advisory']:
        items = ['{} ({} finding{})'.format(item['title'], len(item['findings']),
                                            '' if len(item['findings']) == 1 else 's')
                 for item in translated['advisory']]
        summary['groups'].append({'title': 'Worth a look during review', 'items': items})

    hidden = sum(len(item['findings']) for item in translated['elsa'])
    if hidden:
        summary['notes'].append('{} finding(s) caused by ELSA rather than the submitter, '
                                'hidden from them.'.format(hidden))

    if translated['user']:
        summary['tone'] = 'attention'
    elif translated['advisory']:
        summary['tone'] = 'info'
    else:
        summary['tone'] = 'ok'
        summary['notes'].append('Nothing outstanding for the submitter.')

    return summary


def validation_summary_for_email(bundle, summary=None):
    """The validation summary as plain text."""
    summary = summary or validation_summary(bundle)
    lines = ['PDS validation: ' + summary['headline']]
    for group in summary['groups']:
        lines.append('  {}:'.format(group['title']))
        lines.extend('    - ' + item for item in group['items'])
    lines.extend('  ' + note for note in summary['notes'])
    return '\n'.join(lines)


def validation_context(bundle, user):
    """Everything the PDS validation panel needs, for the bundle page and the partial.

    Read-only, nothing gets started here. Running a check is a POST.
    """
    from build import preflight

    # ELSA's own requirements, from the database, so they show before any check has run.
    outstanding = preflight.requirements(bundle)

    latest_validation = validate_runner.latest_run_for(bundle)
    context = {
        'validation_run': latest_validation,
        'validation_requirements': outstanding,
        # Whether the page should start a check after loading. Rendering never starts one.
        'validation_auto_check': validate_runner.should_auto_check(bundle),
        # Why submission is blocked, in the same words the view uses when refusing.
        'validation_block': validate_runner.submission_block(bundle, user),
        'validation_summary': None,
        'validation_cards': [],
        'validation_advisory': [],
        'validation_raw': [],
        'validation_raw_total': 0,
        'validation_raw_elsa': 0,
    }

    # Build a summary if there's anything to say, including an unmet requirement with no findings.
    # Findings come from the run the verdict is based on, which may not be the latest if that one failed.
    judged = validate_runner.judged_run(bundle)
    findings = (judged.findings or []) if judged else []
    # Same list the gate uses, so the panel and the gate agree.
    blocking = list(outstanding) + validate_rules.unresolved_items(findings, bundle)
    if findings or outstanding:
        context['validation_summary'] = validate_rules.summarise(findings, blocking)
        context['validation_cards'] = validate_rules.cards(findings, blocking)
        context['validation_advisory'] = validate_rules.translate(
            validate_rules.without_unresolved(findings, blocking))['advisory']
        # Raw output for the second tab. PDS reports most defects twice, so group them by what
        # they're about. Each group has the plain-language item it became.
        context['validation_raw'] = validate_rules.raw_groups(findings)
        context['validation_raw_total'] = sum(
            len(groups) for _label, groups in context['validation_raw'])
        # Count distinct problems, same as the total. The summary's "hidden" counts rule categories.
        context['validation_raw_elsa'] = sum(
            1 for _label, groups in context['validation_raw']
            for group in groups if group['audience'] == validate_rules.ELSA)

    return context


def bundle_label_targets(bundle):
    """Every collection label a bundle-level metadata edit has to reach.

    Collections are in two tables. Product_Collection has the ones ELSA creates and
    AdditionalCollections has the ones the user adds. Data collections are excluded since they
    have their own observational metadata.
    """
    return list(chain(
        Product_Collection.objects.filter(bundle=bundle).exclude(collection='Data'),
        AdditionalCollections.objects.filter(bundle=bundle)))


@walkthrough.moved_to_setup('alias')
def alias(request, pk_bundle):  # DEPRECATED: to be replaced by edit alias, **not deprecated**
    print(' \n\n \n\n-------------------------------------------------------------------------')
    print('\n\n---------------------- Add an Alias with ELSA ---------------------------')
    print('------------------------------ DEBUGGER ---------------------------------')

    # Get Bundle
    bundle = Bundle.objects.get(pk=pk_bundle)
    #collections = Collections.objects.get(bundle=bundle)

    # Secure ELSA by seeing if the user logged in is the same user associated with the Bundle
    if request.user == bundle.user:
        print('authorized user: {}'.format(request.user))

        # ELSA's current user is the bundle user so begin view logic
        # Get forms
        form_alias = AliasForm(request.POST or None)

        # Declare context_dict for templating language used in ELSAs templates
        context_dict = {
            'form_alias': form_alias,
            'bundle': bundle,
            'walkthrough': walkthrough.progress(bundle, 'alias'),
        }

        # After ELSAs friend hits submit, if the forms are completed correctly, we should enter
        # this conditional.
        print('\n\n------------------------------- ALIAS INFO --------------------------------')
        print('\nCurrently awaiting user input...\n\n')

        if form_alias.is_valid():
            print('form_alias is valid for {}.'.format(bundle.user))
            # Create Alias model object
            alias = form_alias.save(commit=False)
            alias.bundle = bundle
            alias.save()
            print('Alias model object: {}'.format(alias))

            product_bundle = Product_Bundle.objects.get(bundle=bundle)
            product_collections_list = bundle_label_targets(bundle)

            write_into_label(alias, product_bundle, product_collections_list)

            print('---------------- End Build Alias -----------------------------------')
            
            return redirect(walkthrough.after_save(request, bundle, 'alias'))

        # Get all current Alias objects associated with the user's Bundle
        # alias_list = Alias.objects.filter(bundle=bundle)
        # context_dict['alias_list'] = alias_list

        return render(request, 'build/alias/alias.html', context_dict)
    else:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')


@login_required
def alias_edit(request, pk_bundle, pk_alias):  # DEPRECATED: to be replaced by edit alias
    print(' \n\n \n\n-------------------------------------------------------------------------')
    print('\n\n---------------------- Add an Alias with ELSA ---------------------------')
    print('------------------------------ DEBUGGER ---------------------------------')

    # Get Bundle
    bundle = Bundle.objects.get(pk=pk_bundle)
    #    collections = Collections.objects.get(bundle=bundle)

    # Secure ELSA by seeing if the user logged in is the same user associated with the Bundle
    if request.user == bundle.user:
        print('authorized user: {}'.format(request.user))

        # Get Alias and its form
        alias = Alias.objects.get(pk=pk_alias, bundle=bundle)

        form_alias = AliasForm(request.POST or None, instance=alias)

        # We're removing the old alias first and then adding it back with the updated information.
        if request.method == 'POST' and form_alias.is_valid():
            if form_alias.has_changed():
                old_alias = Alias.objects.get(pk=pk_alias, bundle=bundle)
                product_bundle = Product_Bundle.objects.get(bundle=bundle)
                product_collections_list = bundle_label_targets(bundle)

                remove_from_label(old_alias, product_bundle, product_collections_list,)

                alias = form_alias.save()

                write_into_label(alias, product_bundle, product_collections_list)

            return redirect('build:bundle', pk_bundle=pk_bundle)
                
        # Declare context_dict for templating language used in ELSAs templates
        context_dict = {
            'alias': alias,
            'bundle': bundle,
            'form_alias': form_alias,

        }

        return render(request, 'build/alias/alias_edit.html', context_dict)
    else:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')


@login_required
def alias_delete(request, pk_bundle, pk_alias):

    print(' \n\n \n\n-------------------------------------------------------------------------')
    print('\n\n---------------------- Remove an Alias with ELSA ---------------------------')
    print('------------------------------ DEBUGGER ---------------------------------')

    bundle = Bundle.objects.get(pk=pk_bundle)

    if request.user == bundle.user:
        print("authorized user")

        alias = Alias.objects.get(pk=pk_alias)

        product_bundle = Product_Bundle.objects.get(bundle=bundle)
        product_collections_list = bundle_label_targets(bundle)

        remove_from_label(alias, product_bundle, product_collections_list)

        alias.delete()

        return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/')


        # delete_alias = request.POST.get('Delete')

        # context_dict = {
        #     'alias': pk_alias,
        #     'bundle': bundle,
        #     'delete_alias': delete_alias,
        # }

        # print(pk_alias)
        # print(request)
        # print(bundle)

        # obj = get_object-or_404(alias_edit, id=id)

        # alias_class = Alias()

        # if request.method == 'POST':
        #     alias_class.remove(bundle, alias)
        #     alias_to_remove = Alias.objects.filter(
        #         bundle=bundle).filter(alternate_id=alias)
        #     alias_to_remove.delete()
        #     return HttpResponseRedirect("/")

        # return render(request, 'build/alias/alias_delete.html', context_dict)

    else:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')


@login_required
def array(request, pk_bundle, pk_data, pk_product_observational):
    print(' \n\n \n\n-------------------------------------------------------------------------')
    print('\n\n---------------- Welcome to Build A Bundle with ELSA --------------------')
    print('------------------------------ DEBUGGER ---------------------------------')

    bundle = Bundle.objects.get(pk=pk_bundle)
    data = Data.objects.get(pk=pk_data)
    product_observational = Product_Observational.objects.get(
        pk=pk_product_observational)

    if request.user == bundle.user:
        # Get forms
        form_array = ArrayForm(request.POST or None)

        # Get array
        arrays = Array.objects.filter(
            product_observational=product_observational)

        # Get display dictionary to show what it says to the user
        try:
            disp_dict = DisplayDictionary.objects.get(data=pk_data)
        except DisplayDictionary.DoesNotExist:
            disp_dict = None

        # Declare context_dict for template
        context_dict = {
            'bundle': bundle,
            'data': data,
            'form_array': form_array,
            'product_observational': product_observational,
            'arrays': arrays,
            'disp_dict': disp_dict,
        }

        # After ELSAs friend hits submit, if the forms are completed correctly, we should enter
        # this conditional.
        print('\n\n------------------------------- ARRAY INFO --------------------------------')
        print('\nCurrently awaiting user input...\n\n')
        if form_array.is_valid():
            print('form_array is valid for {}.'.format(bundle.user))
            # Create Array model object
            array = form_array.save(commit=False)
            array.product_observational = product_observational
            array.save()
            print('Array model object: {}'.format(array))

            # Find appropriate label(s).
            # Array gets added to... some... Product_Observational labels.
            # We first get all labels of these given types.
            label = array.product_observational.label()

            # Open appropriate label(s).
            print('- Label: {}'.format(label))
            print(' ... Opening Label ... ')
            label_list = open_label_with_tree(label)
            label_root = label_list[1]
            print('Label List: {}\nLabel Root: {}'.format(label_list, label_root))

            # Build Array
            print(' ... Building Label ... ')
            label_root = array.build_array(label_root)
            # array.array_list.append(label_root) <~-- just stole this from alias ?? idk what does

            # Close appropriate label(s)
            print(' ... Closing Label ... ')
            close_label(label, label_root, label_list[2])

        else:
            print("Form array is not valid")

        return render(request, 'build/data/array.html', context_dict)

    else:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')


@login_required
def array_detail(request, pk_bundle, pk_product_observational):
    print(' \n\n \n\n-------------------------------------------------------------------------')
    print('\n\n---------------- Welcome to Build A Bundle with ELSA --------------------')
    print('------------------------------ DEBUGGER ---------------------------------')

    bundle = Bundle.objects.get(pk=pk_bundle)

    if request.user == bundle.user:

        # Declare context_dict for template
        context_dict = {
            'bundle': bundle,
        }

        return render(request, 'build/data/array_detail.html', context_dict)

    else:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')


@login_required
def table_detail(request, pk_bundle, pk_product_observational):
    print(' \n\n \n\n-------------------------------------------------------------------------')
    print('\n\n---------------- Welcome to Build A Bundle with ELSA --------------------')
    print('------------------------------ DEBUGGER ---------------------------------')

    bundle = Bundle.objects.get(pk=pk_bundle)

    if request.user == bundle.user:

        # Declare context_dict for template
        context_dict = {
            'bundle': bundle,
        }

        return render(request, 'build/data/table_detail.html', context_dict)

    else:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')


"""
    build is the start of the bundle building process.  Because a bundle has yet to be created, there is
    no security check to see if the user is associated with the bundle...
"""


@login_required
def build(request):
    print(' \n\n \n\n-------------------------------------------------------------------------')
    print('\n\n---------------- Welcome to Build A Bundle with ELSA --------------------')
    print('------------------------------ DEBUGGER ---------------------------------')

    # Get forms
    form_bundle = BundleForm(request.POST or None)
    form_collections = CollectionsForm(request.POST or None)
    form_product_collection_document = ProductCollectionForm(request.POST or None)
    form_product_collection_context = ProductCollectionForm(request.POST or None)
    form_product_collection_schema = ProductCollectionForm(request.POST or None)

    # Declare context_dict for template
    context_dict = {
        'form_bundle': form_bundle,
        'form_collections': form_collections,
        'form_product_collection': form_product_collection_document,
        'user': request.user,
    }

    print('\n\n------------------------------- USER INFO -------------------------------')
    print('User:    {}'.format(request.user))
    print('Agency:  {}'.format(request.user.userprofile.agency))
    print('All users have access to this area.')

    print('\n\n------------------------------- BUILD INFO --------------------------------')
    print('\n ... waiting on user input ...\n')
    # After ELSAs friend hits submit, if the forms are completed correctly, we should enter here
    # this conditional.
    if form_bundle.is_valid() and form_collections.is_valid():
        print('form_bundle & form_collections are valid')
        # Create Collections Model Object and list of Collections, list of Collectables
        # bundle_name = form_bundle['name']
        # bundle_user = request.user
        # bundle_count = Bundle.objects.filter(name=bundle_name, user=bundle_user).count()
        # product_bundle = ProductBundleForm().save(commit=False)
        # product_bundle.bundle = bundle

        # Check Uniqueness  --- GOTTA BE A BETTER WAY (k)
        bundle_name = form_bundle.cleaned_data['name']
        bundle_user = request.user
        enable_walkthrough = request.POST.get('enable_walkthrough') == 'on'

        # Same form sent again goes to the bundle it already made (see build/creation.py).
        create_token = request.POST.get('create_token', '')
        repeat = creation.already_created(request, create_token, bundle_name)
        if repeat is not None:
            creation.mark_just_created(request, repeat, again=True)
            if enable_walkthrough:
                return redirect(walkthrough.setup_url(repeat))
            messages.info(request, 'You already created "{}"; here it is.'.format(repeat.name))
            return redirect(walkthrough.bundle_url(repeat))

        # Names are unique across all of ELSA, not per user, and case-insensitive. Check here
        # so a taken name is a form message instead of a 500.
        bundle_count = Bundle.objects.filter(name__iexact=bundle_name).count()

        if bundle_count == 0:  # If user and bundle name are unique, then...
            print('At the start')
            # One transaction so a crash can't leave a Bundle with no Product_Bundle.
            # Files already written are fine, creating the same name again overwrites them.
            with transaction.atomic():
                # Create Bundle model.
                bundle = form_bundle.save(commit=False)
                #bundle.uniqueifier = bundle.name + "_" + smart_str(request.user.id)
                bundle.user = request.user
                # b for build.  New Bundles are always in build stage first.
                bundle.status = 'b'
            
            
                #bundle.save()

                #TESTING OPTIONAL BUNDLE ID

                user_bundleID = form_bundle.cleaned_data.get('bundleID', '').strip()
                if user_bundleID:
                    bundle.bundleID = user_bundleID
                else:
                    bundle.bundleID = bundle.name.lower().replace(' ', '_')

                bundle.save()


                print('Bundle model object: {}'.format(bundle))

                # Build the bundle directory. If this fails remove the Bundle record,
                # otherwise that bundle gives a 500 on every page.
                try:
                    bundle.build_directory()
                except OSError as e:
                    print('Bundle directory creation failed: {}'.format(e))
                    bundle.delete()
                    form_bundle.add_error(None,
                        'ELSA could not create the bundle because of a server storage problem. '
                        'Please try again later or contact the ELSA team via the Contact page.')
                    return render(request, 'build/build.html', context_dict)
                print('Bundle directory: {}'.format(bundle.directory()))

                # Create Product_Bundle model.
                product_bundle = ProductBundleForm().save(commit=False)
                product_bundle.bundle = bundle
                product_bundle.save()
                print('product_bundle model object: {}'.format(product_bundle))

                # Build Product_Bundle label using the base case template found in
                # templates/pds4/basecase
                print('\n---------------Start Build Product_Bundle Base Case------------------------')
                product_bundle.build_base_case()  # simply copies basecase to user bundle directory
                # Open label - returns a list where index 0 is the label object and 1 is the tree
                print(' ... Opening Label ... ')
                # list = [label_object, label_root]
                label_list = open_label_with_tree(product_bundle.label())
                label_root = label_list[1]
                # Fill label - fills
                print(' ... Filling Label ... ')
                #label_root = bundle.version.fill_xml_schema(label_root)
                label_root = product_bundle.fill_base_case(
                    label_root)  # Fix for new data collections
                # Close label
                print(' ... Closing Label ... ')
                close_label(product_bundle.label(), label_root, label_list[2])

                print('---------------- End Build Product_Bundle Base Case -------------------------')

                if bundle.bundle_type == 'External':
                    # Look up by LID, not name. The name differs on prod, and a failed lookup gives a 500.
                    ama_investigation = Investigation.objects.filter(lid=AMA_LID).order_by('pk').first()
                    print(ama_investigation)
                    write_into_label(ama_investigation, product_bundle, [])

                    print('before initial save')
                    collections = form_collections.save(commit=False)
                    print('after initial save and before setting bundle')

                    collections.has_context = False
                    collections.has_xml_schema = False
                    collections.has_document = True

                    collections.bundle = bundle
                    print('after setting bundle and before final save')
                    collections.save()
                    print('here')
                    print('\nCollections model object:    {}'.format(collections))
                    print('now here')

                    # Create PDS4 compliant directories for each collection within the bundle.
                    print('before build collections directories')
                    collections.build_directories()
                    print('after build collections directories')

                    product_collection = form_product_collection_document.save(commit=False)
                    product_collection.bundle = bundle
                    product_collection.collection = 'Document'
                    product_collection.save()

                    label_list = open_label_with_tree(product_bundle.label())
                    label_root = label_list[1]
                    print(' ... Adding Bundle Member Entries ... ')
                    label_root = product_bundle.build_bundle_member_entry(
                        label_root, product_collection)
                    close_label(product_bundle.label(), label_root, label_list[2])
                    print(' ... Bundle Member Entry Added: {} ...'.format(product_collection.lid))


                    product_collection.build_base_case()

                    # Open Product_Collection label
                    print(' ... Opening Label ... ')
                    label_list = open_label_with_tree(
                        product_collection.label())
                    label_root = label_list[1]

                    # Fill label
                    print(' ... Filling Label ... ')
                    #label_root = bundle.version.fill_xml_schema(label_root)
                    label_root = product_collection.fill_base_case(label_root)

                    # The collection has its own Investigation_Area, so fill it too. fill_label picks
                    # 'collection_to_investigation' from the root tag.
                    if ama_investigation:
                        print(' ... Adding Investigation Area ... ')
                        label_root = ama_investigation.fill_label(label_root)

                    # Close label
                    print(' ... Closing Label ... ')
                    close_label(product_collection.label(), label_root, label_list[2])

                    # Write the inventory table. It's empty for now and gets rewritten when documents change.
                    product_collection.build_inventory()
                    print('-------------End Build Product_Collection Base Case-----------------')
                else:
                    print('before initial save')
                    collections = form_collections.save(commit=False)
                    print('after initial save and before setting bundle')

                    collections.has_context = True
                    collections.has_xml_schema = True
                    collections.has_document = True

                    collections.bundle = bundle
                    print('after setting bundle and before final save')
                    collections.save()
                    print('here')
                    print('\nCollections model object:    {}'.format(collections))
                    print('now here')

                    # Create PDS4 compliant directories for each collection within the bundle.
                    print('before build collections directories')
                    collections.build_directories()
                    print('after build collections directories')

                    for collection in collections.list():
                        print(collection)

                        # Create Product_Collection model for each collection
                    
                        if collection == 'document':
                            product_collection = form_product_collection_document.save(commit=False)
                            product_collection.bundle = bundle
                            product_collection.collection = 'Document'
                        elif collection == 'context':
                            product_collection = form_product_collection_context.save(commit=False)
                            product_collection.bundle = bundle
                            product_collection.collection = 'Context'
                        elif collection == 'xml_schema':
                            product_collection = form_product_collection_schema.save(commit=False)
                            product_collection.bundle = bundle
                            product_collection.collection = 'XML_Schema'
                        product_collection.save()

                        # Fill Product_Bundle with Collection Bundle Member Entries
                        # list = [label_object, label_root]
                        label_list = open_label_with_tree(product_bundle.label())
                        label_root = label_list[1]
                        print(' ... Adding Bundle Member Entries ... ')
                        label_root = product_bundle.build_bundle_member_entry(
                            label_root, product_collection)
                        close_label(product_bundle.label(), label_root, label_list[2])
                        print(' ... Bundle Member Entry Added: {} ...'.format(product_collection.lid))

                        # Build Product_Collection label for all labels other than those found in the data collection.
                        print('-------------Start Build Product_Collection Base Case-----------------')
                        if collection != 'data':
                            product_collection.build_base_case()

                            # Open Product_Collection label
                            print(' ... Opening Label ... ')
                            label_list = open_label_with_tree(
                                product_collection.label())
                            label_root = label_list[1]

                            # Fill label
                            print(' ... Filling Label ... ')
                            #label_root = bundle.version.fill_xml_schema(label_root)
                            label_root = product_collection.fill_base_case(label_root)

                            # Close label
                            print(' ... Closing Label ... ')
                            close_label(product_collection.label(), label_root, label_list[2])

                            # Write the inventory table. Every PDS4 collection needs one, Archive too.
                            product_collection.build_inventory()
                            print('-------------End Build Product_Collection Base Case-----------------')

                # Further develop context_dict entries for templates
                context_dict['Bundle'] = bundle
                context_dict['Product_Bundle'] = Product_Bundle.objects.get(
                    bundle=bundle)
                context_dict['Product_Collection_Set'] = Product_Collection.objects.filter(
                    bundle=bundle)

                # Last, once every collection label exists, so the entry reaches them all.
                _create_initial_modification_history(bundle)

                # url = smart_str(bundle.id) + '/' + 'alias' 

                # print('trying to trigger server reset')
                # return redirect(url, request, context_dict)

            # This form already made this bundle (see build/creation.py).
            creation.remember(request, create_token, bundle)
            creation.mark_just_created(request, bundle)

            # Determine where to redirect based on walkthrough checkbox
            if enable_walkthrough:
                request.session['walkthrough_bundle_type'] = bundle.bundle_type #Rupak
                print('Walkthrough enabled: redirecting to the setup page.')
                return redirect(walkthrough.setup_url(bundle))
            else:
                # No walkthrough, so just go to the bundle page.
                print('Walkthrough not enabled: redirecting to the bundle page.')
                messages.success(request, 'Bundle "{}" created and saved to your Bundle Hub.'.format(bundle.name))
                return redirect(walkthrough.bundle_url(bundle))
        else:
            print('failed uniqueness')
            own = Bundle.objects.filter(name__iexact=bundle_name, user=request.user).first()
            if own is not None:
                # Name is already theirs, so tell them and link to it.
                form_bundle.add_error('name', 'You already have a bundle named "{}". Choose another name '
                                              'to create a new one.'.format(own.name))
                context_dict['own_existing'] = own
                context_dict['own_existing_url'] = (walkthrough.setup_url(own) if walkthrough.required_left(own)
                                                    else walkthrough.bundle_url(own))
            else:
                form_bundle.add_error('name', 'A bundle named "{}" already exists in ELSA. Bundle names have to be unique, because the name becomes the bundle\'s identifier. Please choose another.'.format(bundle_name))

    # A fresh key each time the form is shown (build/creation.py).
    context_dict['create_token'] = creation.issue_token(request)
    context_dict['discarded'] = request.session.pop('bundle_discarded', None)
    return render(request, 'build/build.html', context_dict)


def _create_initial_modification_history(bundle):
    """Add the first Modification History entry for a new bundle: 1.0, today, Bundle creation."""
    if Modification_History.objects.filter(bundle=bundle).exists():
        return
    mod_history = Modification_History(
        bundle=bundle,
        description='Bundle creation',
        modification_date=datetime.date.today().strftime('%Y-%m-%d'),
        version_id='1.0',
    )
    mod_history.save()
    product_bundle = Product_Bundle.objects.get(bundle=bundle)
    write_into_label(mod_history, product_bundle, bundle_label_targets(bundle))


# Old walkthrough intro pages, kept so old links still work.
@login_required
def yes_intro_page(request, bundle_id):
    bundle = get_object_or_404(Bundle, pk=bundle_id)
    if request.user != bundle.user:
        return redirect('main:restricted_access')
    return redirect(walkthrough.setup_url(bundle))


@login_required
def no_intro_page(request, bundle_id):
    bundle = get_object_or_404(Bundle, pk=bundle_id)
    if request.user != bundle.user:
        return redirect('main:restricted_access')
    return redirect(walkthrough.bundle_url(bundle))


@login_required
def setup(request, pk_bundle):
    """Guided setup, all the walkthrough steps on one page.

    GET only. Each step saves through the same view as the bundle page (see build/bundle_setup.py),
    except the citation, which has setup_citation.
    """
    bundle = Bundle.objects.filter(pk=pk_bundle).first()
    if bundle is None:
        # Usually discarded a moment ago and reached again with the Back button.
        messages.info(request, 'That bundle no longer exists.')
        return redirect('friends:bundle_hub')
    if request.user != bundle.user:
        return redirect('main:restricted_access')
    try:
        Product_Bundle.objects.get(bundle=bundle)
    except Product_Bundle.DoesNotExist:
        # Creation crashed partway; the bundle page sends these back to the hub too.
        return HttpResponseRedirect(reverse('friends:bundle_hub'))
    label_upgrades.safe_upgrade(bundle, source=label_upgrades.SOURCE_PAGE)
    context = bundle_setup.context(bundle)
    context['just_created'] = creation.pop_just_created(request, bundle)
    context['can_discard'] = walkthrough.is_untouched(bundle)
    return render(request, 'build/setup/setup.html', context)


@login_required
@require_POST
def setup_discard(request, pk_bundle):
    """Discard a new bundle with nothing in it, from the setup page.

    Anything with content has to be deleted from the Bundle Hub.
    """
    bundle = Bundle.objects.filter(pk=pk_bundle).first()
    if bundle is None:
        return redirect('build:build')
    if request.user != bundle.user:
        return redirect('main:restricted_access')
    if not walkthrough.is_untouched(bundle):
        messages.error(request, '"{}" already has content, so it was not discarded. If you no longer need it, '
                                'delete it from your Bundle Hub.'.format(bundle.name))
        return redirect(walkthrough.setup_url(bundle))
    name = bundle.name
    try:
        # Same as the Bundle Hub delete: remove the folder first, then the records.
        bundle.remove_bundle()
        Bundle.objects.filter(pk=bundle.pk).delete()
    except Exception:
        import logging
        logging.getLogger(__name__).exception('discarding bundle %s for %s failed', pk_bundle, request.user.username)
        messages.error(request, 'ELSA could not discard "{}". Please try again, or delete it from your '
                                'Bundle Hub.'.format(name))
        return redirect(walkthrough.setup_url(bundle))
    request.session['bundle_discarded'] = name
    messages.success(request, 'Discarded "{}". Nothing was kept, and the name is free again.'.format(name))
    return redirect('build:build')


@login_required
def setup_citation(request, pk_bundle):
    """Save the citation from the setup page.

    Answers in JSON when the page posts it. A plain post goes back to the setup page.
    """
    bundle = get_object_or_404(Bundle, pk=pk_bundle)
    if request.user != bundle.user:
        return redirect('main:restricted_access')
    if request.method != 'POST':
        return redirect(walkthrough.setup_url(bundle, 'citation'))

    form = bundle_setup.CitationListForm(request.POST, bundle=bundle)
    in_place = _is_panel_request(request)
    # The bundle page's citation window posts here too and goes back to the bundle page.
    from_bundle = walkthrough.returning_to_bundle(request)
    if not form.is_valid():
        if in_place:
            return _inplace_refused(form)
        for message in form.non_field_errors() or ['The citation was not saved. Please check it and try again.']:
            messages.error(request, message)
        return redirect(walkthrough.bundle_url(bundle) if from_bundle
                        else walkthrough.setup_url(bundle, 'citation'))

    try:
        # All or nothing, so the database and the labels don't disagree about the citation.
        with transaction.atomic():
            bundle_setup.save_citation(bundle, form)
    except (OSError, LxmlSyntaxError, Product_Bundle.DoesNotExist) as error:
        print('setup_citation: could not write the labels of bundle {}: {}'.format(bundle.pk, error))
        message = ('The citation could not be saved because ELSA could not update this '
                   'bundle\'s labels. Nothing was changed. Please try again, or contact the '
                   'ELSA team via the Contact page if it keeps happening.')
        if in_place:
            return _inplace_refused(None, message)
        messages.error(request, message)
        return redirect(walkthrough.bundle_url(bundle) if from_bundle
                        else walkthrough.setup_url(bundle, 'citation'))
    messages.success(request, 'Citation saved.')
    following = (walkthrough.bundle_url(bundle) if from_bundle
                 else walkthrough.next_url(bundle, 'citation'))
    if in_place:
        return JsonResponse({'ok': True, 'redirect': following})
    return redirect(following)


@login_required
def data_prep(request, bundle, data_enum):

    data = Data.objects.get(pk=bundle)
    bundle = Bundle.objects.get(pk=bundle)
    if request.user != bundle.user:
        return redirect('main:restricted_access')
    data_prep_form = DataPrepForm

    '''
    The following two lines of code are confusing nonsense garbage that took me at least five weeks to figure
    out, this is what they do as far as I understand. The first line creates a formset object using the data
    prep form and the data prep model. It also prevents the bundle field from showing and makes triple sure
    that it only shows the number of forms we want. The second line actually creates the formset that gets
    displayed and gets the information for us using the formset object we just created. The first input is
    the standard form stuff. The second input uses an object query to get the data_prep object associated
    with the bundle  -J
    '''
    DataPrepFormSet = modelformset_factory(Data_Prep, data_prep_form, exclude=(
        'bundle',), extra=data.data_enum, max_num=data.data_enum, min_num=data.data_enum)
    formset = DataPrepFormSet(request.POST or None,
                              queryset=Data_Prep.objects.filter(bundle=bundle))

    context_dict = {
        'bundle': bundle,
        'DataPrepFormSet': DataPrepFormSet,
        'formset': formset,
    }

    print(data.directory())

    if request.method == 'POST' and formset.is_valid():
        print("POST and valid")
        for form in formset:
            data_form = form.save()
            if data_form.name[:5] != "data_":
                data_form.name = "data_"+data_form.name
            data_form.bundle = bundle

            # Create the actual data entries in the databasae
            print(data_form.data_type)
            if data_form.data_type == 'Table Delimited':
                actual_data = Table_Delimited(
                    name=data_form.name, bundle=bundle)
                actual_data.save()
            elif data_form.data_type == 'Table Binary':
                actual_data = Table_Binary(name=data_form.name, bundle=bundle)
                actual_data.save()
            elif data_form.data_type == 'Table Fixed-Width':
                actual_data = Table_Fixed_Width(
                    name=data_form.name, bundle=bundle)
                actual_data.save()

            # Create the data stubs
            data_form.build_data_directory()
            data_form.build_base_case()

            data_form.save()
        return render(request, 'build/two.html', context_dict)

    return render(request, 'build/data_prep/data_prep.html', context_dict)


# -- forms on the bundle page that save in place --
# 
# The page posts these itself (templates/build/bundle/_inplace_forms.html) and names the form in
# X-Elsa-Inplace. A refusal comes back as form errors in the open window. A success comes back
# as the address to go to. Without JavaScript nothing changes.

def _inplace_form(request):
    """The name of the bundle-page form saving itself in place, or None."""
    if request.method != 'POST' or not _is_panel_request(request):
        return None
    return request.headers.get('X-Elsa-Inplace') or None


def _inplace_refused(form, message=''):
    """A refused in-place save: the form's own errors, keyed by field name."""
    errors = form.errors.get_json_data() if form is not None else {}
    if not errors:
        errors = {'__all__': [{'message': message or 'That could not be saved. Please check the form and try again.', 'code': ''}]}
    return JsonResponse({'ok': False, 'errors': errors}, status=400)


def inplace_redirects(view):
    """Turn a redirect into JSON when the bundle page posted in place.

    fetch() would follow the redirect and use up the toast before the real page loads.
    """
    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        response = view(request, *args, **kwargs)
        if _inplace_form(request) and response.status_code in (301, 302, 303):
            return JsonResponse({'ok': True, 'redirect': response['Location']})
        return response
    return wrapper


# The bundle_detail view is the page that details a specific bundle.
@login_required
@inplace_redirects
def bundle(request, pk_bundle):
    # Get Bundle
    bundle = Bundle.objects.get(pk=pk_bundle)
    # Secure ELSA by seeing if the user logged in is the same user associated with the Bundle
    if request.user == bundle.user:
        print('authorized user: {}'.format(request.user))
        # ELSA's current user is the bundle user so begin view logic

        print(' \n\n \n\n----------------------------------------------------------------------\n')
        print('-----------------------BEGIN Bundle Detail VIEW--------------------------.\n')
        print('--------------------------------------------------------------------------\n')

        # get set of aliases associated with the bundle
        alias_set = Alias.objects.filter(bundle=bundle)

        # Each collection carries its own NetCDF list and AMA status; see _collections_with_ama.
        additional_collections_set = _collections_with_ama(bundle)

        # get citation information associated with bundle
        # citation_information_set = Citation_Information.objects.filter(bundle=bundle)

        citation_information_set = Citation_Information.objects.filter(bundle=bundle)

        # A failed bundle creation has no Product_Bundle, so go back to the hub instead of a 500.
        # This has to stay above the first access.
        try:
            product_bundle = Product_Bundle.objects.get(bundle=bundle)
        except Product_Bundle.DoesNotExist:
            print('Bundle {} has no Product_Bundle (creation likely failed); redirecting.'.format(bundle.pk))
            return HttpResponseRedirect(reverse('friends:bundle_hub'))
        # Bring old labels up to date before anything reads them.
        label_upgrades.safe_upgrade(bundle, source=label_upgrades.SOURCE_PAGE)
        # Bad or missing bundle label shouldn't break the page. Fall back to the DB values.
        citation_xml = None
        try:
            label_list = open_label_with_tree(product_bundle.label())
            label_root = label_list[1]
            identification_area = label_root.find('{}Identification_Area'.format(NAMESPACE))
            if identification_area is not None:
                citation_xml = identification_area.find('{}Citation_Information'.format(NAMESPACE))
        except Exception as e:
            print('bundle view: could not read bundle label for citation display: {}'.format(e))

        for citation_information in citation_information_set:
            citation_information.display_authors = []
            citation_information.display_editors = []

            if citation_xml is not None:
                list_author = citation_xml.find('{}List_Author'.format(NAMESPACE))

                if list_author is not None:
                    for person in list_author.findall('{}Person'.format(NAMESPACE)):
                        given = person.findtext('{}given_name'.format(NAMESPACE), default='')
                        family = person.findtext('{}family_name'.format(NAMESPACE), default='')
                        affiliation = person.find('{}Affiliation'.format(NAMESPACE))
                        orcid = person.findtext('{}person_orcid'.format(NAMESPACE), default='')

                        organization = ''
                        if affiliation is not None:
                            organization = affiliation.findtext('{}organization_name'.format(NAMESPACE), default='')

                        citation_information.display_authors.append({
                            'type': 'Person',
                            'name': f'{given} {family}'.strip(),
                            'organization': organization,
                            'orcid': orcid,
                        })

                    for org in list_author.findall('{}Organization'.format(NAMESPACE)):
                        org_name = org.findtext('{}organization_name'.format(NAMESPACE), default='')
                        parent_org = org.find('{}Parent_Organization'.format(NAMESPACE))

                        parent_name = ''
                        if parent_org is not None:
                            parent_name = parent_org.findtext('{}parent_organization_name'.format(NAMESPACE), default='')

                        citation_information.display_authors.append({
                            'type': 'Organization',
                            'name': org_name,
                            'organization': parent_name,
                        })

                    list_editor = citation_xml.find('{}List_Editor'.format(NAMESPACE))

                    if list_editor is not None:

                        # Editor people
                        for person in list_editor.findall('{}Person'.format(NAMESPACE)):
                            given = person.findtext(
                                '{}given_name'.format(NAMESPACE),
                                default=''
                            )

                            family = person.findtext(
                                '{}family_name'.format(NAMESPACE),
                                default=''
                            )

                            orcid = person.findtext(
                                '{}person_orcid'.format(NAMESPACE),
                                default=''
                            )

                            affiliation = person.find(
                                '{}Affiliation'.format(NAMESPACE)
                            )

                            organization = ''

                            if affiliation is not None:
                                organization = affiliation.findtext(
                                    '{}organization_name'.format(NAMESPACE),
                                    default=''
                                )

                            citation_information.display_editors.append({
                                'type': 'Person',
                                'name': f'{given} {family}'.strip(),
                                'organization': organization,
                                'orcid': orcid,
                            })

                        # Editor organizations
                        for organization_element in list_editor.findall(
                            '{}Organization'.format(NAMESPACE)
                        ):
                            organization_name = organization_element.findtext(
                                '{}organization_name'.format(NAMESPACE),
                                default=''
                            )

                            organization_rorid = organization_element.findtext(
                                '{}organization_rorid'.format(NAMESPACE),
                                default=''
                            )

                            sequence_number = organization_element.findtext(
                                '{}sequence_number'.format(NAMESPACE),
                                default=''
                            )

                            parent_organization = organization_element.find(
                                '{}Parent_Organization'.format(NAMESPACE)
                            )

                            parent_organization_name = ''

                            if parent_organization is not None:
                                parent_organization_name = parent_organization.findtext(
                                    '{}parent_organization_name'.format(NAMESPACE),
                                    default=''
                                )

                            # Do not display completely empty organization elements
                            if (
                                organization_name
                                or organization_rorid
                                or sequence_number
                                or parent_organization_name
                            ):
                                citation_information.display_editors.append({
                                    'type': 'Organization',
                                    'name': organization_name,
                                    'rorid': organization_rorid,
                                    'sequence_number': sequence_number,
                                    'parent_organization': parent_organization_name,
                                })

                    citation_information.display_editor_people_count = sum(
                        1 for editor in citation_information.display_editors
                        if editor.get('type') == 'Person'
                    )

                    citation_information.display_editor_organization_count = sum(
                        1 for editor in citation_information.display_editors
                        if editor.get('type') == 'Organization'
                    )

        modification_history_set = Modification_History.objects.filter(bundle=bundle)
        # get set of data collections currently associated with the bundle
        data_set = Data.objects.filter(bundle=bundle)
        print('---DEBUG--- Data set: {}'.format(data_set))

        # get set of observational products currently associated with the bundle
        product_observational_set = []    
        if len(data_set) > 0:
            for data in data_set:
                product_observational_set.extend(Product_Observational.objects.filter(data=data))

        # Forms present on bundle detail page
        # form_netcdf = NetCDFForm(request.POST or None, request.FILES or None) # To handle NetCDF files
        #form_netcdf = MultipleNetCDFUploadForm(request.POST, request.FILES) # To handle NetCDF files
        if request.method == 'POST':
            form_netcdf = MultipleNetCDFUploadForm(request.POST, request.FILES)
        else:
            form_netcdf = MultipleNetCDFUploadForm()

        form_alias = AliasForm(request.POST or None) 
        form_bundle = BundleForm(request.POST or None) 
        form_citation_information = CitationInformationForm(
            request.POST or None,
            initial={'publication_year': timezone.now().year},
            bundle=bundle,
        )
        form_modification_history = ModificationHistoryForm(request.POST or None)     
        form_data = DataForm(request.POST or None, pk_bun=pk_bundle)
        # With the files too, so a refused upload's reason is what the window shows.
        form_document = ProductDocumentForm(request.POST or None, request.FILES or None, bundle=bundle)
        annex_form_document = AnnexProductDocumentForm(request.POST or None, bundle=bundle)
        form_collections = CollectionsForm(request.POST or None)
        form_product_collection = ProductCollectionForm(request.POST or None)
        form_additional_collections = AdditionalCollectionForm(request.POST or None, bundle=bundle)
        form_investigation = InvestigationForm(request.POST or None)
        form_target = TargetFormAll(request.POST or None, pk_bundle=pk_bundle)
        form_instrument_host = InstrumentHostForm(request.POST or None, pk_inv=None)
        form_facility = FacilityForm(request.POST or None)
        # Own id prefix because the Data Product form on this page also has id_name.
        context_products_contact = ContextProductsContactForm(request.POST or None, auto_id='contact_%s')
        contact_form = ContactForm(request.POST or None)

        # creating sets of objects associated with the bundle to add to the bundle progress checklist
        investigations_set = bundle.investigations.all()
        targets_set = bundle.targets.all()
        instrument_hosts_set = bundle.instrument_hosts.all()
        facilities_set = bundle.facilities.all()
        telescopes_set = bundle.telescopes.all()
        hosts_set = False
        if (instrument_hosts_set.exists() or
            facilities_set.exists() or
            telescopes_set.exists()):
            # If there are instrument hosts, facilities, or telescopes, we can get the instruments
            hosts_set = True
            
        instruments_set = bundle.instruments.all()
        product_observational_set = Product_Observational.objects.filter(data__bundle=bundle)


        instrument_host_investigations = []
        for instrument_host in bundle.instrument_hosts.all():
            instrument_host_investigations.append(instrument_host.investigations.last())      

        instrument_investigations = []
        for instrument in bundle.instruments.all():
            instrument_investigations.append(instrument.investigations.last())

        facility_investigations = []
        for facility in bundle.facilities.all():
            facility_investigations.append(facility.investigations.last())

        telescope_investigations = []
        for telescope in bundle.telescopes.all():
            telescope_investigations.append(telescope.investigations.last())

        file_tree = _bundle_file_tree(bundle)

        table_set = []
        print(Table_Binary.objects.filter(bundle=bundle))
        table_set.append(Table_Delimited.objects.filter(bundle=bundle))
        table_set.append(Table_Binary.objects.filter(bundle=bundle))
        table_set.append(Table_Fixed_Width.objects.filter(bundle=bundle))

        # Context dictionary for template
        context_dict = {
            'bundle':bundle,
            'alias_set':alias_set,
            'alias_set_count':len(alias_set), 
            'citation_information_set':citation_information_set,  
            'citation_information_set_count':len(citation_information_set),    
            'modification_history_set':modification_history_set,  
            'modification_history_set_count':len(modification_history_set),     
            'data_set':data_set,
            'table_set': table_set,
            'form_alias':form_alias,
            'form_bundle':form_bundle,
            'form_citation_information':form_citation_information,
            'form_data':form_data,
            'form_modification_history':form_modification_history,
            'form_document':form_document,
            'annex_form_document':annex_form_document,
            'form_collections':form_collections,
            'form_product_collection': form_product_collection,
            'form_additional_collections': form_additional_collections,
            'additional_collections_count': len(additional_collections_set),
            'investigations': bundle.investigations.all(),
            'form_investigation': form_investigation,
            'form_target': form_target,
            'form_instrument_host': form_instrument_host,
            'instrument_hosts': bundle.instrument_hosts.all(),
            'form_facility': form_facility,
            'facilities': bundle.facilities.all(),
            'telescopes': bundle.telescopes.all(),
            'instruments': bundle.instruments.all(),
            'instrument_host_investigations': instrument_host_investigations,
            'instrument_investigations': instrument_investigations,
            'facility_investigations': facility_investigations,
            'telescope_investigations': telescope_investigations,
            'targets': bundle.targets.all(),
            'product_observational_set':product_observational_set,
            'documents':Product_Document.objects.filter(bundle=bundle),
            'additional_collections_set': additional_collections_set,
            # Listed on their own rather than inside a collection: they belong to none.
            'unassigned_netcdf_files': _unassigned_netcdf_files(bundle),
            'user':request.user,
            'context_products_contact' : context_products_contact,
            'contact_form' : contact_form,
            'context_successful_submit': False,
            'additional_collection_successful_submit': False,
            'bundle_type': bundle.bundle_type,
            
            # To handle NetCDF files
            'form_netcdf': form_netcdf,

        }

        # Compute status for bundle progress checklist
        status_dict = {
            'Alias': bundle.alias_set.exists(),
            'Citation_Information': bundle.citation_information_set.exists(),
            'Modification_History': bundle.modification_history_set.exists(),
            'Investigations': investigations_set.exists(),
            'Targets': targets_set.exists(),
            'Instruments': instruments_set.exists(),
            'Hosts': hosts_set,
            'Product_Observational': product_observational_set.exists(),
        }

        context_dict['status_dict'] = status_dict
        context_dict['file_tree'] = file_tree
        # Where the team is with a submitted bundle, and what they last said.
        context_dict['review_state'] = staff_hooks.review_state(bundle)
        # Lets the create page know a bundle was made, in case the user goes Back to it.
        context_dict['just_created'] = creation.pop_just_created(request, bundle)

        context_dict.update(validation_context(bundle, request.user))

        # To handle NetCDF files
        # if form_netcdf.is_valid():
        #     netcdf_obj = form_netcdf.save(commit=False)
        #     netcdf_obj.bundle = bundle
        #     netcdf_obj.save()

        #     print('before call')

        #     variable_coord_to_product(bundle=bundle)

        #     print('after call')
    
        #     return HttpResponseRedirect('/elsa/build/' + str(bundle.pk) + '/')

        if request.method == 'POST':
            if form_netcdf.is_valid():
                files = form_netcdf.cleaned_data['netcdf_files']
                collection = form_netcdf.cleaned_data['collection']

                # Scoped to this bundle since collection_name isn't unique across users.
                netcdf_collection = AdditionalCollections.objects.filter(
                    bundle=bundle, collection_name=collection).first()

                if netcdf_collection is None:
                    return JsonResponse(
                        {'error': 'Could not tell which collection this upload belongs to. '
                                  'Please reload the page and try again.'},
                        status=400
                    )

                netcdf_collection_directory = netcdf_collection.directory()
                # Make sure the folder exists, otherwise os.rename fails.
                os.makedirs(netcdf_collection_directory, exist_ok=True)


                # Refuse the upload if the disk can't hold it plus a margin. The page checks first, this is
                # the backstop. The code tells the page to open its storage dialog.
                from build import storage_report
                total_upload = sum(getattr(f, 'size', 0) for f in files)
                space = storage_report.check_space(total_upload)
                if not space['ok']:
                    print('Upload rejected: needs {} bytes, {} usable'.format(total_upload, space['available']))
                    return JsonResponse(
                        {'error': 'The server does not have enough storage space for this upload right now. '
                                  'Nothing was added to your bundle.',
                         'code': 'insufficient_storage',
                         'needed': storage_report.human_size(space['needed']),
                         'available': storage_report.human_size(space['available']),
                         'available_bytes': space['available']},
                        status=507
                    )

                created_objs = []
                # Two uploads with the same name in a collection would overwrite the file and its label, so refuse.
                # Compare sanitised names since that's what lands on disk ("a - Copy.nc" becomes "a_-_Copy.nc").
                # The message uses the name the user chose.
                storage = NetCDFFile._meta.get_field('file').storage
                existing_names = {
                    storage.get_valid_name(os.path.basename(name)) for name in
                    NetCDFFile.objects.filter(collection=netcdf_collection)
                    .values_list('file', flat=True) if name
                }
                seen_in_this_upload = set()
                for f in files:
                    incoming = storage.get_valid_name(os.path.basename(f.name))
                    if incoming in existing_names or incoming in seen_in_this_upload:
                        return JsonResponse(
                            {'error': 'A file named "{}" is already in the "{}" collection. '
                                      'Delete it first if you want to replace it, or rename the '
                                      'file, since two files cannot share a name inside one '
                                      'collection.'.format(os.path.basename(f.name),
                                                           netcdf_collection.collection_name)},
                            status=400
                        )
                    # Also refuses a name that would overwrite a label or inventory in the folder.
                    conflict = netcdf_uploads.name_conflict(
                        netcdf_collection, os.path.basename(f.name), incoming)
                    if conflict:
                        return JsonResponse({'error': conflict}, status=400)
                    seen_in_this_upload.add(incoming)

                try:
                    for f in files:
                        if not is_netcdf_file(f):
                            return JsonResponse(
                                {'error': '"{}" is not a valid NetCDF file. Only NetCDF classic, 64-bit, or HDF5-based files are accepted.'.format(f.name)},
                                status=400
                            )

                        file_title = f.name
                        if len(file_title) > 100:
                            file_title = file_title[:100]

                        # Create the NetCDFFile object using your model's fields
                        netcdf_obj = NetCDFFile(
                            bundle=bundle,
                            collection=netcdf_collection,
                            file=f,
                            title=file_title,
                        )

                        netcdf_obj.save()
                        created_objs.append(netcdf_obj)
                except OSError as e:
                    print('NetCDF upload failed writing to disk: {}'.format(e))
                    return JsonResponse(
                        {'error': 'The server could not store the uploaded file (storage problem). '
                                  'Please try again later or contact the ELSA team via the Contact page.'},
                        status=507
                    )


                if created_objs:
                    # Process only the files just uploaded for this bundle.
                    errors = variable_coord_to_product(bundle, created_objs, netcdf_collection_directory)
                    if errors:
                        # Files are saved but metadata extraction failed; tell the user which.
                        return JsonResponse(
                            {'error': 'Uploaded, but metadata could not be extracted from: '
                                      + '; '.join(errors)},
                            status=400
                        )

                # Upload is XHR and the page reloads itself, so the reply says which collection and files
                # to show. The plain form path without JS keeps its redirect.
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse(_netcdf_upload_reply(bundle, netcdf_collection, created_objs))

                return HttpResponseRedirect('/elsa/build/' + str(bundle.pk) + '/')
            elif (request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                  and not request.headers.get('X-Elsa-Inplace')
                  and 'collection' in request.POST
                  and 'netcdf_files' in form_netcdf.errors):
                # No files in the upload. The page normally stops this before sending.
                return JsonResponse({'error': 'Choose at least one NetCDF file to upload.'}, status=400)

        if form_investigation.is_valid():
            print(form_investigation.cleaned_data['investigation'].file_ref)
            print(Investigation.objects.filter(file_ref=form_investigation.cleaned_data['investigation'].file_ref).first().file_ref)
            i = Investigation.objects.filter(file_ref=form_investigation.cleaned_data['investigation'].file_ref).first()
            # i = Investigation.objects.get(
            #     file_ref=form_investigation.cleaned_data['investigation'].file_ref)
            context_dict['investigation'] = i
            bundle.investigations.add(i)
            print(i)

            # Label Fix for context products - Said
            product_bundle = Product_Bundle.objects.get(bundle=bundle)
            # Ok this gets confusing, and will add documentation later. Let me briefly explain here. - Said
            # Querying DB for product_collection objects such as document, context, and so on. Need a separate query for additional collections, so I do that query.
            # Chain(query1, query2), creates a pseudo-union of both queries, then allows write_into_label to write into all necessary labels.
            product_collections_list = chain(Product_Collection.objects.filter(bundle=bundle).exclude(collection='Data'), AdditionalCollections.objects.filter(bundle=bundle))

            write_into_label(i, product_bundle, product_collections_list)

            context_dict['context_successful_submit'] = True

            messages.success(request, 'Investigation "{}" added.'.format(i.name))
            return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/')


        # Reopen the citation window if its post was refused. Only for the citation form,
        # since every form on this page is bound to every POST.
        if (request.method == 'POST' and 'number_of_authors_people' in request.POST
                and not form_citation_information.is_valid()):
            context_dict['reopen_citation_modal'] = True

        # After ELSAs friend hits submit, if the forms are completed correctly, we should enter
        # this conditional.
        if form_citation_information.is_valid():
            print('\n\n----------------- CITATION_INFORMATION INFO -------------------------')
            print('form_citation_information is valid')
            # Create Citation_Information model object
            citation_information = form_citation_information.save(commit=False)
            citation_information.bundle = bundle
            citation_information.save()
            print("CITATION SAVE SUCCESSFUL")
            print('Citation Information model object: {}'.format(citation_information))

            product_bundle = Product_Bundle.objects.get(bundle=bundle)
            product_collections_list = Product_Collection.objects.filter(bundle=bundle).exclude(collection='Data')

            print(product_collections_list)

            write_into_label(citation_information, product_bundle, product_collections_list)

            print('------------- End Build Citation Information -------------------')     

            # Update context_dict with the current Citation_Information models associated with the user's bundle
            citation_information_set = Citation_Information.objects.filter(bundle=bundle)
            context_dict['citation_information_set'] = citation_information_set
            context_dict['citation_information_set_count'] = len(citation_information_set)
            form_citation_information = CitationInformationForm()
            context_dict['form_citation_information'] = form_citation_information

            # # fixes the refresh duplication issue - deric
            # return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/citation_information_current/' + str(citation_information.pk) + '/')
            
            next_page = request.GET.get('next')

            if next_page == 'bundle':
                return redirect(reverse('build:bundle', args=[pk_bundle]))
            else:
                return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/citation_information_current/' + str(citation_information.pk) + '/')

            # fixes the refresh duplication issue, use this one for offline testing - deric
            # return HttpResponseRedirect('/build/' + pk_bundle + '/')

        if form_modification_history.is_valid():
            print('form_modification_history is valid')
            # Create modification_history model object
            modification_history = form_modification_history.save(commit=False)
            modification_history.bundle = bundle
            modification_history.save()
            print(' Modification History  model object: {}'.format(modification_history))

            product_bundle = Product_Bundle.objects.get(bundle=bundle)
            product_collections_list = Product_Collection.objects.filter(bundle=bundle).exclude(collection='Data')

            write_into_label(modification_history, product_bundle, product_collections_list)

            print('------------- End Build  Modification History  -------------------')

            # Update context_dict with the current  Modification History  models associated with the user's bundle
            modification_history_set = Modification_History.objects.filter(bundle=bundle)
            context_dict['modification_history_set'] = modification_history_set
            context_dict['modification_history_set_count'] = len(modification_history_set)

            messages.success(request, 'Modification history added.')
            # # fixes the refresh duplication issue - deric
            return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/')

            # fixes the refresh duplication issue, use this one for offline testing - deric
            # return HttpResponseRedirect('/build/' + pk_bundle + '/')         

        additional_collections_list = []
        if form_additional_collections.is_valid():
            additional_collections = form_additional_collections.save(commit=False)
            additional_collections.bundle = bundle
            additional_collections.append_list()
            additional_collections.save()
            additional_collections.build_directories()
            additional_collections_list = additional_collections.list()

            product_bundle = Product_Bundle.objects.get(bundle=bundle)

            # Fill Product_Bundle with Collection Bundle Member Entries
            label_list = open_label_with_tree(product_bundle.label()) #list = [label_object, label_root]
            label_root = label_list[1]
            print(' ... Adding Bundle Member Entries ... ')
            label_root = product_bundle.build_additional_bundle_member_entry(label_root, additional_collections)
            close_label(product_bundle.label(), label_root, label_list[2])
            
            additional_collections.build_base_case()

            # Open Product_Collection label
            print(' ... Opening Label ... ')
            label_list = open_label_with_tree(additional_collections.label())
            label_root = label_list[1]

            # Fill label
            print(' ... Filling Label ... ')
            #label_root = bundle.version.fill_xml_schema(label_root)
            label_root = additional_collections.fill_base_case(label_root)

            # Close label
            print(' ... Closing Label ... ')
            close_label(additional_collections.label(), label_root, label_list[2])
            print('-------------End Build Product_Collection Base Case-----------------')

            # Fill out context products in new additional collection
            # Also there should be a better way to do this, probably make a function in choclate.py, have to refactor
            context_products = []
            context_products.extend(bundle.investigations.all())
            context_products.extend(bundle.instrument_hosts.all())
            context_products.extend(bundle.facilities.all())
            context_products.extend(bundle.instruments.all())
            context_products.extend(bundle.telescopes.all())
            context_products.extend(bundle.targets.all())
            for context_product in context_products:
                write_into_label(context_product, additional_collections, None)

            # write investigation products - Said
            for citation_information in citation_information_set:
                write_into_label(citation_information, additional_collections, None)
            for alias in alias_set:
                write_into_label(alias, additional_collections, None)
            for modification_history in modification_history_set:
                write_into_label(modification_history, additional_collections, None)

            # Write the inventory. Every PDS4 collection needs one or validate gives up on the product.
            additional_collections.build_inventory()

            additional_collections_set = _collections_with_ama(bundle)
            context_dict['additional_collections_set'] = additional_collections_set
            context_dict['additional_collections_count'] =  len(additional_collections_set)

            context_dict['additional_collection_successful_submit'] = True
            # return render(request, 'build/bundle/bundle.html', context_dict)

            # # fixes the refresh duplication issue - deric
            # Open the new collection's tab.
            return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/#additional_collection_' + str(additional_collections.pk))

            # fixes the refresh duplication issue, use this one for offline testing - deric
            # return HttpResponseRedirect('/build/' + pk_bundle + '/')

        # After ELSAs friend hits submit, if the forms are completed correctly, we should enter
        # this conditional.  We must do [] things: 1. Create the Document model object, 2. Add a Product_Document label to the Document Collection, 3. Add the Document as an Internal_Reference to the proper labels (like Product_Bundle and Product_Collection).
        
        if bundle.bundle_type == "External":
            form_document = AnnexProductDocumentForm(request.POST or None, request.FILES or None, bundle=bundle)
        else:
            form_document = ProductDocumentForm(request.POST or None, request.FILES or None, bundle=bundle)
            
        if form_document.is_valid():
            print('\n\n---------------------- DOCUMENT INFO -------------------------------')
            print('form_product_document is valid')  

            # Create Document Model Object
            product_document = form_document.save(commit=False)
            product_document.bundle = bundle
            product_document.save()
            # The file itself, before the label is built, so the label names it.
            document_files.store(product_document, form_document.prepared_file)

            print('Product_Document model object: {}'.format(product_document))

            # Build Product_Document label using the base case template found
            # in templates/pds4/basecase
            print('\n---------------Start Build Product_Document Base Case------------------------')
            product_document.build_base_case()
            print(product_document.label())
            # Open label - returns a list where index 0 is the label object and 1 is the tree
            print(' ... Opening Label ... ')
            label_list = open_label_with_tree(product_document.label())
            label_root = label_list[1]
            # Fill label - fills 
            print(' ... Filling Label ... ')
            #label_root = bundle.version.fill_xml_schema(label_root)
            label_root = product_document.fill_label(label_root)
            # Close label    
            print(' ... Closing Label ... ')
            close_label(label_list[0], label_root, label_list[2])          
            print('---------------- End Build Product_Document Base Case -------')             

            # Add Document info to proper labels.  For now, I simply have Product_Bundle and Product_Collection with a correction for the data collection.  The variable all_labels_kill_data means all Product_Collection labels except those associated with data.  Further below, you will see the correction for the data collection where our label set is now data_labels.
            print('\n---------------Start Build Internal_Reference for Document-------------------')
            all_labels = []
            product_bundle = Product_Bundle.objects.get(bundle=bundle)
            product_collections_list = Product_Collection.objects.filter(bundle=bundle).exclude(collection='Data')

            all_labels.append(product_bundle)
            all_labels.extend(product_collections_list)
            # I think this works -Said
            # Commenting this out for now because it is placing an error and not sure if it's needed -Nicholas
            # all_labels.extend(Table_Delimited.objects.filter(bundle=bundle))
            # all_labels.extend(Table_Binary.objects.filter(bundle=bundle))
            # all_labels.extend(Table_Fixed_Width.objects.filter(bundle=bundle))

            for label in all_labels:
                
                print('- Label: {}'.format(label.label()))
                print(' ... Opening Label ... ')
                label_list = open_label_with_tree(label.label())
                label_root = label_list[1]
        
                # Build Internal_Reference
                print(' ... Building Internal_Reference ... ')
                label_root = label.build_internal_reference(label_root, product_document)

                # Close appropriate label(s)
                print(' ... Closing Label ... ')
                close_label(label.label(), label_root, label_list[2])
            print('\n----------------End Build Internal_Reference for Document-------------------')

            # Rewrite the document collection's inventory whenever membership changes.
            for product_collection in product_collections_list:
                product_collection.build_inventory()

            for citation_information in citation_information_set:
                write_into_label(citation_information, product_document, None)
            for alias in alias_set:
                write_into_label(alias, product_document, None)
            for modification_history in modification_history_set:
                write_into_label(modification_history, product_document, None)



            #form_document = ProductDocumentForm()
            context_dict['form_document'] = form_document
            context_dict['documents'] = Product_Document.objects.filter(bundle=bundle)

            messages.success(request, 'Document "{}" added.'.format(product_document.document_name))
            # # fixes the refresh duplication issue - deric
            # Opened on the document tab, where the new document is.
            return HttpResponseRedirect(walkthrough.bundle_url(bundle, 'doc_collection'))

            # fixes the refresh duplication issue, use this one for offline testing - deric
            # return HttpResponseRedirect('/build/' + pk_bundle + '/')

        with timezone.override(datetime_timezone.utc):
            data_form_is_valid = form_data.is_valid()

        if data_form_is_valid:
            print('\n\n---------------------- DATA INFO -------------------------------')
            print('form_data is valid')

            # Create Data Object
            data = form_data.save(commit=False)
            data.bundle = bundle
            print(form_data.cleaned_data['collection'])
            data.collection = form_data.cleaned_data['collection']
            data.save()

            all_labels = []
            product_bundle = Product_Bundle.objects.get(bundle=bundle)
            product_collections_list = Product_Collection.objects.filter(
                bundle=bundle
            ).exclude(collection='Data')

            all_labels.append(product_bundle)
            all_labels.extend(product_collections_list)

            data.build_base_file(
                start_date_time=form_data.cleaned_data['start_date_time'],
                stop_date_time=form_data.cleaned_data['stop_date_time'],
                start_date_time_nil_reason=form_data.cleaned_data[
                    'start_date_time_nil_reason'
                ],
                stop_date_time_nil_reason=form_data.cleaned_data[
                    'stop_date_time_nil_reason'
                ],
            )

            form_data = DataForm(request.POST or None, pk_bun=pk_bundle)
            context_dict['form_data'] = form_data
            context_dict['data_set'] = Data.objects.filter(bundle=bundle)

            # # fixes the refresh duplication issue - deric
            # return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/')
            # For now redirect to the table edit form, but we want intelligence later to switch to a different page dependent on data type
            return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/' + str(data.pk) + '/table_creation/') 

            # fixes the refresh duplication issue, use this one for offline testing - deric
            # return HttpResponseRedirect('/build/' + pk_bundle + '/')
        
        # satisfy this conditional
        if form_alias.is_valid():
            print('form_alias is valid for {}.'.format(bundle.user))
            # Create Alias model object
            alias = form_alias.save(commit=False)
            alias.bundle = bundle
            alias.save()
            print('Alias model object: {}'.format(alias))

            product_bundle = Product_Bundle.objects.get(bundle=bundle)
            product_collections_list = Product_Collection.objects.filter(bundle=bundle)

            write_into_label(alias, product_bundle, product_collections_list)

            print('---------------- End Build Alias -----------------------------------') 
            # Update alias_set
            alias_set = Alias.objects.filter(bundle=bundle)
            context_dict['alias_set'] = alias_set
            context_dict['alias_set_count'] =  len(alias_set)

            messages.success(request, 'Alias added.')
            # # fixes the refresh duplication issue - deric
            return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/')

            # fixes the refresh duplication issue, use this one for offline testing - deric
            # return HttpResponseRedirect('/build/' + pk_bundle + '/')

        # A window that saved in place and got here was refused, so send back its errors.
        inplace = _inplace_form(request)
        if inplace:
            return _inplace_refused({
                'document': form_document,
                'alias': form_alias,
                'modification_history': form_modification_history,
                'investigation': form_investigation,
                # The setup page's data step creates its collection here.
                'collection': form_additional_collections,
            }.get(inplace))

        context_dict['messages'] = messages.get_messages(request)
        # The Citation Information window is the setup page's list of people too.
        context_dict['citation'] = bundle_setup.citation_rows(bundle)
        context_dict['fixed_editors'] = bundle_setup.fixed_editor_names()
        # Changes ELSA made to the labels by itself, see build/label_upgrades.py.
        context_dict['label_updates'] = list(label_upgrades.history_for(bundle)[:50])
        context_dict['label_updates_unseen'] = list(label_upgrades.unseen_for(bundle))
        context_dict['label_updates_unseen_files'] = label_upgrades.files_phrase(
            label_upgrades.touched(context_dict['label_updates_unseen']))
        context_dict['label_update_failed'] = label_upgrades.failed_update(bundle)
        return render(request, 'build/bundle/bundle.html', context_dict)
    else:
            print('unauthorized user attempting to access a restricted area.')
            return redirect('main:restricted_access')
        


# This view is for select and deleting NetCDF files.  It deletes both the files on disk and the NetCDFFile model objects in the database.  This view is accessed by a bulk delete button on the bundle detail page that appears when a user selects multiple NetCDF files to delete and then clicks the bulk delete button.
@login_required
def bulk_delete_netcdf(request, pk_bundle):
    bundle = Bundle.objects.get(pk=pk_bundle)

    if request.user == bundle.user:
        if request.method == 'POST':
            selected_ids = request.POST.getlist('selected_netcdf')
            print('Bulk deleting NetCDF files: {}'.format(selected_ids))

            for netcdf_id in selected_ids:
                try:
                    netcdf_file = NetCDFFile.objects.get(pk=netcdf_id, bundle=bundle)

                    # Delete the XML label. Uploads may have no extension, so only strip a trailing .nc
                    # and always add .xml. Same naming as _process_single_netcdf.
                    nc_filename = os.path.basename(netcdf_file.file.name)
                    # Get these before deleting the record. NetCDFFile.directory() falls back to the
                    # bundle root for older rows with no collection.
                    file_directory = netcdf_file.directory()
                    try:
                        nc_name = nc_filename[:-3] if nc_filename.endswith('.nc') else nc_filename
                        xml_path = os.path.join(file_directory, nc_name + '.xml')
                        if os.path.exists(xml_path):
                            os.remove(xml_path)
                            print('Deleted XML file: {}'.format(xml_path))
                        else:
                            print('XML file not found at path: {}'.format(xml_path))
                    except Exception as e:
                        print('Could not delete XML for {}: {}'.format(netcdf_id, e))

                    # Delete the NetCDF from disk. Check the collection directory first, then the
                    # bundle root for older rows, then the upload path if processing never finished.
                    candidates = [os.path.join(file_directory, nc_filename),
                                  os.path.join(bundle.directory(), nc_filename)]
                    try:
                        if netcdf_file.file:
                            candidates.append(netcdf_file.file.path)
                    except Exception:
                        pass
                    for nc_path in candidates:
                        if os.path.exists(nc_path):
                            os.remove(nc_path)
                            print('Deleted file: {}'.format(nc_path))
                            break
                    else:
                        print('NetCDF file not found on disk (already removed?): {}'.format(nc_filename))

                    # Delete DB record
                    netcdf_file.delete()
                except NetCDFFile.DoesNotExist:
                    print('NetCDF file {} not found, skipping.'.format(netcdf_id))
                except Exception as e:
                    print('Error deleting NetCDF file {}: {}'.format(netcdf_id, e))

        # Rewrite inventories after the deletes, so they stop naming files that are gone.
        for additional_collection in AdditionalCollections.objects.filter(bundle=bundle):
            additional_collection.build_inventory()

        return HttpResponseRedirect('/elsa/build/' + str(pk_bundle) + '/')

    else:
        return redirect('main:restricted_access')


# --- NetCDF storage check and report ---
# The upload page calls netcdf_storage_check before sending. If there's no room it opens a dialog
# that uses netcdf_storage_draft and netcdf_storage_report. All JSON. Logic is in build/storage_report.py.

def _storage_request(request, pk_bundle):
    """(bundle, payload, error_response) for the storage views, after the usual owner check."""
    from build import storage_report
    bundle = get_object_or_404(Bundle, pk=pk_bundle)
    if request.user != bundle.user:
        return None, None, JsonResponse({'error': 'Not your bundle.'}, status=403)
    if request.method != 'POST':
        return None, None, JsonResponse({'error': 'POST only.'}, status=405)
    try:
        payload = json.loads(request.body or b'{}')
    except ValueError:
        return None, None, JsonResponse({'error': 'Malformed request.'}, status=400)
    if not isinstance(payload, dict):
        return None, None, JsonResponse({'error': 'Malformed request.'}, status=400)
    payload['files'] = storage_report.clean_files(payload.get('files'))
    payload['collection'] = str(payload.get('collection', ''))[:200]
    return bundle, payload, None


def _netcdf_upload_reply(bundle, collection, netcdf_files):
    """What the page needs after an upload: which collection and files to show once it reloads.

    The AMA reading goes in too, since the Files tree might not have a row for the file yet.
    ama_url is the standalone editor.
    """
    ama_filled = _ama_completeness_by_file(bundle)
    return {
        'collection_id': collection.id,
        'collection_name': collection.collection_name,
        'tab_target': '#additional_collection_{}'.format(collection.id),
        'files': [{
            'id': obj.id,
            'title': obj.title,
            'ama_filled': ama_filled.get(obj.id, 0),
            'ama_total': AMA_TOTAL_FIELDS,
            'ama_url': reverse('build:netcdf_ama', kwargs={
                'pk_bundle': bundle.id, 'pk_netcdf': obj.id}),
        } for obj in netcdf_files],
    }


@login_required
def netcdf_storage_check(request, pk_bundle):
    from build import storage_report
    bundle, payload, error = _storage_request(request, pk_bundle)
    if error:
        return error
    total = sum(f['size'] for f in payload['files'])
    space = storage_report.check_space(total)
    if not space['ok']:
        print('Upload preflight refused for {}: needs {} bytes, {} usable'.format(
            request.user.username, total, space['available']))
    return JsonResponse({
        'ok': space['ok'],
        'needed': storage_report.human_size(space['needed']),
        'available': storage_report.human_size(space['available']),
        'available_bytes': space['available'],
    })


@login_required
def netcdf_storage_draft(request, pk_bundle):
    from build import storage_report
    bundle, payload, error = _storage_request(request, pk_bundle)
    if error:
        return error
    message, source = storage_report.draft_message(
        request.user, bundle, payload['collection'], payload['files'])
    return JsonResponse({'message': message, 'source': source})


@login_required
def netcdf_storage_report(request, pk_bundle):
    from build import storage_report
    bundle, payload, error = _storage_request(request, pk_bundle)
    if error:
        return error
    message = str(payload.get('message', '')).strip()
    if not message:
        return JsonResponse({'error': 'Please write a message before sending.'}, status=400)
    if len(message) > 5000:
        return JsonResponse({'error': 'That message is too long. Please keep it under 5000 characters.'},
                            status=400)
    if storage_report.rate_limited('send', request.user, storage_report.SEND_LIMIT_PER_HOUR):
        return JsonResponse({'error': 'You have already sent a few reports this hour. Team ELSA has '
                                      'them and will be in touch.'}, status=429)
    try:
        storage_report.send_report(request.user, bundle, payload['collection'], payload['files'], message)
    except Exception as e:
        print('Storage report email failed: {}'.format(e))
        return JsonResponse({'error': 'The report could not be sent just now. Please email '
                                      'atm-elsa@nmsu.edu directly.'}, status=502)
    return JsonResponse({'sent': True, 'email': request.user.email})


# --- Resumable NetCDF uploads (tus) ---
# The protocol lives in build/netcdf_uploads.py. These views only find the bundle and upload,
# apply the owner check, and turn refusals into tus responses.

def _tus_bundle(request, pk_bundle):
    """The bundle, after the usual owner check. JSON refusals, not redirects: the caller is a script."""
    if not request.user.is_authenticated:
        raise netcdf_uploads.UploadError(401, 'Your session has ended. Please sign in again and '
                                              'pick the same files to carry on where you left off.')
    bundle = Bundle.objects.filter(pk=pk_bundle).first()
    if bundle is None:
        raise netcdf_uploads.UploadError(404, 'That bundle no longer exists.')
    if request.user != bundle.user:
        raise netcdf_uploads.UploadError(403, 'Not your bundle.')
    return bundle


def _tus_upload(request, bundle, upload_id):
    # Someone else's upload looks exactly like a missing one.
    upload = (NetCDFUpload.objects.select_related('collection', 'bundle', 'netcdf_file')
              .filter(pk=upload_id, bundle=bundle, user=request.user).first())
    if upload is None:
        raise netcdf_uploads.UploadError(404, 'This upload is no longer on the server. Please start it again.')
    return upload


def netcdf_upload_create(request, pk_bundle):
    try:
        bundle = _tus_bundle(request, pk_bundle)
        if request.method == 'OPTIONS':
            return netcdf_uploads.options_response()
        if request.method != 'POST':
            raise netcdf_uploads.UploadError(405, 'Method not allowed.', headers={'Allow': 'OPTIONS, POST'})
        netcdf_uploads.check_tus_version(request)
        return netcdf_uploads.create(request, bundle, lambda upload: reverse(
            'build:netcdf_upload', kwargs={'pk_bundle': bundle.pk, 'upload_id': upload.pk}))
    except netcdf_uploads.UploadError as exc:
        return netcdf_uploads.error_response(exc)


def netcdf_upload(request, pk_bundle, upload_id):
    try:
        bundle = _tus_bundle(request, pk_bundle)
        if request.method == 'OPTIONS':
            return netcdf_uploads.options_response()
        netcdf_uploads.check_tus_version(request)
        upload = _tus_upload(request, bundle, upload_id)
        if request.method == 'HEAD':
            return netcdf_uploads.head(upload)
        if request.method == 'PATCH':
            return netcdf_uploads.patch(request, upload)
        if request.method == 'DELETE':
            return netcdf_uploads.terminate(upload)
        raise netcdf_uploads.UploadError(
            405, 'Method not allowed.', headers={'Allow': 'OPTIONS, HEAD, PATCH, DELETE'})
    except netcdf_uploads.UploadError as exc:
        return netcdf_uploads.error_response(exc)


@require_POST
def netcdf_upload_check(request, pk_bundle):
    """Right after files are picked: which ones the upload would refuse, and why."""
    try:
        bundle = _tus_bundle(request, pk_bundle)
        if len(request.body) > 256 * 1024:
            raise netcdf_uploads.UploadError(413, 'Too many files picked at once.')
        try:
            payload = json.loads(request.body or b'{}')
        except ValueError:
            raise netcdf_uploads.UploadError(400, 'Malformed request.')
        if not isinstance(payload, dict):
            raise netcdf_uploads.UploadError(400, 'Malformed request.')
        results = netcdf_uploads.check_files(
            request.user, bundle, payload.get('collection'), payload.get('files'))
    except netcdf_uploads.UploadError as exc:
        return JsonResponse({'error': exc.message}, status=exc.status)
    return JsonResponse({'files': results, 'max_bytes': netcdf_uploads.max_file_bytes()})


@require_POST
def netcdf_upload_finish(request, pk_bundle, upload_id):
    """After the last byte: add the file to the bundle and build its label, like a form upload."""
    try:
        bundle = _tus_bundle(request, pk_bundle)
        upload = _tus_upload(request, bundle, upload_id)
        collection = upload.collection

        def process(netcdf_file):
            directory = collection.directory()
            os.makedirs(directory, exist_ok=True)
            return variable_coord_to_product(bundle, [netcdf_file], directory)

        netcdf_file, errors = netcdf_uploads.finish(upload, process)
    except netcdf_uploads.UploadError as exc:
        return netcdf_uploads.error_response(exc)
    if errors:
        return JsonResponse({'error': 'Uploaded, but metadata could not be extracted from: '
                                      + '; '.join(errors)}, status=400)
    return JsonResponse(_netcdf_upload_reply(bundle, collection, [netcdf_file]))


# --- AMA discipline metadata views ---
# The NetCDF harvest fills ama:Variable and ama:Coordinate. These views collect the rest.
# 
# All three classes have a per-collection default that files inherit, plus an optional per-file override.
# Every save regenerates the labels. The forms use prefixes because they share field names
# like start_time and render in one panel.
AMA_MODEL_METADATA_PREFIX = 'model'
AMA_SIMULATION_PREFIX = 'sim'
AMA_FILE_DESCRIPTION_PREFIX = 'desc'

# (model class, form class, prefix, human label) for the three sections, in panel order.
AMA_SECTIONS = (
    (ModelMetadata, ModelMetadataForm, AMA_MODEL_METADATA_PREFIX, 'Model Metadata'),
    (SimulationConfiguration, SimulationConfigurationForm, AMA_SIMULATION_PREFIX,
     'Simulation Configuration'),
    (FileDescription, FileDescriptionForm, AMA_FILE_DESCRIPTION_PREFIX, 'File Description'),
)


def _ama_status(collection):
    """At-a-glance completeness of the three AMA classes for one collection's summary card."""
    status = {'collection': collection}

    for model_class, _form_class, prefix, _label in AMA_SECTIONS:
        record = model_class.default_for_collection(collection)
        status[prefix] = {
            'filled': len(record.filled_values()) if record else 0,
            'total': len(model_class.ELEMENT_ORDER),
            'is_set': bool(record and record.filled_values()),
        }

    model_metadata = ModelMetadata.default_for_collection(collection)
    status['model_name'] = model_metadata.name if model_metadata else ''

    overridden = set()
    for model_class, _form_class, _prefix, _label in AMA_SECTIONS:
        overridden.update(
            model_class.objects.filter(collection=collection, netcdf_file__isnull=False)
            .values_list('netcdf_file_id', flat=True))
    status['override_count'] = len(overridden)

    return status


def _netcdf_files_for_collection(collection):
    """Files in one collection, each marked if it overrides the defaults."""
    netcdf_files = list(NetCDFFile.objects.filter(collection=collection))

    overridden_ids = set()
    for model_class, _form_class, _prefix, _label in AMA_SECTIONS:
        overridden_ids.update(
            model_class.objects.filter(collection=collection, netcdf_file__isnull=False)
            .values_list('netcdf_file_id', flat=True))

    for netcdf_file in netcdf_files:
        netcdf_file.has_ama_override = netcdf_file.pk in overridden_ids

    return netcdf_files


def _collections_with_ama(bundle):
    """The bundle's collections with their file lists and AMA status.

    Named ama_* to avoid clashing with the NetCDFFile.collection reverse accessor.
    """
    collections = list(AdditionalCollections.objects.filter(bundle=bundle))
    for collection in collections:
        collection.ama_files = _netcdf_files_for_collection(collection)
        collection.ama_status = _ama_status(collection)

    return collections


def _unassigned_netcdf_files(bundle):
    """Older files the backfill couldn't place in a collection, listed in their own section."""
    return list(NetCDFFile.objects.filter(bundle=bundle, collection__isnull=True))


# Every AMA attribute across the three sections, from ELEMENT_ORDER so it stays in sync.
AMA_TOTAL_FIELDS = sum(len(model_class.ELEMENT_ORDER) for model_class, _f, _p, _l in AMA_SECTIONS)


def _ama_completeness_by_file(bundle):
    """How many AMA attributes each file's label will have, keyed by NetCDFFile id.

    Same logic as `resolve_for_file()`, but with bulk queries so it's not three queries per file.
    Returns {} for Archive bundles.
    """
    if bundle.bundle_type != 'External':
        return {}

    netcdf_files = list(NetCDFFile.objects.filter(bundle=bundle))
    if not netcdf_files:
        return {}

    filled = {netcdf_file.id: 0 for netcdf_file in netcdf_files}

    for model_class, _form_class, _prefix, _label in AMA_SECTIONS:
        defaults_by_collection = {}
        overrides_by_file = {}
        for record in model_class.objects.filter(bundle=bundle):
            if record.netcdf_file_id is None:
                defaults_by_collection[record.collection_id] = record
            else:
                overrides_by_file[record.netcdf_file_id] = record

        for netcdf_file in netcdf_files:
            record = overrides_by_file.get(netcdf_file.id)
            if record is None:
                record = defaults_by_collection.get(netcdf_file.collection_id)
            if record is not None:
                filled[netcdf_file.id] += len(record.filled_values())

    return filled


# --- Files tree (XML labels) ---
# The tree follows the bundle directory, which has the PDS4 hierarchy:
# 
# <bundle>_bundle/            bundle_<name>.xml            Product_Bundle
# +-- document/               collection_<id>_document.xml Product_Collection
# |                           <doc>.xml                    Product_Document
# +-- <collection>/           collection_<id>_<coll>.xml   Product_Collection
# <file>.xml                   Product_Observational
# <file>.nc                    the data file that label describes
# 
# Label content isn't read here. label_content() gets one label at a time.

# Caption under each row. The kind comes from the filename prefix.
LABEL_KIND_CAPTIONS = {
    'bundle': 'Bundle Label',
    'collection': 'Collection Label',
    'document': 'Document Label',
    'data': 'Data Product Label',
}

# Fallback types when a directory has no Product_Collection row. Uses the PDS4 spelling.
IMPLIED_COLLECTION_TYPES = {
    'document': 'Document',
    'context': 'Context',
    'xml_schema': 'XML Schema',
}

# Order of directories in the tree: ELSA's collections first, then the user's.
ELSA_MANAGED_DIRS = ('document', 'context', 'xml_schema')

# Filter chip kinds, in order, with the plural text for the chip.
FILTERABLE_KINDS = (
    ('collection', 'Collections'),
    ('data', 'Data'),
    ('document', 'Documents'),
)


def _label_kind(filename, folder_name):
    """Which of the four PDS4 label kinds a generated label is."""
    name = filename.lower()
    if name.startswith('bundle_') or name.startswith('bundle.'):
        return 'bundle'
    if name.startswith('collection_') or name.startswith('collection.'):
        return 'collection'
    if folder_name.lower() == 'document':
        return 'document'
    return 'data'


_PDS = '{http://pds.nasa.gov/pds4/pds/v1}'


def _collection_inventory(label_path):
    """The inventory table a collection label names, read for the Files tree and its viewer.

    Found through the label's own <File_Area_Inventory>/<File>/<file_name> rather than by
    guessing the file name, so a bundle written before the _inventory.csv naming still shows the
    table its label actually points at. None when the label cannot be read or names no file that
    is there.
    """
    try:
        root = etree.parse(label_path).getroot()
    except (OSError, etree.XMLSyntaxError):
        return None
    area = root.find('{0}File_Area_Inventory'.format(_PDS))
    if area is None:
        return None
    file_name = (area.findtext('{0}File/{0}file_name'.format(_PDS)) or '').strip()
    # A bare name in the label's own directory is the only thing ELSA writes there; anything with
    # a separator in it is not ours and is not followed.
    if not file_name or os.path.basename(file_name) != file_name:
        return None
    path = os.path.join(os.path.dirname(label_path), file_name)
    if not os.path.isfile(path):
        return None

    try:
        with open(path, 'rb') as handle:
            raw = handle.read()
    except OSError:
        return None
    count = sum(1 for line in raw.decode('utf-8', errors='replace').splitlines() if line.strip())
    return {'name': file_name, 'abs_path': path, 'raw': raw, 'count': count}


def _bundle_file_tree(bundle):
    """The bundle's labels, grouped by directory.

    Returns a dict with 'root_labels', 'folders' and 'total'. A missing directory gives an empty tree.
    """
    root_dir = bundle.directory()
    if not os.path.isdir(root_dir):
        print('_bundle_file_tree: directory missing, skipping: {}'.format(root_dir))
        return {'root_labels': [], 'folders': [], 'filter_kinds': [], 'total': 0}

    # AdditionalCollections.directory() lowercases collection_name to form the directory name, so
    # the directory basename is the key back to the collection record and its declared type.
    # Ordered by pk, i.e. by creation: the model has no Meta.ordering, so this is the same order the
    # Collections tabs on this page iterate, and the two cards agree on where a new collection goes.
    collections_in_order = list(
        AdditionalCollections.objects.filter(bundle=bundle).order_by('pk'))
    collections_by_dir = {
        collection.collection_name.lower(): collection
        for collection in collections_in_order}

    # ELSA's own collections are Product_Collection rows and the stored key isn't the declared type,
    # so read the type off the record to match the label.
    product_collections_by_dir = {
        collection.collection.lower(): collection
        for collection in Product_Collection.objects.filter(bundle=bundle)}

    # Match a data label to its file by stem: 00000.atmos.nc <-> 00000.atmos.xml.
    # Use file.name since title keeps the uploaded name and storage sanitises the real one.
    # 
    # Also keyed on the collection directory since a stem is only unique in one collection.
    # The stem-only map is the fallback when nothing matches.
    collection_dir_by_id = {
        collection.id: os.path.basename(collection.directory())
        for collection in collections_in_order}

    netcdf_by_dir_stem = {}
    netcdf_by_stem = {}
    for netcdf_file in NetCDFFile.objects.filter(bundle=bundle):
        directory = collection_dir_by_id.get(netcdf_file.collection_id, '')
        for candidate in (netcdf_file.file.name, netcdf_file.title):
            if candidate:
                stem = os.path.splitext(os.path.basename(candidate))[0]
                netcdf_by_dir_stem.setdefault((directory, stem), netcdf_file)
                netcdf_by_stem.setdefault(stem, netcdf_file)

    # Empty for Archive, which keeps the AMA reading off the Archive Files card.
    ama_filled_by_file = _ama_completeness_by_file(bundle)

    def describe(dir_path, folder_name, filename, siblings):
        kind = _label_kind(filename, folder_name)
        entry = {
            'name': filename,
            # Relative to the bundle directory. label_content() uses this, and two collections can
            # have a label with the same name.
            'path': '{}/{}'.format(folder_name, filename) if folder_name else filename,
            'kind': kind,
            'kind_label': LABEL_KIND_CAPTIONS[kind],
            'describes': None,
            'processed': None,
            'processing_error': '',
            # Only set for External data labels: id for the AMA editor and how much AMA the label has.
            'netcdf_id': None,
            'ama_filled': None,
            'ama_total': None,
            # Collection labels only: the inventory table the label names. A PDS4 collection
            # product is the label and this table together, so the viewer offers it as a second
            # tab on the label rather than as a row of its own.
            'inventory': None,
        }

        if kind == 'collection':
            inventory = _collection_inventory(os.path.join(dir_path, filename))
            if inventory is not None:
                entry['inventory'] = {
                    'name': inventory['name'],
                    'path': ('{}/{}'.format(folder_name, inventory['name'])
                             if folder_name else inventory['name']),
                    'count': inventory['count'],
                }

        if kind != 'data':
            return entry

        # Show the data file under its label.
        stem = os.path.splitext(filename)[0]
        for sibling in siblings:
            if sibling != filename and os.path.splitext(sibling)[0] == stem:
                entry['describes'] = sibling
                break

        netcdf_file = netcdf_by_dir_stem.get((folder_name, stem)) or netcdf_by_stem.get(stem)
        if netcdf_file is not None:
            entry['processed'] = netcdf_file.processed
            entry['processing_error'] = netcdf_file.processing_error
            if entry['describes'] is None:
                # The label is on disk but its data file is not; still say what it stands for.
                entry['describes'] = netcdf_file.title

            # No collection means no defaults and no AMA editor, so don't show a reading.
            if ama_filled_by_file and netcdf_file.collection_id is not None:
                entry['netcdf_id'] = netcdf_file.id
                entry['ama_filled'] = ama_filled_by_file.get(netcdf_file.id, 0)
                entry['ama_total'] = AMA_TOTAL_FIELDS

        return entry

    def labels_in(dir_path, folder_name):
        try:
            siblings = sorted(os.listdir(dir_path))
        except OSError as e:
            print('_bundle_file_tree: unreadable directory {}: {}'.format(dir_path, e))
            return []

        labels = [
            describe(dir_path, folder_name, name, siblings)
            for name in siblings
            if name.lower().endswith('.xml') and os.path.isfile(os.path.join(dir_path, name))]

        # The collection label goes first in its group since it's the parent.
        labels.sort(key=lambda label: (0 if label['kind'] == 'collection' else 1, label['name']))
        return labels

    root_labels = labels_in(root_dir, '')

    on_disk = {name for name in os.listdir(root_dir)
               if os.path.isdir(os.path.join(root_dir, name))}

    ordered_names = [name for name in ELSA_MANAGED_DIRS if name in on_disk]
    for collection in collections_in_order:
        name = os.path.basename(collection.directory())
        if name in on_disk and name not in ordered_names:
            ordered_names.append(name)
    # Files with no collection record still go in the tree, after the known directories.
    ordered_names.extend(sorted(on_disk.difference(ordered_names)))

    folders = []
    for name in ordered_names:
        collection = collections_by_dir.get(name.lower())
        product_collection = product_collections_by_dir.get(name.lower())
        if collection is not None:
            collection_type = collection.collection_type
        elif product_collection is not None:
            collection_type = product_collection.collection_type()
        else:
            collection_type = IMPLIED_COLLECTION_TYPES.get(name.lower(), '')
        labels = labels_in(os.path.join(root_dir, name), name)
        folders.append({
            'name': name,
            'collection_type': collection_type,
            'labels': labels,
            'count': len(labels),
        })

    # Only offer chips that match something. Skip the bundle label, and hide the row if fewer than two.
    kinds_present = {label['kind'] for label in root_labels}
    for folder in folders:
        kinds_present.update(label['kind'] for label in folder['labels'])

    filter_kinds = [{'kind': kind, 'label': plural}
                    for kind, plural in FILTERABLE_KINDS
                    if kind in kinds_present]

    return {
        'root_labels': root_labels,
        'folders': folders,
        'filter_kinds': filter_kinds if len(filter_kinds) > 1 else [],
        'total': len(root_labels) + sum(folder['count'] for folder in folders),
    }


def _resolve_label_path(bundle, relative_path):
    """Absolute path of a label in this bundle, or None.

    relative_path comes from the browser, so check it stays inside the bundle directory.
    """
    if not relative_path or not relative_path.lower().endswith('.xml'):
        return None

    root_dir = _eventual_path(bundle.directory())
    candidate = _eventual_path(os.path.join(root_dir, relative_path))

    if os.path.commonpath([root_dir, candidate]) != root_dir:
        return None
    if not os.path.isfile(candidate):
        return None

    return candidate


@login_required
def label_content(request, pk_bundle):
    """One label's XML, fetched on demand by the Files tree."""
    bundle = get_object_or_404(Bundle, pk=pk_bundle)

    if request.user != bundle.user:
        return redirect('main:restricted_access')

    relative_path = request.GET.get('path', '')
    label_path = _resolve_label_path(bundle, relative_path)
    if label_path is None:
        if request.GET.get('download'):
            raise Http404('That label is not part of this bundle.')
        return JsonResponse({'error': 'That label is not part of this bundle.'}, status=404)

    # The viewer's Download button: the file exactly as it sits on disk, not the pretty-printed
    # copy shown on screen, which drops the XML declaration.
    if request.GET.get('download'):
        response = FileResponse(open(label_path, 'rb'), as_attachment=True,
                                filename=os.path.basename(label_path),
                                content_type='application/xml')
        response['X-Content-Type-Options'] = 'nosniff'
        response['Cache-Control'] = 'private, no-cache'
        return response

    # A corrupt label should show up in the viewer, not break the page.
    try:
        tree = etree.parse(label_path)
        content = etree.tostring(tree, pretty_print=True, encoding='unicode')
    except (OSError, etree.XMLSyntaxError) as e:
        print('label_content: unreadable label {}: {}'.format(label_path, e))
        return JsonResponse(
            {'error': 'This label could not be read ({}). Please contact the ELSA team via the '
                      'Contact page.'.format(type(e).__name__)},
            status=500)

    return JsonResponse({
        'name': os.path.basename(label_path),
        'path': relative_path,
        'content': content,
    })


def _resolve_inventory(bundle, relative_path):
    """A collection inventory in this bundle: (collection label path, inventory dict), or None.

    Only a .csv that a collection label in the same directory names is served, so this cannot be
    used to read arbitrary files out of the bundle, let alone outside it.
    """
    if not relative_path or not relative_path.lower().endswith('.csv'):
        return None
    root_dir = _eventual_path(bundle.directory())
    candidate = _eventual_path(os.path.join(root_dir, relative_path))
    if os.path.commonpath([root_dir, candidate]) != root_dir or not os.path.isfile(candidate):
        return None

    directory = os.path.dirname(candidate)
    for name in sorted(os.listdir(directory)):
        if _label_kind(name, os.path.basename(directory)) != 'collection':
            continue
        if not name.lower().endswith('.xml'):
            continue
        inventory = _collection_inventory(os.path.join(directory, name))
        if inventory is not None and _eventual_path(inventory['abs_path']) == candidate:
            return os.path.join(directory, name), inventory
    return None


@login_required
def inventory_content(request, pk_bundle):
    """One collection inventory, for the viewer's Inventory tab, or the file itself with ?download=1.

    Read-only on purpose. ELSA rewrites the table whenever a product joins or leaves the
    collection, so anything typed into it would be lost at the next change.
    """
    bundle = get_object_or_404(Bundle, pk=pk_bundle)
    if request.user != bundle.user:
        return redirect('main:restricted_access')

    relative_path = request.GET.get('path', '')
    found = _resolve_inventory(bundle, relative_path)
    if found is None:
        if request.GET.get('download'):
            raise Http404('That inventory is not part of this bundle.')
        return JsonResponse({'error': 'That inventory is not part of this bundle.'}, status=404)
    _label_path, inventory = found

    if request.GET.get('download'):
        response = FileResponse(open(inventory['abs_path'], 'rb'), as_attachment=True,
                                filename=inventory['name'], content_type='text/csv')
        response['X-Content-Type-Options'] = 'nosniff'
        response['Cache-Control'] = 'private, no-cache'
        return response

    return JsonResponse({
        'name': inventory['name'],
        'path': relative_path,
        'count': inventory['count'],
        'content': inventory['raw'].decode('utf-8', errors='replace'),
        'download_url': '{}?path={}&download=1'.format(
            reverse('build:inventory_content', args=[bundle.pk]), quote(relative_path)),
    })


@login_required
def netcdf_file(request, pk_bundle, pk_netcdf):
    """Download one uploaded NetCDF file, owner only."""
    bundle = get_object_or_404(Bundle, pk=pk_bundle)
    if request.user != bundle.user:
        return redirect('main:restricted_access')
    nc_file = get_object_or_404(NetCDFFile, pk=pk_netcdf, bundle=bundle)

    path = nc_file.stored_path()
    if path is None:
        raise Http404('This NetCDF file is no longer on disk.')

    # FileResponse streams in blocks, so a multi-gigabyte file never sits in memory.
    response = FileResponse(open(path, 'rb'), as_attachment=True,
                            filename=os.path.basename(path), content_type='application/x-netcdf')
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'private, no-cache'
    return response


@login_required
def netcdf_inspect(request, pk_bundle, pk_netcdf):
    """What a NetCDF file holds and its label, for the inspector window.

    Each half can fail on its own, so a bad file still shows its label and the other way round.
    """
    bundle = get_object_or_404(Bundle, pk=pk_bundle)
    if request.user != bundle.user:
        return redirect('main:restricted_access')
    nc_file = get_object_or_404(NetCDFFile, pk=pk_netcdf, bundle=bundle)

    path = nc_file.stored_path()
    if path is None:
        return JsonResponse(
            {'error': 'This file is no longer on disk. Delete it here and upload it again.'},
            status=404)

    data = {
        'name': os.path.basename(path),
        'size': filesizeformat(os.path.getsize(path)),
    }

    try:
        data['contents'] = netcdf_header.contents_html(path)
    except Exception as e:
        print('netcdf_inspect: could not read {}: {}'.format(path, e))
        data['contents_error'] = ('ELSA could not read this file\'s header ({}).'
                                  .format(type(e).__name__))

    label_path = nc_file.label()
    if os.path.isfile(label_path):
        try:
            tree = etree.parse(label_path)
            data['label'] = etree.tostring(tree, pretty_print=True, encoding='unicode')
            data['label_name'] = os.path.basename(label_path)
        except (OSError, etree.XMLSyntaxError) as e:
            print('netcdf_inspect: unreadable label {}: {}'.format(label_path, e))
            data['label_error'] = ('This file\'s label could not be read ({}). Please contact '
                                   'the ELSA team via the Contact page.'.format(type(e).__name__))
    elif nc_file.processing_error:
        data['label_error'] = ('No label was written because processing failed: {}'
                               .format(nc_file.processing_error))
    else:
        data['label_error'] = 'No label has been written for this file yet.'

    return JsonResponse(data)


def _report_label_refresh(request, errors):
    """Surface label-regeneration problems without pretending the save failed."""
    _report_ama_save(request, errors)


def _report_ama_save(request, errors, summary=''):
    """One message per save. A failure to rewrite the labels is still reported."""
    if errors:
        messages.warning(
            request,
            'Saved, but some labels could not be refreshed: {}'.format('; '.join(errors)))
        return
    messages.success(request, 'Saved. {}'.format(summary).strip() if summary else 'Saved.')


def _join_readably(items):
    """'a', 'a and b', 'a, b and c' - for putting section names in a sentence."""
    items = list(items)
    if len(items) <= 1:
        return ''.join(items)
    return '{} and {}'.format(', '.join(items[:-1]), items[-1])


def _describe_ama_save(collection, shared_sections, others_affected):
    """Say what a save changed for other files, if anything."""
    if not shared_sections:
        return ''

    sections = _join_readably(shared_sections)
    collection_name = getattr(collection, 'collection_name', 'this collection')

    if others_affected == 1:
        tail = ', replacing the values 1 other file had of its own'
    elif others_affected:
        tail = ', replacing the values {} other files had of their own'.format(others_affected)
    else:
        tail = ''
    verb = 'applies' if len(shared_sections) == 1 else 'apply'
    return '{} now {} to every file in {}{}.'.format(
        sections, verb, collection_name, tail)


def _is_panel_request(request):
    """True when the master-detail UI is asking for just the panel, not a whole page."""
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


def _build_ama_forms(data, collection, netcdf_file, scope):
    """Bind or prefill the three AMA forms for one scope.

    On GET for a file with no values of its own, prefill from the collection default.
    """
    forms_by_prefix = {}
    for model_class, form_class, prefix, _label in AMA_SECTIONS:
        has_override = False
        if scope == 'file':
            instance = model_class.override_for_file(netcdf_file)
            # Read this before the fallback replaces `instance`. It decides the starting scope radio.
            has_override = instance is not None
            if data is None and instance is None:
                instance = model_class.default_for_collection(collection)
        else:
            instance = model_class.default_for_collection(collection)

        forms_by_prefix[prefix] = form_class(
            data, instance=instance, scope=scope, prefix=prefix, has_override=has_override)
    return forms_by_prefix


def _save_ama_section(request, collection, netcdf_file, model_class, form, label):
    """Save one section and handle its apply-to-collection checkbox.

    Returns (applied_to_collection, other_files_affected).
    """
    scope_is_file = netcdf_file is not None

    if scope_is_file and not form.has_any_value():
        # Clearing every field means go back to inheriting. Apply-to-collection on an empty section
        # does nothing, so it can't blank the collection default.
        model_class.objects.filter(netcdf_file=netcdf_file).delete()
        return False, 0

    candidate = form.save(commit=False)

    if scope_is_file and not form.wants_apply_to_collection() and not form.wants_file_scope():
        # The file's panel is prefilled from the default, so only save an override if something
        # changed, otherwise later collection edits stop reaching it.
        # 
        # `wants_file_scope()` is the exception. If the user picks "just this file" we store it anyway.
        default = model_class.default_for_collection(collection)
        default_values = default.filled_values() if default else {}
        if candidate.filled_values() == default_values:
            model_class.objects.filter(netcdf_file=netcdf_file).delete()
            return False, 0

    record = candidate
    record.bundle = collection.bundle
    record.collection = collection
    record.netcdf_file = netcdf_file
    record.save()

    if not form.wants_apply_to_collection():
        return False, 0

    # Count before the push and leave out the file being edited.
    others_cleared = (model_class.objects
                      .filter(collection=collection, netcdf_file__isnull=False)
                      .exclude(netcdf_file=netcdf_file)
                      .count())

    model_class.apply_to_collection(record, collection)
    if scope_is_file:
        # This file's row became the collection default, so it doesn't need its own copy.
        model_class.objects.filter(netcdf_file=netcdf_file).delete()
    return True, others_cleared


def _ama_panel_context(collection, netcdf_file, forms_by_prefix, saved=False):
    """Context shared by the collection-defaults panel and the per-file panel."""
    sections = []
    for model_class, _form_class, prefix, label in AMA_SECTIONS:
        override = model_class.override_for_file(netcdf_file) if netcdf_file else None
        sections.append({
            'prefix': prefix,
            'label': label,
            'form': forms_by_prefix[prefix],
            'has_override': override is not None,
            # Field count for the tab.
            'field_count': len(model_class.ELEMENT_ORDER),
        })

    # For the copy-from-another-file control. Empty hides the control.
    if netcdf_file is None:
        siblings = []
    else:
        siblings = list(
            NetCDFFile.objects.filter(collection=collection).exclude(pk=netcdf_file.pk))

    return {
        'bundle': collection.bundle,
        'collection': collection,
        'netcdf_file': netcdf_file,
        'is_default_scope': netcdf_file is None,
        'sections': sections,
        'sibling_files': siblings,
        'has_any_override': any(section['has_override'] for section in sections),
        'panel_saved': saved,
    }


def _render_ama_panel(request, context_dict):
    """Panel only for the inline editor, full page for a direct hit. Both use the same partial."""
    if _is_panel_request(request):
        return render(request, 'build/bundle/_ama_panel.html', context_dict)
    return render(request, 'build/bundle/netcdf_ama.html', context_dict)


def _handle_ama_panel_post(request, collection, netcdf_file):
    """Validate all three sections, then save them. Returns (saved, forms_by_prefix)."""
    scope = 'file' if netcdf_file is not None else 'collection'
    forms_by_prefix = _build_ama_forms(request.POST, collection, netcdf_file, scope)

    # Validate every section before writing any of them.
    if not all(form.is_valid() for form in forms_by_prefix.values()):
        return False, forms_by_prefix

    applied_to_collection = False
    shared_sections = []
    others_affected = 0
    for model_class, _form_class, prefix, label in AMA_SECTIONS:
        applied, others = _save_ama_section(
            request, collection, netcdf_file, model_class, forms_by_prefix[prefix], label)
        if applied:
            applied_to_collection = True
            shared_sections.append(label)
            others_affected = max(others_affected, others)

    # Only this file's label changes, unless values were pushed to the whole collection.
    if netcdf_file is not None and not applied_to_collection:
        targets = [netcdf_file]
    else:
        targets = list(NetCDFFile.objects.filter(collection=collection))

    _report_ama_save(
        request,
        regenerate_netcdf_labels(collection.bundle, targets),
        _describe_ama_save(collection, shared_sections, others_affected))
    return True, forms_by_prefix


@login_required
def ama_collection_defaults(request, pk_bundle, pk_collection):
    """The AMA defaults for one collection: applied to every file that has no values of its own."""
    bundle = get_object_or_404(Bundle, pk=pk_bundle)

    if request.user != bundle.user:
        return redirect('main:restricted_access')

    collection = get_object_or_404(AdditionalCollections, pk=pk_collection, bundle=bundle)
    saved = False

    if request.method == 'POST':
        saved, forms_by_prefix = _handle_ama_panel_post(request, collection, None)
        if saved:
            if not _is_panel_request(request):
                return HttpResponseRedirect('/elsa/build/' + str(pk_bundle) + '/')
            # Rebind from what is stored so the panel reflects the save, including any clearing.
            forms_by_prefix = _build_ama_forms(None, collection, None, 'collection')
        elif not _is_panel_request(request):
            messages.error(request, 'AMA defaults could not be saved. Please check the values.')
            return HttpResponseRedirect('/elsa/build/' + str(pk_bundle) + '/')
    else:
        forms_by_prefix = _build_ama_forms(None, collection, None, 'collection')

    return _render_ama_panel(
        request, _ama_panel_context(collection, None, forms_by_prefix, saved=saved))


@login_required
def netcdf_ama(request, pk_bundle, pk_netcdf):
    """The AMA values for a single NetCDF file, overriding its collection's defaults."""
    bundle = get_object_or_404(Bundle, pk=pk_bundle)

    if request.user != bundle.user:
        return redirect('main:restricted_access')

    netcdf_file = get_object_or_404(NetCDFFile, pk=pk_netcdf, bundle=bundle)
    collection = netcdf_file.collection

    if collection is None:
        # Older file with no collection, so there's nowhere to save AMA values. Say so.
        messages.error(
            request,
            '"{}" is not attached to a collection, so its AMA metadata cannot be edited. '
            'Re-upload it into a collection.'.format(netcdf_file.title))
        return HttpResponseRedirect('/elsa/build/' + str(pk_bundle) + '/')

    saved = False

    if request.method == 'POST':
        saved, forms_by_prefix = _handle_ama_panel_post(request, collection, netcdf_file)
        if saved:
            if not _is_panel_request(request):
                return HttpResponseRedirect(
                    reverse('build:netcdf_ama',
                            kwargs={'pk_bundle': bundle.pk, 'pk_netcdf': netcdf_file.pk}))
            forms_by_prefix = _build_ama_forms(None, collection, netcdf_file, 'file')
    else:
        forms_by_prefix = _build_ama_forms(None, collection, netcdf_file, 'file')

    return _render_ama_panel(
        request, _ama_panel_context(collection, netcdf_file, forms_by_prefix, saved=saved))


@login_required
def assign_netcdf_collection(request, pk_bundle, pk_netcdf):
    """Put a NetCDF file with no collection into a collection.

    Moves the file and label into the collection directory and rebuilds the label.
    """
    bundle = get_object_or_404(Bundle, pk=pk_bundle)

    if request.user != bundle.user:
        return redirect('main:restricted_access')

    netcdf_file = get_object_or_404(NetCDFFile, pk=pk_netcdf, bundle=bundle)

    if request.method == 'POST':
        collection = AdditionalCollections.objects.filter(
            pk=request.POST.get('collection'), bundle=bundle).first()

        if collection is None:
            messages.error(request, 'Pick a collection to move "{}" into.'.format(
                netcdf_file.title))
        elif netcdf_file.collection_id is not None:
            messages.error(request, '"{}" is already in a collection.'.format(netcdf_file.title))
        else:
            source_directory = netcdf_file.directory()
            basename = os.path.basename(netcdf_file.file.name)
            label_name = (basename[:-3] if basename.endswith('.nc') else basename) + '.xml'

            netcdf_file.collection = collection
            netcdf_file.save(update_fields=['collection'])

            destination_directory = netcdf_file.directory()
            os.makedirs(destination_directory, exist_ok=True)

            moved = False
            source_file = os.path.join(source_directory, basename)
            if os.path.exists(source_file) and source_directory != destination_directory:
                # shutil.move for the same cross-filesystem reason the upload path uses it.
                shutil.move(source_file, os.path.join(destination_directory, basename))
                moved = True

            # The rebuild writes a new label in the collection directory, so remove the old one.
            stale_label = os.path.join(source_directory, label_name)
            if source_directory != destination_directory and os.path.exists(stale_label):
                os.remove(stale_label)

            errors = regenerate_netcdf_labels(bundle, [netcdf_file]) if moved else []
            if errors:
                messages.warning(request, 'Moved "{}" into {}, but its label could not be '
                                          'rebuilt: {}'.format(netcdf_file.title,
                                                               collection.collection_name,
                                                               '; '.join(errors)))
            else:
                messages.success(request, 'Moved "{}" into {}.'.format(
                    netcdf_file.title, collection.collection_name))

    return HttpResponseRedirect('/elsa/build/' + str(pk_bundle) + '/')


@login_required
def netcdf_ama_reset(request, pk_bundle, pk_netcdf):
    """Drop a file's overrides so it follows its collection's defaults again."""
    bundle = get_object_or_404(Bundle, pk=pk_bundle)

    if request.user != bundle.user:
        return redirect('main:restricted_access')

    netcdf_file = get_object_or_404(NetCDFFile, pk=pk_netcdf, bundle=bundle)

    if request.method == 'POST':
        for model_class, _form_class, _prefix, _label in AMA_SECTIONS:
            model_class.objects.filter(netcdf_file=netcdf_file).delete()
        _report_label_refresh(request, regenerate_netcdf_labels(bundle, [netcdf_file]))

    if _is_panel_request(request) and netcdf_file.collection is not None:
        forms_by_prefix = _build_ama_forms(None, netcdf_file.collection, netcdf_file, 'file')
        return render(request, 'build/bundle/_ama_panel.html',
                      _ama_panel_context(netcdf_file.collection, netcdf_file, forms_by_prefix,
                                         saved=True))

    return HttpResponseRedirect(
        reverse('build:netcdf_ama', kwargs={'pk_bundle': bundle.pk, 'pk_netcdf': netcdf_file.pk}))


@login_required
def netcdf_ama_copy(request, pk_bundle, pk_netcdf):
    """Copy one section's values from another file in the same collection.

    Only writes a per-file override. Reads the source's resolved values, so copying from
    a file that follows the default still works.
    """
    bundle = get_object_or_404(Bundle, pk=pk_bundle)

    if request.user != bundle.user:
        return redirect('main:restricted_access')

    netcdf_file = get_object_or_404(NetCDFFile, pk=pk_netcdf, bundle=bundle)
    collection = netcdf_file.collection

    if request.method != 'POST' or collection is None:
        return HttpResponseRedirect(
            reverse('build:netcdf_ama',
                    kwargs={'pk_bundle': bundle.pk, 'pk_netcdf': netcdf_file.pk}))

    sections_by_prefix = {
        prefix: (model_class, label)
        for model_class, _form_class, prefix, label in AMA_SECTIONS}
    section = sections_by_prefix.get(request.POST.get('section'))

    # Same collection only, another collection would be a different model run.
    source = NetCDFFile.objects.filter(
        pk=request.POST.get('source_netcdf'), bundle=bundle, collection=collection).first()

    if section is None or source is None or source.pk == netcdf_file.pk:
        messages.error(request, 'Pick a different file in this collection to copy from.')
    else:
        model_class, label = section
        resolved = model_class.resolve_for_file(source)
        if resolved is None or not resolved.filled_values():
            messages.error(request, '"{}" has no {} to copy.'.format(source.title, label))
        else:
            record = model_class.objects.filter(netcdf_file=netcdf_file).first()
            if record is None:
                record = model_class(bundle=bundle, collection=collection,
                                     netcdf_file=netcdf_file)
            record.copy_values_from(resolved)
            record.save()
            # This message already covers it, so don't add a second "Saved." alert.
            errors = regenerate_netcdf_labels(bundle, [netcdf_file])
            if errors:
                messages.warning(request, '{} copied from "{}", but the label could not be '
                                          'refreshed: {}'.format(label, source.title,
                                                                 '; '.join(errors)))
            else:
                messages.success(request, '{} copied from "{}".'.format(label, source.title))

    if _is_panel_request(request):
        forms_by_prefix = _build_ama_forms(None, collection, netcdf_file, 'file')
        return render(request, 'build/bundle/_ama_panel.html',
                      _ama_panel_context(collection, netcdf_file, forms_by_prefix, saved=True))

    return HttpResponseRedirect(
        reverse('build:netcdf_ama', kwargs={'pk_bundle': bundle.pk, 'pk_netcdf': netcdf_file.pk}))


@login_required
@require_POST
def label_updates_seen(request, pk_bundle):
    """Dismiss the notice about changes ELSA made to this bundle's labels."""
    bundle = get_object_or_404(Bundle, pk=pk_bundle)
    if request.user != bundle.user:
        return redirect('main:restricted_access')
    label_upgrades.mark_seen(bundle)
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'ok': True})
    return HttpResponseRedirect(reverse('build:bundle', kwargs={'pk_bundle': bundle.pk}))


# This view is for when a user clicks the submit bundle for review button on the bundle detail page (Currently implemented for External Bundles- Rupak).  This view marks the bundle as submitted by adding a timestamp to the submitted_at field in the Bundle model, and then sends an email to ATM with the details of the submission.
@login_required
def submit_bundle_internal(request, pk_bundle):
    print('\n\n')
    print('-------------------------------------------------------------------------')
    print('\n\n------------------ Submit Bundle with ELSA -------------------')
    print('------------------------------ DEBUGGER ---------------------------------')

    bundle = Bundle.objects.get(pk=pk_bundle)

    if request.user == bundle.user:
        if request.method == 'POST':
            # Check here too, not just by disabling the button.
            # 
            # Repair first so staff who pass the gate still send clean labels. If the repair changes
            # anything, the last check is out of date and the gate asks for a new one.
            # Submitting again is the owner sending it, so a submitted bundle gets upgraded too.
            label_upgrades.safe_upgrade(bundle, source=label_upgrades.SOURCE_SUBMIT,
                                        include_submitted=True, wait_seconds=60)
            validate_runner.repair_before_check(bundle)
            blocked = validate_runner.submission_block(bundle, request.user)
            if blocked is not None:
                _reason, explanation = blocked
                messages.warning(
                    request,
                    'This bundle was not submitted. {}'.format(explanation))
                return HttpResponseRedirect(
                    reverse('build:bundle', kwargs={'pk_bundle': pk_bundle}))

            is_resubmission = bundle.submitted_at is not None
            bundle.submitted_at = timezone.now()
            bundle.save()

            # Goes in the Admin Console as well as the email.
            case = staff_hooks.open_case(
                'submission', 'Bundle "{}"'.format(bundle.name), requester=bundle.user,
                bundle=bundle, body='The bundle was resubmitted.' if is_resubmission else '',
                data={'bundle_name': bundle.name, 'bundle_type': bundle.bundle_type})

            # No second check here, the gate just confirmed one that matches these files.

            submitter = bundle.user.get_full_name() or bundle.user.username
            subject = '{} Bundle "{}" by {}'.format(
                'Resubmission:' if is_resubmission else 'New Submission:',
                bundle.name,
                bundle.user.username
            )
            summary = validation_summary(bundle)
            context = {
                'is_resubmission': is_resubmission,
                'headline': 'Bundle resubmitted' if is_resubmission else 'New bundle submission',
                'lead': '{} {} the bundle "{}" for review.'.format(
                    submitter, 'resubmitted' if is_resubmission else 'submitted', bundle.name),
                'bundle': bundle,
                'submitter': submitter,
                'submitter_email': bundle.user.email,
                'submitted': localtime(bundle.submitted_at).strftime('%B %d, %Y at %I:%M %p %Z'),
                'archive_path': bundle.directory(),
                # Staff review on the case page, so no download link here.
                'case_url': staff_hooks.case_url(request, case),
                'validation': summary,
                'validation_text': validation_summary_for_email(bundle, summary),
            }

            try:
                email = build_branded(
                    # Add pds-atm@nmsu.edu if it needs to be sent to all
                    ['lneakras@nmsu.edu', 'rupakdey@nmsu.edu'],
                    subject, 'build/email/bundle_submitted', context)
                if bundle.user.email:
                    email.reply_to = [bundle.user.email]
                email.send()
                messages.success(request, 'Bundle submitted for review! A notification has been sent to ATM.')
            except Exception:
                logger.exception('submission email for bundle %s failed', bundle.pk)
                messages.warning(request, 'Bundle marked as submitted, but the email notification could not be sent. Please contact ATM directly.')

            return HttpResponseRedirect('/elsa/build/' + str(pk_bundle) + '/')

    else:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')


@login_required
def bundle_download(request, pk_bundle):
    """The bundle folder as a .tar.gz for its owner."""
    bundle = get_object_or_404(Bundle, pk=pk_bundle)

    if request.user != bundle.user:
        # Older submission emails link here. Staff get the console download, which checks
        # their role and admin mode and logs it.
        console = staff_hooks.staff_download_path(request.user, bundle)
        if console:
            return HttpResponseRedirect(console)
        return redirect('main:restricted_access')

    directory = bundle.directory()
    if not bundle.directory_is_contained() or not os.path.isdir(directory):
        messages.error(request, 'The files for this bundle could not be found on the server. '
                                'Please contact the ELSA team.')
        return HttpResponseRedirect(reverse('build:bundle', kwargs={'pk_bundle': bundle.pk}))

    return bundle_tarball_response(directory)


# The bundle_delete view is the page a user sees once they select the delete bundle button.  This view gives the user an option to confirm or take back their choice.  This view could be improved upon.
@login_required
def bundle_delete(request, pk_bundle):

    # Get Bundle
    bundle = Bundle.objects.get(pk=pk_bundle)

    # Secure ELSA by seeing if the user logged in is the same user associated with the Bundle
    if request.user == bundle.user:
        print('authorized user: {}'.format(request.user))

        # ELSA's current user is the bundle user so begin view logic
        print('\n\n')
        print('---------------------------------------------------------------------------------\n')
        print('---------------------- Start Bundle Delete --------------------------------------\n')
        print('---------------------------------------------------------------------------------\n')

        print('\n\n------------------------------- BUNDLE INFO -------------------------------')
        print('Bundle: {}'.format(bundle))
        print('User: {}'.format(bundle.user))
        print('Request: {}'.format(request.user))

        confirm_form = ConfirmForm(request.POST or None)
        context_dict = {}
        context_dict['bundle'] = bundle
        context_dict['user'] = bundle.user
        # CHANGE CONTEXT_DICT KEY TO 'confirm_form' #
        context_dict['delete_bundle_form'] = confirm_form
        context_dict['user_response'] = 'empty'

        print('Form confirm_form is valid: {}'.format(confirm_form.is_valid()))
        print('Response user_response is: {}'.format(
            context_dict['user_response']))
        if confirm_form.is_valid():
            print('Delete Bundle? {}'.format(
                confirm_form.cleaned_data["decision"]))
            decision = confirm_form.cleaned_data['decision']
            if decision == 'Yes':
                context_dict['decision'] = 'was'
                success_status = bundle.remove_bundle()
                bundle.delete()
                if success_status:
                    return redirect('../../success_delete/')

            if decision == 'No':
                # Go back to bundle page
                context_dict['decision'] = 'was not'

        return render(request, 'build/bundle/confirm_delete.html', context_dict)

    # Secure: Current user is not the user associated with the bundle, so...
    else:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')


def bundle_delete_new(request, pk_bundle):
    bundle = Bundle.objects.get(pk=pk_bundle)

    if request.user == bundle.user:
        print('authorized user: {}'.format(request.user))

        # ELSA's current user is the bundle user so begin view logic
        print('\n\n')
        print('---------------------------------------------------------------------------------\n')
        print('---------------------- Start Bundle Delete --------------------------------------\n')
        print('---------------------------------------------------------------------------------\n')

        print('\n\n------------------------------- BUNDLE INFO -------------------------------')
        print('Bundle: {}'.format(bundle))
        print('User: {}'.format(bundle.user))
        print('Request: {}'.format(request.user))

        success_status = bundle.remove_bundle()
        bundle.delete()
        if success_status:
            return redirect('../../success_delete/') 
    else:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')


@login_required
def success_delete(request):
    return render(request, 'build/bundle/success_delete.html')



#helper function to get the bundle name from the bundle id
def bundle_title(xml_content: str) -> str:
    try:
        ns = {'pds': 'http://pds.nasa.gov/pds4/pds/v1'}
        root = ET.fromstring(xml_content.encode('utf-8'))
        title_element = root.find('.//pds:Identification_Area/pds:title', namespaces=ns)
        return title_element.text.strip() if title_element is not None else None
    except ET.XMLSyntaxError as e:
        print(f"[XML ERROR] Could not parse XML: {e}")
        return None
    
@walkthrough.moved_to_setup('citation_information')
def citation_information(request, pk_bundle):
    print('\n\n')
    print('-------------------------------------------------------------------------')
    print('\n\n--------------- Add Citation_Information with ELSA -------------------')
    print('------------------------------ DEBUGGER ---------------------------------')

    bundle = Bundle.objects.get(pk=pk_bundle)

    # Secure ELSA by seeing if the user logged in is the same user associated with the Bundle
    if request.user == bundle.user:
        print('authorized user: {}'.format(request.user))

        # Get forms
        # citation_information.objects.get(pk=pk_citation_information)
        # initial_citation_information = {
        #     'author_list': citation_information.author_list,
        #     'editor_list': citation_information.editor_list,
        #     'publication_year': citation_information.publication_year,
        #     'description': citation_information.description,
        #     'keyword': citation_information.keyword,
        # }
        form_citation_information = CitationInformationForm(
            request.POST or None,
            initial={'publication_year': timezone.now().year},
            bundle=bundle,
        )
        # if form_citation_information and form_citation_information.has_changed:
        #     print('changed: {}', format(form_citation_information.changed_data))

        #     for change in form_citation_information.changed_data:
        #         if change == 'author_list':
        #             citation_information.author_list = form_citation_information['author_list'].value()
        #         elif change == 'editor_list':
        #             citation_information.editor_list = form_citation_information['editor_list'].value()
        #         elif change == 'publication_year':
        #             citation_information.publication_year = form_citation_information['publication_year'].value()
        #         elif change == 'description':
        #             citation_information.description = form_citation_information['description'].value()
        #         elif change == 'keyword':
        #             citation_information.keyword = form_citation_information['keyword'].value()
        #         citation_information.save()

        #citation_information.objects.get(pk=pk_citation_information)
        # Declare context_dict for template
        context_dict = {
            'form_citation_information': form_citation_information,
            'bundle': bundle,
            'bundle_type': bundle.bundle_type, #Rupak
            'walkthrough': walkthrough.progress(bundle, 'citation_information'),
        }

        # After ELSAs friend hits submit, if the forms are completed correctly, we should enter
        # this conditional.
        print('\n\n----------------- CITATION_INFORMATION INFO -------------------------')
        if form_citation_information.is_valid():
            print('form_citation_information is valid')
            # Create Citation_Information model object
            citation_information = form_citation_information.save(commit=False)
            citation_information.bundle = bundle
            citation_information.save()
            print('Citation Information model object: {}'.format(citation_information))
            
            product_bundle = Product_Bundle.objects.get(bundle=bundle)
            product_collections_list = bundle_label_targets(bundle)

            write_into_label(citation_information, product_bundle, product_collections_list)
            # A NetCDF uploaded before the citation existed has no citation in its label.
            mirror_citation_into_data_products(bundle)
            # Keep the people in the database, then write every label from there.
            bundle_setup.rewrite_from_label(citation_information)

            print('------------- End Build Citation Information -------------------')

            next_page = request.GET.get('next')

            if next_page == 'bundle':
                return redirect(reverse('build:bundle', args=[pk_bundle]))
            else:
                return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/citation_information_current/' + str(citation_information.pk) + '/')
            # return redirect(reverse('build:context_search', args=[pk_bundle]))
            
        # Update context_dict with the current Citation_Information models associated with the user's bundle
        context_dict['citation_information_set'] = Citation_Information.objects.filter(bundle=bundle)
        return render(request, 'build/citation_information/citation_information.html', context_dict)

    # Secure: Current user is not the user associated with the bundle, so...
    else:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')

@walkthrough.moved_to_setup('citation_information')
def edit_citation_information(request, pk_bundle, pk_citation_information):
    print('\n\n')
    print('-------------------------------------------------------------------------')
    print('\n\n--------------- Edit Citation_Information with ELSA -------------------')
    print('------------------------------ DEBUGGER ---------------------------------')

    bundle = Bundle.objects.get(pk=pk_bundle)

    context_dict = {}

    # Secure ELSA by seeing if the user logged in is the same user associated with the Bundle
    if request.user == bundle.user:
        print('authorized user: {}'.format(request.user))

        # Get forms
        citation_information = Citation_Information.objects.get(pk=pk_citation_information)
        form_edit_citation_information = EditCitationInformationForm(request.POST or None, pk_cit=pk_citation_information)

        if form_edit_citation_information.is_valid():
            print('form_citation_information is valid')
            cleaned_form = form_edit_citation_information.cleaned_data

            # Save the people, then write the citation into every label from the database.
            bundle_setup.store_rows(citation_information,
                                    bundle_setup.rows_from_edit_form(citation_information, cleaned_form))
            bundle_setup.write_citation(citation_information)

            # Back to the bundle if opened from there, otherwise the next walkthrough step.
            if walkthrough.returning_to_bundle(request):
                messages.success(request, 'Citation information updated.')
            return redirect(walkthrough.after_save(request, bundle, 'citation_information'))


        context_dict = {
            'form_edit_citation_information': form_edit_citation_information,
            'bundle': bundle,
            'citation_information': citation_information,
            # Same page for the walkthrough and the bundle page. Only the walkthrough gets the stepper.
            'walkthrough': None if walkthrough.returning_to_bundle(request)
                           else walkthrough.progress(bundle, 'citation_information'),
        }

            # return render(request, 'build/citation_information/citation_information_current.html', context_dict)
        return render(request, 'build/citation_information/citation_information_current.html', context_dict)
        
    else:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')


@walkthrough.moved_to_setup('modification_history')
def modification_history(request, pk_bundle):
    print('\n\n')
    print('-------------------------------------------------------------------------')
    print('\n\n--------------- Add  Modification History  with ELSA -------------------')
    print('------------------------------ DEBUGGER ---------------------------------')

    bundle = Bundle.objects.get(pk=pk_bundle)

    # Secure ELSA by seeing if the user logged in is the same user associated with the Bundle
    if request.user == bundle.user:
        print('authorized user: {}'.format(request.user))

        # Get forms
        form_modification_history = ModificationHistoryForm(request.POST or None)

        # Declare context_dict for template
        context_dict = {
            'form_modification_history': form_modification_history,
            'bundle': bundle,
            'walkthrough': walkthrough.progress(bundle, 'modification_history'),
        }

        # After ELSAs friend hits submit, if the forms are completed correctly, we should enter
        # this conditional.
        print('\n\n-----------------  Modification History INFO -------------------------')
        if form_modification_history.is_valid():
            print('form_modification_history is valid')
            # Create modification_history model object
            modification_history = form_modification_history.save(commit=False)
            modification_history.bundle = bundle
            modification_history.save()
            print(' Modification History  model object: {}'.format(modification_history))

            product_bundle = Product_Bundle.objects.get(bundle=bundle)
            product_collections_list = bundle_label_targets(bundle)

            write_into_label(modification_history, product_bundle, product_collections_list)

            print('------------- End Build  Modification History  -------------------')
            return redirect(walkthrough.after_save(request, bundle, 'modification_history'))


        # Update context_dict with the current  Modification History  models associated with the user's bundle
        context_dict['modification_history_set'] = Modification_History.objects.filter(bundle=bundle)

        return render(request, 'build/modification_history/mod_history.html', context_dict)

    # Secure: Current user is not the user associated with the bundle, so...
    else:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')


@walkthrough.moved_to_setup('context_search')
def context_search(request, pk_bundle):
    print('\n\n')
    print('-------------------------------------------------------------------------')
    print('\n\n------------------- Context Search with ELSA ------------------------')
    print('------------------------------ DEBUGGER ---------------------------------')

    # Get bundle and collections
    bundle = Bundle.objects.get(pk=pk_bundle)
    #    collections = Collections.objects.get(bundle=bundle)

    # Secure ELSA by seeing if the user logged in is the same user associated with the Bundle
    if request.user == bundle.user:
        print('authorized user: {}'.format(request.user))

        form_investigation = InvestigationForm(request.POST or None)
        form_target = TargetFormAll(request.POST or None, pk_bundle=pk_bundle)
        contact_form = ContactForm(request.POST or None)
        context_products_contact = ContextProductsContactForm(request.POST or None)

        # Context Dictionary
        context_dict = {
            'bundle': bundle,
            'form_investigation': form_investigation,
            'form_target': form_target,
            'investigation_list': bundle.investigations.all(),
            'instrument_host_list': bundle.instrument_hosts.all(),
            'instrument_list': bundle.instruments.all(),
            'target_list': bundle.targets.all(),
            'facility_list': bundle.facilities.all(),
            'telescope_list': bundle.telescopes.all(),
            'contact_form': contact_form,
            'context_products_contact' : context_products_contact,
            'walkthrough': walkthrough.progress(bundle, 'context_search'),
        }

        return render(request, 'build/context/context_search.html', context_dict)

    # Secure: Current user is not the user associated with the bundle, so...
    else:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')


def context_search_investigation(request, pk_bundle):
    print('\n\n')
    print('-------------------------------------------------------------------------')
    print('\n\n--------------- Add Context: Investigation with ELSA ----------------')
    print('------------------------------ DEBUGGER ---------------------------------')

    # Get bundle and collections
    bundle = Bundle.objects.get(pk=pk_bundle)

    # Secure ELSA by seeing if the user logged in is the same user associated with the Bundle
    if request.user == bundle.user:
        print('authorized user: {}'.format(request.user))

        # Get form for observing system component
        form_investigation = InvestigationForm(request.POST or None)
        context_products_contact = ContextProductsContactForm(request.POST or None)
        
        # Context Dictionary
        context_dict = {
            'bundle': bundle,
            'form_investigation': form_investigation,
            'bundle_investigation_set': bundle.investigations.all(),
            'context_products_contact': context_products_contact,
        }

        # If the user just added an investigation, add it to the context dictionary
        # so we can notify the user it has been added
        if request.method == 'POST':
            if form_investigation.is_valid():
                print(form_investigation.cleaned_data['investigation'].file_ref)
                print(Investigation.objects.filter(file_ref=form_investigation.cleaned_data['investigation'].file_ref).first().file_ref)
                i = Investigation.objects.filter(file_ref=form_investigation.cleaned_data['investigation'].file_ref).first()
                # i = Investigation.objects.get(
                #     file_ref=form_investigation.cleaned_data['investigation'].file_ref)
                context_dict['investigation'] = i
                bundle.investigations.add(i)
                print(i)

                # Label Fix for context products - Said
                product_bundle = Product_Bundle.objects.get(bundle=bundle)
                # Ok this gets confusing, and will add documentation later. Let me briefly explain here. - Said
                # Querying DB for product_collection objects such as document, context, and so on. Need a separate query for additional collections, so I do that query.
                # Chain(query1, query2), creates a pseudo-union of both queries, then allows write_into_label to write into all necessary labels.
                product_collections_list = chain(Product_Collection.objects.filter(bundle=bundle).exclude(collection='Data'), AdditionalCollections.objects.filter(bundle=bundle))

                write_into_label(i, product_bundle, product_collections_list)

            messages.success(request, 'Investigation Product Added')
            context_dict['successful_submit'] = True
            # return render(request, 'build/context/context_search_investigation.html', context_dict)
            return render(request, 'build/bundle/bundle.html', context_dict)
            #return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/')
        
        context_dict['messages'] = messages.get_messages(request)
        return render(request, 'build/bundle/bundle.html', context_dict)
        #return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/')
        # return HttpResponseRedirect('/build/' + pk_bundle + '/')
        # return render(request, 'build/context/context_search_investigation.html', context_dict)

    # Secure: Current user is not the user associated with the bundle, so...
    else:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')

def context_search_instrument_host_and_facility(request, pk_bundle, pk_investigation):
    print('\n\n')
    print('-------------------------------------------------------------------------')
    print('\n\n-------------- Add Context: Instrument Host with ELSA ---------------')
    print('------------------------------ DEBUGGER ---------------------------------')

    # Get bundle and collections
    bundle = Bundle.objects.get(pk=pk_bundle)
    investigation = Investigation.objects.get(pk=pk_investigation)

    # Secure ELSA by seeing if the user logged in is the same user associated with the Bundle
    if request.user == bundle.user:
        print('authorized user: {}'.format(request.user))

        # Get form for observing system component
        form_instrument_host = InstrumentHostForm(
            request.POST or None, pk_inv=pk_investigation)
        
        form_facility = FacilityForm(request.POST or None)

        # Context Dictionary
        context_dict = {
            'bundle': bundle,
            'investigation': investigation,
            'form_instrument_host': form_instrument_host,
            'bundle_instrument_host_set': bundle.instrument_hosts.all(),  # We could add filters
            'form_facility': form_facility,
            'bundle_facility_set': bundle.facilities.all(),
        }

        # If the user just added an instrument host, add it to the context dictionary
        # so we can notify the user it has been added
        if request.method == 'POST':
            if form_instrument_host.is_valid():
                i = Instrument_Host.objects.filter(
                    file_ref=form_instrument_host.cleaned_data['instrument_host'].file_ref).first()
                context_dict['instrument_host'] = i
                bundle.instrument_hosts.add(i)

                i.investigations.add(investigation)
                i.save()

                print(i.investigations)

                # Label Fix for context products - Said
                product_bundle = Product_Bundle.objects.get(bundle=bundle)
                # Ok this gets confusing, and will add documentation later. Let me briefly explain here. - Said
                # Querying DB for product_collection objects such as document, context, and so on. Need a separate query for additional collections, so I do that query.
                # Chain(query1, query2), creates a pseudo-union of both queries, then allows write_into_label to write into all necessary labels.
                product_collections_list = chain(Product_Collection.objects.filter(bundle=bundle).exclude(collection='Data'), AdditionalCollections.objects.filter(bundle=bundle))

                write_into_label(i, product_bundle, product_collections_list)
            if form_facility.is_valid():
                i = Facility.objects.filter(
                    name=form_facility.cleaned_data['facility']).first()
                context_dict['facility'] = i

                bundle.facilities.add(i)

                i.investigations.add(investigation)
                i.save()

                # Label Fix for context products - Said
                product_bundle = Product_Bundle.objects.get(bundle=bundle)
                # Ok this gets confusing, and will add documentation later. Let me briefly explain here. - Said
                # Querying DB for product_collection objects such as document, context, and so on. Need a separate query for additional collections, so I do that query.
                # Chain(query1, query2), creates a pseudo-union of both queries, then allows write_into_label to write into all necessary labels.
                product_collections_list = chain(Product_Collection.objects.filter(bundle=bundle).exclude(collection='Data'), AdditionalCollections.objects.filter(bundle=bundle))

                write_into_label(i, product_bundle, product_collections_list)

        return render(request, 'build/context/context_search_instrument_host_and_facility.html', context_dict)

    # Secure: Current user is not the user associated with the bundle, so...
    else:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')

@inplace_redirects
def context_search_target(request, pk_bundle):
    print('\n\n')
    print('-------------------------------------------------------------------------')
    print('\n\n------------------- Add Context: Targets with ELSA ------------------')
    print('------------------------------ DEBUGGER ---------------------------------')

    # Get bundle and collections
    bundle = Bundle.objects.get(pk=pk_bundle)

    # instrument_host = Instrument_Host.objects.get(pk=pk_instrument_host)

    # Secure ELSA by seeing if the user logged in is the same user associated with the Bundle
    if request.user == bundle.user:
        print('authorized user: {}'.format(request.user))

        # Get form for observing system component
        form_target = TargetFormAll(request.POST or None, pk_bundle=pk_bundle)

        # Context Dictionary
        context_dict = {
            'bundle': bundle,
            # 'instrument_host': instrument_host,
            'form_target': form_target,
            'bundle_target_set': bundle.targets.all(),  # We could add filters
        }

        # If the user just added an instrument host, add it to the context dictionary
        # so we can notify the user it has been added
        if request.method == 'POST':
            if form_target.is_valid():
                i = Target.objects.filter(file_ref=form_target.cleaned_data['target'].file_ref).first()
                context_dict['target'] = i
                bundle.targets.add(i)

                # i.investigations.add(investigation)

                # Label Fix for context products - Said
                product_bundle = Product_Bundle.objects.get(bundle=bundle)
                # Ok this gets confusing, and will add documentation later. Let me briefly explain here. - Said
                # Querying DB for product_collection objects such as document, context, and so on. Need a separate query for additional collections, so I do that query.
                # Chain(query1, query2), creates a pseudo-union of both queries, then allows write_into_label to write into all necessary labels.
                product_collections_list = chain(Product_Collection.objects.filter(bundle=bundle).exclude(collection='Data'), AdditionalCollections.objects.filter(bundle=bundle))

                write_into_label(i, product_bundle, product_collections_list)
                mirror_targets_into_products(bundle)
                messages.success(request, 'Target "{}" added.'.format(i.name))
            else:
                # Tell the user if the target wasn't added.
                if _inplace_form(request):
                    return _inplace_refused(form_target)
                messages.error(request, 'No target was added: {}'.format(
                    ' '.join(e for errs in form_target.errors.values() for e in errs)
                    or 'please choose one from the list.'))
                if walkthrough.returning_to_bundle(request):
                    return redirect(walkthrough.bundle_url(bundle))
                return redirect('build:context_search', pk_bundle=pk_bundle)

        if walkthrough.returning_to_bundle(request):
            return redirect(walkthrough.bundle_url(bundle))
        # On to the next walkthrough step. Archive has none after context, so back to the bundle page.
        return redirect(walkthrough.next_url(bundle, 'context_search'))


    # Secure: Current user is not the user associated with the bundle, so...
    else:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')

def context_search_target_inv(request, pk_bundle, pk_investigation):
    print('\n\n')
    print('-------------------------------------------------------------------------')
    print('\n\n------------------- Add Context: Targets with ELSA ------------------')
    print('------------------------------ DEBUGGER ---------------------------------')

    # Get bundle and collections
    bundle = Bundle.objects.get(pk=pk_bundle)

    investigation = Investigation.objects.get(pk=pk_investigation)

    # instrument_host = Instrument_Host.objects.get(pk=pk_instrument_host)

    # Secure ELSA by seeing if the user logged in is the same user associated with the Bundle
    if request.user == bundle.user:
        print('authorized user: {}'.format(request.user))

        # Get form for observing system component
        form_target = TargetForm(request.POST or None, pk_ins=pk_investigation, pk_bundle=pk_bundle)

        # Context Dictionary
        context_dict = {
            'bundle': bundle,
            'investigation': investigation,
            # 'instrument_host': instrument_host,
            'form_target': form_target,
            'bundle_target_set': bundle.targets.all(),  # We could add filters
        }

        # If the user just added an instrument host, add it to the context dictionary
        # so we can notify the user it has been added
        if request.method == 'POST':
            if form_target.is_valid():
                i = Target.objects.filter(file_ref=form_target.cleaned_data['target'].file_ref).first()
                context_dict['target'] = i
                bundle.targets.add(i)

                # Don't do i.investigations.add(investigation) here.
                # Investigation.targets is the PDS target list and it's shared by everyone's bundles.
                # The user's choice goes in bundle.targets.

                # Label Fix for context products - Said
                product_bundle = Product_Bundle.objects.get(bundle=bundle)
                # Ok this gets confusing, and will add documentation later. Let me briefly explain here. - Said
                # Querying DB for product_collection objects such as document, context, and so on. Need a separate query for additional collections, so I do that query.
                # Chain(query1, query2), creates a pseudo-union of both queries, then allows write_into_label to write into all necessary labels.
                product_collections_list = chain(Product_Collection.objects.filter(bundle=bundle).exclude(collection='Data'), AdditionalCollections.objects.filter(bundle=bundle))

                write_into_label(i, product_bundle, product_collections_list)

        return render(request, 'build/context/context_search_target_investigation.html', context_dict)

    # Secure: Current user is not the user associated with the bundle, so...
    else:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')


def context_search_instrument(request, pk_bundle, pk_investigation, pk_instrument_host):
    print('\n\n')
    print('-------------------------------------------------------------------------')
    print('\n\n--------------- Add Context: Instruments with ELSA ------------------')
    print('------------------------------ DEBUGGER ---------------------------------')

    # Get bundle and collections
    bundle = Bundle.objects.get(pk=pk_bundle)
    investigation = Investigation.objects.get(pk=pk_investigation)
    instrument_host = Instrument_Host.objects.get(pk=pk_instrument_host)
    # target = Target.objects.get(pk=pk_target)

    # Secure ELSA by seeing if the user logged in is the same user associated with the Bundle
    if request.user == bundle.user:
        print('authorized user: {}'.format(request.user))

        # Get form for observing system component
        form_instrument = InstrumentForm(
            request.POST or None, pk_ins=pk_instrument_host)

        # Context Dictionary
        context_dict = {
            'bundle': bundle,
            'investigation': investigation,
            'instrument_host': instrument_host,
            'form_instrument': form_instrument,
            'bundle_instrument_set': bundle.instruments.all(),  # We could add filters
        }

        # If the user just added an instrument host, add it to the context dictionary
        # so we can notify the user it has been added
        if request.method == 'POST':
            if form_instrument.is_valid():
                i = Instrument.objects.filter(
                    file_ref=form_instrument.cleaned_data['instrument'].file_ref).first()
                context_dict['instrument'] = i
                bundle.instruments.add(i)

                i.investigations.add(investigation)
                i.instrument_hosts.add(instrument_host)
                i.save()

                # Label Fix for context products - Said
                product_bundle = Product_Bundle.objects.get(bundle=bundle)
                # Ok this gets confusing, and will add documentation later. Let me briefly explain here. - Said
                # Querying DB for product_collection objects such as document, context, and so on. Need a separate query for additional collections, so I do that query.
                # Chain(query1, query2), creates a pseudo-union of both queries, then allows write_into_label to write into all necessary labels.
                product_collections_list = chain(Product_Collection.objects.filter(bundle=bundle).exclude(collection='Data'), AdditionalCollections.objects.filter(bundle=bundle))

                write_into_label(i, product_bundle, product_collections_list)

        return render(request, 'build/context/context_search_instrument.html', context_dict)

    # Secure: Current user is not the user associated with the bundle, so...
    else:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')


def context_search_facility(request, pk_bundle):
    print('\n\n')
    print('-------------------------------------------------------------------------')
    print('\n\n--------------- Add Context: Facility with ELSA ----------------')
    print('------------------------------ DEBUGGER ---------------------------------')

    # Get bundle and collections
    bundle = Bundle.objects.get(pk=pk_bundle)

    # Secure ELSA by seeing if the user logged in is the same user associated with the Bundle
    if request.user == bundle.user:
        print('authorized user: {}'.format(request.user))

        # Get form for observing system component
        form_facility = FacilityForm(request.POST or None)

        # Context Dictionary
        context_dict = {
            'bundle': bundle,
            'form_facility': form_facility,
            'bundle_facility_set': bundle.facilities.all(),
        }

        # If the user just added an investigation, add it to the context dictionary
        # so we can notify the user it has been added
        if request.method == 'POST':
            if form_facility.is_valid():
                i = Facility.objects.filter(
                    name=form_facility.cleaned_data['facility'].file_ref).first()
                context_dict['facility'] = i

                bundle.facilities.add(i)

                # i.inv.append(Investigation)

                # i.investigations.add(investigation)

                # Label Fix for context products - Said
                product_bundle = Product_Bundle.objects.get(bundle=bundle)
                # Ok this gets confusing, and will add documentation later. Let me briefly explain here. - Said
                # Querying DB for product_collection objects such as document, context, and so on. Need a separate query for additional collections, so I do that query.
                # Chain(query1, query2), creates a pseudo-union of both queries, then allows write_into_label to write into all necessary labels.
                product_collections_list = chain(Product_Collection.objects.filter(bundle=bundle).exclude(collection='Data'), AdditionalCollections.objects.filter(bundle=bundle))

                write_into_label(i, product_bundle, product_collections_list)

        return render(request, 'build/context/context_search_facility.html', context_dict)

    # Secure: Current user is not the user associated with the bundle, so...
    else:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')


def context_search_facility_instrument(request, pk_bundle, pk_investigation, pk_facility):
    print('\n\n')
    print('-------------------------------------------------------------------------')
    print('\n\n--------------- Add Context: Instruments with ELSA ------------------')
    print('------------------------------ DEBUGGER ---------------------------------')

    # Get bundle and collections
    bundle = Bundle.objects.get(pk=pk_bundle)
    facility = Facility.objects.get(pk=pk_facility)
    investigation = Investigation.objects.get(pk=pk_investigation)

    # Secure ELSA by seeing if the user logged in is the same user associated with the Bundle
    if request.user == bundle.user:
        print('authorized user: {}'.format(request.user))

        # Get form for observing system component
        form_instrument = FacilityInstrumentForm(
            request.POST or None, pk_fac=pk_facility)

        # Context Dictionary
        context_dict = {
            'bundle': bundle,
            'facility': facility,
            'investigation': investigation,
            'form_instrument': form_instrument,
            'bundle_instrument_set': bundle.instruments.all(),  # We could add filters
        }

        # If the user just added an instrument host, add it to the context dictionary
        # so we can notify the user it has been added
        if request.method == 'POST':
            if form_instrument.is_valid():
                i = Instrument.objects.filter(
                    file_ref=form_instrument.cleaned_data['instrument'].file_ref).first()
                context_dict['instrument'] = i
                bundle.instruments.add(i)

                i.investigations.add(investigation)
                i.save()

                # Label Fix for context products - Said
                product_bundle = Product_Bundle.objects.get(bundle=bundle)
                # Ok this gets confusing, and will add documentation later. Let me briefly explain here. - Said
                # Querying DB for product_collection objects such as document, context, and so on. Need a separate query for additional collections, so I do that query.
                # Chain(query1, query2), creates a pseudo-union of both queries, then allows write_into_label to write into all necessary labels.
                product_collections_list = chain(Product_Collection.objects.filter(bundle=bundle).exclude(collection='Data'), AdditionalCollections.objects.filter(bundle=bundle))

                write_into_label(i, product_bundle, product_collections_list)

        return render(request, 'build/context/context_search_facility_instrument.html', context_dict)

    # Secure: Current user is not the user associated with the bundle, so...
    else:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')


def context_search_telescope(request, pk_bundle, pk_investigation, pk_facility):
    print('\n\n')
    print('-------------------------------------------------------------------------')
    print('\n\n--------------- Add Context: Telescope with ELSA ----------------')
    print('------------------------------ DEBUGGER ---------------------------------')

    # Get bundle and collections
    bundle = Bundle.objects.get(pk=pk_bundle)
    investigation = Investigation.objects.get(pk=pk_investigation)
    facility = Facility.objects.get(pk=pk_facility)

    # Secure ELSA by seeing if the user logged in is the same user associated with the Bundle
    if request.user == bundle.user:
        print('authorized user: {}'.format(request.user))

        # Get form for observing system component
        form_telescope = TelescopeForm(request.POST or None)

        # Context Dictionary
        context_dict = {
            'bundle': bundle,
            'investigation': investigation,
            'facility': facility,
            'form_telescope': form_telescope,
            'bundle_telescope_set': bundle.telescopes.all(),
        }

        # If the user just added an investigation, add it to the context dictionary
        # so we can notify the user it has been added
        if request.method == 'POST':
            if form_telescope.is_valid():
                i = Telescope.objects.filter(
                    file_ref=form_telescope.cleaned_data['telescope'].file_ref).first()
                context_dict['telescope'] = i
                bundle.telescopes.add(i)

                # i.investigations.add(investigation)
                i.investigations.add(investigation)
                i.facilities.add(facility)
                i.save()

                # Fill label seems wrong - Said
                # i.fill_label(bundle)
                # Label Fix for context products - Said
                product_bundle = Product_Bundle.objects.get(bundle=bundle)
                # Ok this gets confusing, and will add documentation later. Let me briefly explain here. - Said
                # Querying DB for product_collection objects such as document, context, and so on. Need a separate query for additional collections, so I do that query.
                # Chain(query1, query2), creates a pseudo-union of both queries, then allows write_into_label to write into all necessary labels.
                product_collections_list = chain(Product_Collection.objects.filter(bundle=bundle).exclude(collection='Data'), AdditionalCollections.objects.filter(bundle=bundle))

                write_into_label(i, product_bundle, product_collections_list)

        return render(request, 'build/context/context_search_telescope.html', context_dict)

    # Secure: Current user is not the user associated with the bundle, so...
    else:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')

def context_search_target_and_instrument(request, pk_bundle, pk_investigation, pk_facility):
    print('\n\n')
    print('-------------------------------------------------------------------------')
    print('\n\n-------------- Add Context: Instrument Host with ELSA ---------------')
    print('------------------------------ DEBUGGER ---------------------------------')

    # Get bundle and collections
    bundle = Bundle.objects.get(pk=pk_bundle)
    investigation = Investigation.objects.get(pk=pk_investigation)
    facility = Facility.objects.get(pk=pk_facility)

    # Secure ELSA by seeing if the user logged in is the same user associated with the Bundle
    if request.user == bundle.user:
        print('authorized user: {}'.format(request.user))

        # Get form for observing system component
        form_target = TargetForm(
            request.POST or None, pk_ins=pk_investigation, pk_bundle=pk_bundle)
        
        form_instrument = FacilityInstrumentForm(
            request.POST or None, pk_fac=pk_facility)

        # Context Dictionary
        context_dict = {
            'bundle': bundle,
            'investigation': investigation,
            'facility': facility,
            'form_target': form_target,
            'bundle_target_set': bundle.targets.all(),  # We could add filters
            'form_instrument': form_instrument,
            'bundle_instrument_set': bundle.instruments.all(),
        }

        # If the user just added an instrument host, add it to the context dictionary
        # so we can notify the user it has been added
        if request.method == 'POST':
            if form_target.is_valid():
                # target = request.POST.get('target')
                # print(target)
                # products = Target.objects.filter(name__icontains=target)

                # print(products)
                # filter important, will break all pages if touched - Said
                i = Target.objects.filter(file_ref=form_target.cleaned_data['target'].file_ref).first()
                context_dict['target'] = i
                bundle.targets.add(i)

                # Label Fix for context products - Said
                product_bundle = Product_Bundle.objects.get(bundle=bundle)
                # Ok this gets confusing, and will add documentation later. Let me briefly explain here. - Said
                # Querying DB for product_collection objects such as document, context, and so on. Need a separate query for additional collections, so I do that query.
                # Chain(query1, query2), creates a pseudo-union of both queries, then allows write_into_label to write into all necessary labels.
                product_collections_list = chain(Product_Collection.objects.filter(bundle=bundle).exclude(collection='Data'), AdditionalCollections.objects.filter(bundle=bundle))

                write_into_label(i, product_bundle, product_collections_list)
            if form_instrument.is_valid():
                i = Instrument.objects.filter(
                    file_ref=form_instrument.cleaned_data['instrument'].file_ref).first()
                context_dict['instrument'] = i
                bundle.instruments.add(i)

                i.investigations.add(investigation)
                i.save()
                
                # Label Fix for context products - Said
                product_bundle = Product_Bundle.objects.get(bundle=bundle)
                # Ok this gets confusing, and will add documentation later. Let me briefly explain here. - Said
                # Querying DB for product_collection objects such as document, context, and so on. Need a separate query for additional collections, so I do that query.
                # Chain(query1, query2), creates a pseudo-union of both queries, then allows write_into_label to write into all necessary labels.
                product_collections_list = chain(Product_Collection.objects.filter(bundle=bundle).exclude(collection='Data'), AdditionalCollections.objects.filter(bundle=bundle))

                write_into_label(i, product_bundle, product_collections_list)

        return render(request, 'build/context/context_search_target_and_instrument.html', context_dict)


# def collections(request, pk_bundle):
#     bundle = Bundle.objects.get(pk=pk_bundle)
#     form_additional_collections = AdditionalCollectionForm(request.POST or None)
#     form_document = ProductDocumentForm(request.POST or None)

#    # stage = request.GET.get('stage', 'document') # defaults to document

#     #bundle = Bundle.objects.get(pk=pk_bundle)
#     # if request.method == "POST" and request.POST.get("form_name") == "document_form":
#     #     document = form_document.save(commit = False)
#     #     document.bundle = bundle
#     #     document.save()
#     #     # redirect to the next page after saving
#     #     return redirect('build:collections_additional', pk_bundle=bundle.pk)


#     # decide which page we need to render
#     # if stage == 'additional':
#     #     template = 'build/collections/collections_additional.html'
#     # else:
#     #     template = 'build/collections/collections_document.html'

#     # current_stage = request.resolver_match.url_name

#     # if current_stage == 'collections_document':
#     #     template = 'build/collections/collections_document.html'
#     # elif current_stage == 'collections_additional':
#     #     template = 'build/collections/collections_additional.html'
#     # else:
#     #     template = 'build/collections/collections_document.html' # default to this

#     return render(request, 'build/collections/collections_additional.html', {"pk_bundle": pk_bundle, "bundle": bundle, "form_additional_collections": form_additional_collections, "form_document": form_document})
#     #return render(request, template, {"pk_bundle": pk_bundle, "bundle": bundle, "form_additional_collections": form_additional_collections, "form_document": form_document})
@login_required
@inplace_redirects
@walkthrough.moved_to_setup('annex_collection_document')
def annex_collection_document(request, pk_bundle):
    bundle = Bundle.objects.get(pk=pk_bundle)
    if request.user != bundle.user:
        return redirect('main:restricted_access')
    annex_form_document = AnnexProductDocumentForm(
        request.POST or None, request.FILES or None, bundle=bundle)

    if annex_form_document.is_valid():        
        document = annex_form_document.save(commit=False)
        document.bundle = bundle
        document.save()
        # The file itself, before the label is built, so the label names it.
        document_files.store(document, annex_form_document.prepared_file)
        document.build_base_case()

        print(document.label())
        # Open label - returns a list where index 0 is the label object and 1 is the tree
        print(' ... Opening Label ... ')
        label_list = open_label_with_tree(document.label())
        label_root = label_list[1]
        # Fill label - fills 
        print(' ... Filling Label ... ')
        #label_root = bundle.version.fill_xml_schema(label_root)
        label_root = document.fill_label(label_root)
        # Close label    
        print(' ... Closing Label ... ')
        close_label(label_list[0], label_root, label_list[2])          
        print('---------------- End Build Product_Document Base Case -------')                     


        product_bundle = Product_Bundle.objects.get(bundle=bundle)
        product_collections_list = bundle_label_targets(bundle)

        # The document collection has a new member, so rewrite its inventory and record count.
        rebuild_collection_inventories(bundle)
        mirror_citation_into_data_products(bundle)
        mirror_targets_into_products(bundle)

        messages.success(request, 'Document "{}" added.'.format(document.document_name))
        return redirect(walkthrough.after_save(request, bundle, 'annex_collection_document', 'doc_collection'))

        #write_into_label(document, product_bundle, product_collections_list)

    # Refused. From the bundle page's window, send the errors back to the open window.
    if _inplace_form(request):
        return _inplace_refused(annex_form_document)

    # Otherwise show this page, which has to know if it's the walkthrough or the bundle page.
    from_bundle = walkthrough.returning_to_bundle(request)
    return render(request, 'build/collections/annex_collection_document.html', {
        "pk_bundle": pk_bundle, "bundle": bundle, "annex_form_document": annex_form_document,
        "source": 'bundle' if from_bundle else 'walkthrough',
        "walkthrough": None if from_bundle else walkthrough.progress(bundle, 'annex_collection_document'),
    })

@login_required
def collection_document(request, pk_bundle):
    bundle = Bundle.objects.get(pk=pk_bundle)
    if request.user != bundle.user:
        return redirect('main:restricted_access')
    form_document = ProductDocumentForm(
        request.POST or None, request.FILES or None, bundle=bundle)

    if form_document.is_valid():        
        document = form_document.save(commit=False)
        document.bundle = bundle
        document.save()
        # The file itself, before the label is built, so the label names it.
        document_files.store(document, form_document.prepared_file)
        document.build_base_case()

        print(document.label())
        # Open label - returns a list where index 0 is the label object and 1 is the tree
        print(' ... Opening Label ... ')
        label_list = open_label_with_tree(document.label())
        label_root = label_list[1]
        # Fill label - fills 
        print(' ... Filling Label ... ')
        #label_root = bundle.version.fill_xml_schema(label_root)
        label_root = document.fill_label(label_root)
        # Close label    
        print(' ... Closing Label ... ')
        close_label(label_list[0], label_root, label_list[2])          
        print('---------------- End Build Product_Document Base Case -------')                     

        product_bundle = Product_Bundle.objects.get(bundle=bundle)
        product_collections_list = bundle_label_targets(bundle)

        rebuild_collection_inventories(bundle)
        mirror_citation_into_data_products(bundle)
        mirror_targets_into_products(bundle)

        return redirect(reverse('build:collection_additional', args=[pk_bundle]))

        #write_into_label(document, product_bundle, product_collections_list)

    return render(request, 'build/collections/collection_document.html', {"pk_bundle": pk_bundle, "bundle": bundle, "form_document": form_document})

@login_required
@walkthrough.moved_to_setup('collection_additional')
def collection_additional(request, pk_bundle):
    bundle = Bundle.objects.get(pk=pk_bundle)
    if request.user != bundle.user:
        return redirect('main:restricted_access')
    form_additional_collections = AdditionalCollectionForm(request.POST or None, bundle=bundle)
    return render(request, 'build/collections/collection_additional.html', {
        "pk_bundle": pk_bundle, "bundle": bundle, "form_additional_collections": form_additional_collections,
        "walkthrough": walkthrough.progress(bundle, 'collection_additional')})

@login_required
def data(request, pk_bundle, pk_data):
    """
    The data page displays a data object pk_data associated with bundle pk_bundle.
    The detail of the data page displays objects related to this particular data collection.
    The objects related to this collection are:
        1. Display Dictionary: None or 1
        2. Product Observational: None or More
        3. 
    """
    print('\n\n')
    print('-------------------------------------------------------------------------')
    print('\n\n---------------------- Add Data with ELSA ---------------------------')
    print('------------------------------ DEBUGGER ---------------------------------')
    # Get bundle
    bundle = Bundle.objects.get(pk=pk_bundle)

    # Secure ELSA by seeing if the user logged in is the same user associated with the Bundle
    if request.user == bundle.user:
        print('authorized user: {}'.format(request.user))

        # Get Data Object 
        data = Data.objects.get(pk=pk_data)
        # print(data.data_type)
        
        # Get related Display Dictionary
        # Get display dictionary to show what it says to the user
        try:
            print('Trying display get')
            # display_dictionary = DisplayDictionary.objects.get(data=data)
        except DisplayDictionary.DoesNotExist:
            print('Displaying get did not work')
            display_dictionary = None

        # Get related Product Observationals
        product_observational_set = Product_Observational.objects.filter(data=data)
        # data_object_set = Data_Object.objects.filter(data=data)

        # Get forms
        form_dictionary = DictionaryForm(request.POST or None)
        # form_display_dictionary = DisplayDictionaryForm(request.POST or None)
        form_product_observational = ProductObservationalForm(request.POST or None)

        # After ELSA's friend hits submit, if the form is completed correctly, we should
        # satisfy this conditional
        if form_dictionary.is_valid():
            if request.POST.get('dictionary_type') == 'Display':
                #display_dictionary = DisplayDictionary(data=data)
                #display_dictionary.save()

                # The xml schema declaration needs to be added to each currently existing
                # observational product that is an array and each new one added. Each new
                # one added should add the dictionary if it exists upon creation of the array.

                # Given each product in the product observational set
                for product_observational in product_observational_set:

                    # 1. Open appropriate label(s).  
                    print('- Label: {}'.format(product_observational.label()))
                    print(' ... Opening Label ... ')
                    label_list = open_label_with_tree(product_observational.label())
                    label_root = label_list[1]
        
                    # Build display dictionary within the label
                    print(' ... Building Label ... ')
                    print('Debug: Tree ---\n{}'.format(etree.tostring(label_root)))
                    label_root = product_observational.fill_display_dictionary(label_root)

                    print('Debug: Tree ---\n{}'.format(etree.tostring(label_root)))

                    # Close appropriate label(s)
                    print(' ... Closing Label ... ')
                    close_label(product_observational.label(), label_root, label_list[2])
                    # 1. Get root of product_observational label
                

           #display_dictionary = display_dictionary.save(commit=False)
           #display_dictionary.data = data
           #display_dictionary.save()

        #After ELSA's friend hits submit, if the form is completed correctly, we should
        #satisfy this conditional
        if form_product_observational.is_valid():
            # Make Product Observational
            product_observational = form_product_observational.save(commit=False)
            product_observational.bundle = bundle
            product_observational.data = data
            product_observational.save()
            print('Product Observational object: {}'.format(product_observational))

            # Make data directory
            print('Checking to see if data directory needs to be made')
            new_directory = data.build_directory()

            # If it's a new directory, we need a product_collection to describe the
            # collection. *** Currently: Just does base case. Fix in data model.
            if new_directory:
                data.build_product_collection()

            # Regardless if it's a new directory or not, we create the product_observational
            # to describe the current observations in the product
            #product_observational.build_base_case()
            ## Get Root: 
            #product_observational.fill_base_case()

        # if form_data_object.is_valid():
        #     # Make Product Observational
        #     data_object = form_data_object.save(commit=False)
        #     data_object.bundle = bundle
        #     data_object.data = data
        #     data_object.save()
        #     print('data_object object: {}'.format(data_object))

        #     # Make data directory
        #     print('Checking to see if data directory needs to be made')
        #     new_directory = data.build_directory()

        #     # If it's a new directory, we need a product_collection to describe the
        #     # collection. *** Currently: Just does base case. Fix in data model.
        #     if new_directory:
        #         data.build_product_collection()

        #     # Regardless if it's a new directory or not, we create the product_observational
        #     # to describe the current observations in the product
        #     #product_observational.build_base_case()
        #     ## Get Root: 
        #     #product_observational.fill_base_case()
            
        # Context Dictionary
        context_dict = {
            'bundle':bundle,
            'form_dictionary':form_dictionary,
            'data': data,
            #'display_dictionary':display_dictionary,
            #'product_observational_set':product_observational_set,
            #'form_data_object': form_data_object,
            #'data_object_set': data_object_set,
            'form_product_observational':form_product_observational,
        }

        return render(request, 'build/data/data.html', context_dict)

    # Secure: Current user is not the user associated with the bundle, so...
    else:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')


@login_required
def display_dictionary(request, pk_bundle, pk_data, pk_display_dictionary):
    print(' \n\n \n\n-------------------------------------------------------------------------')
    print('\n\n------------------- Add Display Dictionary with ELSA --------------------------')
    print('------------------------------ DEBUGGER ---------------------------------')

    # Get Bundle
    bundle = Bundle.objects.get(pk=pk_bundle)
    #    collections = Collections.objects.get(bundle=bundle)

    # Secure ELSA by seeing if the user logged in is the same user associated with the Bundle
    if request.user == bundle.user:
        print('authorized user: {}'.format(request.user))
        display_dictionary = DisplayDictionary.objects.get(
            pk=pk_display_dictionary)

        # ELSA's current user is the bundle user so begin view logic
        # Get forms
        try:
            cds = Color_Display_Settings.objects.get(
                display_dictionary=display_dictionary)
            initial_cds = {
                'color_display_axis': cds.color_display_axis,
                'comment_color_display': cds.comment_color_display,
                'red_channel_band': cds.red_channel_band,
                'green_channel_band': cds.green_channel_band,
                'blue_channel_band': cds.blue_channel_band,
            }
            form_color_display_settings = ColorDisplaySettingsForm(
                request.POST or None, initial=initial_cds)

        except Color_Display_Settings.DoesNotExist:
            form_color_display_settings = ColorDisplaySettingsForm(
                request.POST or None)

        try:
            dd = Display_Direction.objects.get(
                display_dictionary=display_dictionary)
            initial_dd = {
                'comment_display_direction': dd.comment_display_direction,
                'horizontal_display_axis': dd.horizontal_display_axis,
                'horizontal_display_direction': dd.horizontal_display_direction,
                'vertical_display_axis': dd.vertical_display_axis,
                'vertical_display_direction': dd.vertical_display_direction,
            }
            form_display_direction = DisplayDirectionForm(
                request.POST or None, initial=initial_dd)

        except Display_Direction.DoesNotExist:
            form_display_direction = DisplayDirectionForm(request.POST or None)

        try:
            mds = Movie_Display_Settings.objects.get(
                display_dictionary=display_dictionary)
            initial_mds = {
                'time_display_axis': mds.time_display_axis,
                'comment': mds.comment,
                'frame_rate': mds.frame_rate,
                'loop_flag': mds.loop_flag,
                'loop_count': mds.loop_count,
                'loop_delay': mds.loop_delay,
                'loop_delay_unit': mds.loop_delay_unit,
                'loop_back_and_forth_flag': mds.loop_back_and_forth_flag,
            }
            form_movie_display_settings = MovieDisplaySettingsForm(
                request.POST or None, initial=initial_mds)

        except Movie_Display_Settings.DoesNotExist:
            form_movie_display_settings = MovieDisplaySettingsForm(
                request.POST or None)

        # Declare context_dict for templating language used in ELSAs templates
        context_dict = {
            'bundle': bundle,
            'form_color_display_settings': form_color_display_settings,
            'form_display_direction': form_display_direction,
            'form_movie_display_settings': form_movie_display_settings,

        }

        # After ELSAs friend hits submit, if the forms are completed correctly, we should enter
        # this conditional.
        print('\n\n------------------------ DISPLAY DICTIONARY INFO ----------------------------')
        print('\nCurrently awaiting user input...\n\n')
        if form_color_display_settings.is_valid() and form_display_direction.is_valid() and form_movie_display_settings.is_valid():

            print('All Display Dictionary forms valid for {}.'.format(bundle.user))
            # Link the following to the given Array model object

            # Create Color_Display_Settings model object
            color_display_settings = form_color_display_settings.save(
                commit=False)
            color_display_settings.display_dictionary = display_dictionary
            color_display_settings.save()

            # Create Display_Direction model object
            display_direction = form_display_direction.save(commit=False)
            display_direction.display_dictionary = display_dictionary
            display_direction.save()

            # Create Display_Settings model object
            #display_settings = form_display_settings.save(commit=False)
            # Add association
            # display_settings.save()

            # Create Movie_Display_Direction model object
            movie_display_settings = form_movie_display_settings.save(
                commit=False)
            movie_display_settings.display_dictionary = display_dictionary
            movie_display_settings.save()

            # Find appropriate label(s).
            print(
                '---------------- End Build Display Dictionary ------------------------------')

        # Get current Display Dictionary object associated with the user's Bundle
        #alias_list = Alias.objects.filter(bundle=bundle)
        #context_dict['alias_list'] = alias_list
        return render(request, 'build/dictionary/display.html', context_dict)
    else:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')


def document(request, pk_bundle):
    print('-------------------------------------------------------------------------')
    print('\n\n--------------------- Add Document with ELSA ------------------------')
    print('------------------------------ DEBUGGER ---------------------------------')

    # Get forms
    form_product_document = ProductDocumentForm(request.POST or None, bundle=bundle)
    bundle = Bundle.objects.get(pk=pk_bundle)

    # Declare context_dict for template
    context_dict = {
        'form_product_document': form_product_document,
        'bundle': bundle,

    }

    # After ELSAs friend hits submit, if the forms are completed correctly, we should enter
    # this conditional.  We must do [] things: 1. Create the Document model object, 2. Add a Product_Document label to the Document Collection, 3. Add the Document as an Internal_Reference to the proper labels (like Product_Bundle and Product_Collection).
    print('\n\n---------------------- DOCUMENT INFO -------------------------------')
    if form_product_document.is_valid():
        print('form_product_document is valid')

        # Create Document Model Object
        product_document = form_product_document.save(commit=False)
        product_document.bundle = bundle
        product_document.save()
        print('Product_Document model object: {}').format(product_document)

        # Build Product_Document label using the base case template found in templates/pds4/basecase
        print('\n---------------Start Build Product_Document Base Case------------------------')
        product_document.build_base_case()
        # Open label - returns a list where index 0 is the label object and 1 is the tree
        print(' ... Opening Label ... ')
        label_list = open_label_with_tree(product_document.label())
        label_object = label_list[0]
        label_root = label_list[1]
        # Fill label - fills
        print(' ... Filling Label ... ')
        label_root = product_document.fill_base_case(label_root)
        # Close label
        print(' ... Closing Label ... ')
        close_label(label_object, label_root, label_list[2])
        print('---------------- End Build Product_Document Base Case -------------------------')

        # Add Document info to proper labels.  For now, I simply have Product_Bundle and Product_Collection.  This list will need to be updated.
        print('\n---------------Start Build Internal_Reference for Document-------------------')
        all_labels = []
        product_bundle = Product_Bundle.objects.get(bundle=bundle)
        product_collections_list = Product_Collection.objects.filter(
            bundle=bundle)

        all_labels.append(product_bundle)
        all_labels.extend(product_collections_list)

        for label in all_labels:
            print('- Label: {}').format(label)
            print(' ... Opening Label ... ')
            label_list = open_label_with_tree(label.label())
            label_object = label_list[0]
            label_root = label_list[1]

            # Build Internal_Reference
            print(' ... Building Internal_Reference ... ')
            label_root = label.build_internal_reference(
                label_root, product_document)

            # Close appropriate label(s)
            print(' ... Closing Label ... ')
            close_label(label_object, label_root, label_list[2])
        print(
            '\n----------------End Build Internal_Reference for Document-------------------')

        rebuild_collection_inventories(bundle)
        mirror_citation_into_data_products(bundle)
        mirror_targets_into_products(bundle)

    return render(request, 'build/document/document.html', context_dict)

def annex_product_document(request, pk_bundle, pk_product_document):
    # Get Bundle
    bundle = Bundle.objects.get(pk=pk_bundle)

    # Make sure the user is logged in with the same account associated with the bundle
    if request.user == bundle.user:
        print('authorized user: {}'.format(request.user))

        product_document = Product_Document.objects.get(pk=pk_product_document)

        initial_product = {
            "document_name":product_document.document_name,
            "document_id":product_document.document_id,
            "local_id":product_document.local_id,     
            "file_name":product_document.file_name,
            "comment":product_document.comment,
            "document_std_id":product_document.document_std_id,
        }

        annex_form_product_document = AnnexProductDocumentForm(request.POST or None, request.FILES or None, initial=initial_product, bundle=bundle, editing=product_document)
        documents = Product_Document.objects.filter(bundle=bundle)
        
        if annex_form_product_document.is_valid() and annex_form_product_document.has_changed():
            
            all_labels = []
            product_bundle = Product_Bundle.objects.get(bundle=bundle)
            product_collections_list = bundle_label_targets(bundle)
            # We need to check for Product_Collections associated with Data products now.
                    
            all_labels.append(product_bundle)
            all_labels.append(product_collections_list)

            # Open appropriate label(s).  
            print(' ... Opening Label ... ')

            #breaks here
            label_list = open_label_with_tree(product_document.label())
            old_name = product_document.label()

            for change in annex_form_product_document.changed_data:
                if change == 'document_name':
                   product_document.document_name = os.path.basename(annex_form_product_document['document_name'].value())
                
                elif change == 'local_id':
                    product_document.local_id = annex_form_product_document['local_id'].value()

                elif change == "document_id":
                    product_document.document_id = annex_form_product_document['document_id'].value()
                
                elif change == 'file_name':
                    product_document.file_name = annex_form_product_document['file_name'].value()
                
                elif change == 'comment':
                    product_document.comment = annex_form_product_document['comment'].value()

                elif change == 'document_std_id':
                    product_document.document_std_id = annex_form_product_document['document_std_id'].value()
                
            product_document.save()

            # Save a replacement file before rebuilding the label. Remove the old one if the name changed.
            if annex_form_product_document.prepared_file is not None:
                document_files.store(product_document, annex_form_product_document.prepared_file)

            label_root = label_list[1]

            # fix the document name path change error - deric
            os.rename(old_name, product_document.label())

            # Build document label
            print(' ... Building Label ... ')
            label_root = product_document.fill_label(label_root)
            #alias.alias_list.append(label_root)

            # Close appropriate label(s)
            print(' ... Closing Label ... ')
            close_label(product_document.label(), label_root, label_list[2])

        # An edit can change the identifier, so rewrite the inventory here too.
        rebuild_collection_inventories(bundle)
        mirror_citation_into_data_products(bundle)
        mirror_targets_into_products(bundle)

        print('Changed: {}'.format(annex_form_product_document.changed_data))

        context_dict = {
            'bundle':bundle,
            'documents':documents,
            'annex_form_product_document':annex_form_product_document,
            'product_document':product_document,
        }

        return render(request, 'build/collections/annex_collection_document.html', context_dict)

    # Secure: Current user is not the user associated with the bundle, so...
    else:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')                    

def product_document(request, pk_bundle, pk_product_document):
    print('\n\n')
    print('-------------------------------------------------------------------------')
    print('\n\n------------------ Add Product_Document with ELSA -------------------')
    print('------------------------------ DEBUGGER ---------------------------------')
    # Get bundle
    bundle = Bundle.objects.get(pk=pk_bundle)

    # Secure ELSA by seeing if the user logged in is the same user associated with the Bundle
    if request.user == bundle.user:
        print('authorized user: {}'.format(request.user))

        product_document = Product_Document.objects.get(pk=pk_product_document)
        if bundle.bundle_type == "External":
            initial_product = {
                "document_name":product_document.document_name,
                "document_id":product_document.document_id,
                "local_id":product_document.local_id,     
                "file_name":product_document.file_name,
                "comment":product_document.comment,
                "document_std_id":product_document.document_std_id,
            }
            # When editing the product document via the bundle page, we want to use the external form for external bundles
            form_product_document = AnnexProductDocumentForm(request.POST or None, request.FILES or None, initial=initial_product, bundle=bundle, editing=product_document)
        else:
            initial_product = {
                'author_list':product_document.author_list,
                'copyright':product_document.copyright,
                'description':product_document.description,
                # 'doi':product_document.doi,
                'document_editions':product_document.document_editions,
                'document_name':product_document.document_name,
                'publication_date':product_document.publication_date,
                'revision_id':product_document.revision_id,
                'edition_name': product_document.edition_name,
                'language': product_document.language,
                'files': product_document.files,
                'file_name': product_document.file_name,
                'local_id': product_document.local_id,
                'document_std_id': product_document.document_std_id,
            }
            
            form_product_document = ProductDocumentForm(request.POST or None, request.FILES or None, initial=initial_product, bundle=bundle, editing=product_document)
        documents = Product_Document.objects.filter(bundle=bundle)
        
        if form_product_document.is_valid() and form_product_document.has_changed():
            
            
            all_labels = []
            product_bundle = Product_Bundle.objects.get(bundle=bundle)
            product_collections_list = bundle_label_targets(bundle)
            # We need to check for Product_Collections associated with Data products now.
                    
            all_labels.append(product_bundle)
            all_labels.append(product_collections_list)

            # Open appropriate label(s).  
            print(' ... Opening Label ... ')

            #breaks here
            label_list = open_label_with_tree(product_document.label())
            old_name = product_document.label()

            for change in form_product_document.changed_data:
                if change == 'author_list':
                   product_document.author_list = form_product_document['author_list'].value()

                elif change == 'copyright':
                   product_document.copyright = form_product_document['copyright'].value()

                elif change == 'description':
                   product_document.description = form_product_document['description'].value()

                elif change == 'document_editions':
                   product_document.document_editions = form_product_document['document_editions'].value()

                elif change == 'document_name':
                   product_document.document_name = form_product_document['document_name'].value()

                elif change == 'publication_date':
                   product_document.publication_date = form_product_document['publication_date'].value()

                elif change == 'revision_id':
                   product_document.revision_id = form_product_document['revision_id'].value()

                # elif change == 'doi':
                #     product_document.doi = form_product_document['doi'].value()
                
                elif change == 'edition_name':
                    product_document.edition_name = form_product_document['edition_name'].value()
                
                elif change == 'language':
                    product_document.language = form_product_document['language'].value()
                
                elif change == 'files':
                    product_document.files = form_product_document['files'].value()
                
                elif change == 'file_name':
                    product_document.file_name = form_product_document['file_name'].value()
                
                elif change == 'local_id':
                    product_document.local_id = form_product_document['local_id'].value()
                
                elif change == 'document_std_id':
                    product_document.document_std_id = form_product_document['document_std_id'].value()    

                elif change == "document_id":
                    product_document.document_id = form_product_document['document_id'].value()
                
                elif change == 'comment':
                    product_document.comment = form_product_document['comment'].value()
                
                product_document.save()

            # Save a replacement file before rebuilding the label. Remove the old one if the name changed.
            if form_product_document.prepared_file is not None:
                document_files.store(product_document, form_product_document.prepared_file)

            label_root = label_list[1]

            # fix the document name path change error - deric
            os.rename(old_name, product_document.label())

            # Build document label
            print(' ... Building Label ... ')
            label_root = product_document.fill_label(label_root)
            #alias.alias_list.append(label_root)

            # Close appropriate label(s)
            print(' ... Closing Label ... ')
            close_label(product_document.label(), label_root, label_list[2])

            # An edit can change the identifier, so rewrite the inventories. Has to be before the redirect.
            rebuild_collection_inventories(bundle)
            mirror_citation_into_data_products(bundle)
            mirror_targets_into_products(bundle)

            messages.success(request, "Document successfully updated.")

            # Opened from the bundle page's document list: go back to it.
            if walkthrough.returning_to_bundle(request):
                return redirect(walkthrough.bundle_url(bundle, 'doc_collection'))
            return redirect(
                'build:product_document',
                pk_bundle=bundle.pk,
                pk_product_document=product_document.pk,
            )


        print('Changed: {}'.format(form_product_document.changed_data))

        context_dict = {
            'bundle':bundle,
            'documents':documents,
            'form_product_document':form_product_document,
            'product_document':product_document,
        }

        #possibly change this to redirect to the home bundle page
        return render(request, 'build/document/product_document.html', context_dict)

    # Secure: Current user is not the user associated with the bundle, so...
    else:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')

@login_required
@xframe_options_sameorigin
def document_file(request, pk_bundle, pk_product_document):
    """Show a document's file to its owner.

    Shown in place, ?download=1 saves it. The bundle page previews it in a frame.
    """
    bundle = get_object_or_404(Bundle, pk=pk_bundle)
    if request.user != bundle.user:
        return redirect('main:restricted_access')
    product_document = get_object_or_404(Product_Document, pk=pk_product_document, bundle=bundle)

    path = document_files.servable_path(product_document)
    if not path:
        raise Http404('This document has no file attached.')

    response = FileResponse(
        open(path, 'rb'),
        content_type=document_files.content_type(product_document),
        as_attachment=request.GET.get('download') == '1',
        filename=product_document.file_name,
    )
    response['X-Content-Type-Options'] = 'nosniff'
    # A replaced file keeps its URL, so never show a stale copy.
    response['Cache-Control'] = 'private, no-cache'
    return response


def delete_product_document(request, pk_bundle, pk_product_document):
    print('\n\n')
    print('-------------------------------------------------------------------------')
    print('\n\n------------------ Delete Product_Document with ELSA -------------------')
    print('------------------------------ DEBUGGER ---------------------------------')
    # Get bundle
    bundle = Bundle.objects.get(pk=pk_bundle)

    # Secure ELSA by seeing if the user logged in is the same user associated with the Bundle
    if request.user == bundle.user:
        print('authorized user')

        product_document = Product_Document.objects.get(pk=pk_product_document)
        product_bundle = Product_Bundle.objects.get(bundle=bundle)
        product_collections_list = Product_Collection.objects.filter(bundle=bundle)

        # Grab the XML file path BEFORE deleting the record
        try:
            xml_path = product_document.label()
            print('XML path to delete: {}'.format(xml_path))
        except Exception as e:
            xml_path = None
            print('Could not get label path: {}'.format(e))

        remove_from_label(product_document, product_bundle, product_collections_list)

        # Delete the XML file from disk
        if xml_path:
            if os.path.exists(xml_path):
                os.remove(xml_path)
                print('Deleted XML file: {}'.format(xml_path))
            else:
                print('XML file not found at path: {}'.format(xml_path))

        # Remove the document's file too, while the record still has its path.
        document_files.remove(product_document)

        # Delete the product_document from the database
        product_document.delete()

        # Rewrite the inventory after the delete so it stops listing this product.
        for product_collection in product_collections_list:
            product_collection.build_inventory()

        return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/')

    # Secure: Current user is not the user associated with the bundle
    else:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')


def product_observational(request, pk_bundle, pk_product_observational):
    print('\n\n')
    print('-------------------------------------------------------------------------')
    print('\n\n---------------- Add Product_Observational with ELSA ----------------')
    print('------------------------------ DEBUGGER ---------------------------------')

    # Get bundle
    bundle = Bundle.objects.get(pk=pk_bundle)

    # Secure ELSA by seeing if the user logged in is the same user associated with the Bundle
    if request.user == bundle.user:
        print('authorized user: {}'.format(request.user))

        product_observational = Product_Observational.objects.get(
            pk=pk_product_observational)
        form_product_observational = TableForm(request.POST or None)
        context_dict = {
            'bundle': bundle,
            'product_observational': product_observational,
            'form_product_observational': form_product_observational,

        }

        print('\n\n----------------- PRODUCT_DOCUMENT INFO -----------------------------')
        if form_product_observational.is_valid():
            print('form_product_observational is valid.')
            # Create the associated model (Table, Array, Cube, etc...)
            observational = form_product_observational.save(commit=False)
            observational.product_observational = product_observational
            observational.save()
            print('observational object: {}'.format(observational))

            print(
                '\n--------- Start Add Observational to Product_Observational -----------------')
            # Open label
            print(' ... Opening Label ... ')
            label_list = open_label_with_tree(product_observational.label())
            label_object = label_list[0]
            label_root = label_list[1]
            print(label_root)

            # Fill label
            print(' ... Filling Label ... ')
            #label_root = bundle.version.fill_xml_schema(label_root)
            label_root = product_observational.fill_observational(
                label_root, observational)

            # Close label
            print(' ... Closing Label ... ')
            close_label(label_object, label_root, label_list[2])
            print(
                '-------------End Add Observational to Product_Observational -----------------')

        # Now we must grab the observational set to display on ELSA's template for the Product_Observational page.  Right now, this is tables so it is easy.
        observational_set = Table.objects.filter(
            product_observational=product_observational)
        context_dict['observational_set'] = observational_set

        return render(request, 'build/data/table.html', context_dict)

    # Secure: Current user is not the user associated with the bundle, so...
    else:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')


def Table_Creation(request, pk_bundle, pk_data):
    bundle = Bundle.objects.get(pk=pk_bundle)
    data = Data.objects.get(pk=pk_data)

    if request.user == bundle.user:
        if data.data_type == 'Table Delimited':
            print("delim form chosen")
            existing_table = Table_Delimited.objects.filter(data=data).first()
            data_form = Table_Delimited_Form(request.POST or None, instance=existing_table, pk_data=pk_data, pk_ins=data.name, pk_bun=pk_bundle)

        elif data.data_type == 'Table Binary':
            print("binary form chosen")
            existing_table = Table_Binary.objects.filter(data=data).first()
            data_form = Table_Binary_Form(request.POST or None, instance=existing_table, pk_data=pk_data, pk_ins=data.name, pk_bun=pk_bundle)

        elif data.data_type == 'Table Character':
            print("character form chosen")
            existing_table = Table_Fixed_Width.objects.filter(data=data).first()
            data_form = Table_Fixed_Width_Form(request.POST or None, instance=existing_table, pk_data=pk_data, pk_ins=data.name, pk_bun=pk_bundle)

        elif data.data_type == 'Array':
            existing_table = None
            data_form = ArrayForm(request.POST or None)

        context_dict = {
            'bundle': bundle,
            'data': data,
            'data_form': data_form,
        }

        
        if data_form.is_valid():
            print('data form valid')

            form = data_form.save(commit=False)
            form.bundle = bundle
            form.data = data
            form.collection = data.collection
            form.save()
            cleaned_form = data_form.cleaned_data

            label_list = open_label_with_tree(form.label())
            label_root = label_list[1]
            # Fill label - fills 
            print(' ... Filling Label ... ')
            #label_root = bundle.version.fill_xml_schema(label_root)
            label_root = form.fill_base_case(label_root, cleaned_form)
            # Close label    
            print(' ... Closing Label ... ')
            close_label(label_list[0], label_root, label_list[2]) 

            context_products = []
            context_products.extend(bundle.investigations.all())
            context_products.extend(bundle.instrument_hosts.all())
            context_products.extend(bundle.facilities.all())
            context_products.extend(bundle.instruments.all())
            context_products.extend(bundle.telescopes.all())
            context_products.extend(bundle.targets.all())
            
            for context_product in context_products:
                write_into_label(context_product, form, None)

            return HttpResponseRedirect('/elsa/build/' + str(pk_bundle) + '/table_field_information/' + str(pk_data) + '/' + str(form.pk) + '/')
        else:
            print(data_form.errors.as_data())

        return render(request, 'build/data/Table_Creation.html', context_dict)
        
    else:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')

def edit_table_field_information(request, pk_bundle, pk_data, pk_table):
    bundle = Bundle.objects.get(pk=pk_bundle)
    data = Data.objects.get(pk=pk_data)


    # Secure ELSA by seeing if the user logged in is the same user associated with the Bundle
    if request.user == bundle.user:
        print('authorized user: {}'.format(request.user))

        # Get forms
        form_edit_table_field_information = EditTableFieldsForm(request.POST or None, pk_data=pk_data, pk_table=pk_table)
        if data.data_type == 'Table Delimited':
            table = Table_Delimited.objects.get(pk=pk_table)
        elif data.data_type == 'Table Binary':
            table = Table_Binary.objects.get(pk=pk_table)
        elif data.data_type == 'Table Character':
            table = Table_Fixed_Width.objects.get(pk=pk_table)

        context_dict = {
            'form_edit_table_field_information': form_edit_table_field_information,
            'bundle': bundle,
        }

        if form_edit_table_field_information.is_valid():
            cleaned_form = form_edit_table_field_information.cleaned_data

            label_list = open_label_with_tree(table.label())
            label_root = label_list[1]

            label_root = table.fill_label_values(label_root, cleaned_form)

            # Close appropriate label(s)
            print(' ... Closing Label ... ')
            close_label(table.label(), label_root, label_list[2])

            # all_labels = []

            # product_bundle = Product_Bundle.objects.get(bundle=bundle)
            # product_collections_list = Product_Collection.objects.filter(bundle=bundle).exclude(collection='Data')

            # all_labels.append(product_bundle)
            # all_labels.extend(product_collections_list)

            # # fix this for filling table values
            # for label in all_labels:
            # # Open appropriate label(s).  
            #     print('- Label: {}'.format(label))
            #     print(' ... Opening Label ... ')
            #     label_list = open_label_with_tree(label.label())
            #     label_root = label_list[1]
            #     print(' ... Building Label ... ')
            #     label_root = table.fill_label_values(label_root, cleaned_form)

            #     # Close appropriate label(s)
            #     print(' ... Closing Label ... ')
            #     close_label(label.label(), label_root, label_list[2])

            return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/')

            # return render(request, 'build/citation_information/citation_information_current.html', context_dict)
        return render(request, 'build/data/fields.html', context_dict)
        
    else:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')

'''
def Table_Creation(request, pk_bundle):

    bundle = Bundle.objects.get(pk=pk_bundle)

    if request.user == bundle.user:


        #Count the number of tables of each type the bundle has. 
        #There's almost certainly a better way to do this, but this was faster and more reliable
        #than dreging the django API.
        TD_iterator = 0
        TB_iterator = 0
        TFW_iterator = 0

        tableTypes = Data_Prep.objects.filter(bundle=bundle)

        for table in tableTypes:
            if table.data_type == "Table Delimited":
                TD_iterator = TD_iterator + 1
            if table.data_type == "Table Binary":
                TB_iterator = TB_iterator + 1
            if table.data_type == "Table Fixed-Width":
                TFW_iterator = TFW_iterator + 1

        #Create the formsets for each table.
        TableDelimitedFormSet = modelformset_factory(Table_Delimited, exclude=('bundle',) , extra=TD_iterator, max_num=TD_iterator)


        TableBinaryFormSet = modelformset_factory(Table_Binary, exclude=('bundle',) , extra=TB_iterator, max_num=TB_iterator)


        TableFixedWidthFormSet = modelformset_factory(Table_Fixed_Width, exclude=('bundle',) , extra=TFW_iterator, max_num=TFW_iterator)


        if request.method == 'POST':
            TD_formset = TableDelimitedFormSet(request.POST, queryset=Table_Delimited.objects.filter(bundle=bundle), prefix='delimited')
            TB_formset = TableBinaryFormSet(request.POST, queryset=Table_Binary.objects.filter(bundle=bundle), prefix='binary')
            TFW_formset = TableFixedWidthFormSet(request.POST, queryset=Table_Fixed_Width.objects.filter(bundle=bundle), prefix='character')
        else:
            TD_formset = TableDelimitedFormSet(queryset=Table_Delimited.objects.filter(bundle=bundle),prefix='delimited')
            TB_formset = TableBinaryFormSet(queryset=Table_Binary.objects.filter(bundle=bundle),prefix='binary')
            TFW_formset = TableFixedWidthFormSet(queryset=Table_Fixed_Width.objects.filter(bundle=bundle),prefix='character')


        context_dict = {
            'bundle':bundle,
            'TableDelimitedFormSet':TableDelimitedFormSet,
            'TD_formset':TD_formset,
            'TD_iterator':TD_iterator,
            'TableBinaryFormSet':TableBinaryFormSet,
            'TB_formset':TB_formset,
            'TB_iterator':TB_iterator,
            'TableFixedWidthFormSet':TableFixedWidthFormSet,
            'TFW_formset':TFW_formset,
            'TFW_iterator':TFW_iterator,
        }



        #id_iterator = 0
        if request.method == 'POST':
            #Create and fill the database fields as well as (eventually) filling the data files
            #print smart_str(TD_iterator) + " " + smart_str(TD_formset.errors)
            if TD_formset.is_valid() and request.method == 'POST':
                id_iterator = 0
                    for TD_form in TD_formset:
                    if TD_form.is_valid():
                        print TD_form.data
                              table_id = TD_form.data['delimited-'+smart_str(id_iterator)+'-id'] #Get Table object id
                            print table_id
                        print TD_form.data
                             table = Table_Delimited.objects.get(id=table_id) #Get the actual Table object using the id from the previous step
                            name = table.name #Get the Tables name (set in data_prep) using the object from the previous step
                            table_form = TD_form.save()
                           table_form.name = name #Manually fill the form name
                            table_form.save()
                            id_iterator = id_iterator+1


            #print smart_str(TB_iterator) + " " + smart_str(TB_formset.errors)
            if TB_formset.is_valid() and request.method == 'POST':
                     id_iterator = 0
                    for TB_form in TB_formset:
                    if TB_form.is_valid():
                        print TB_form.data
                            table_id = TB_form.data['binary-'+smart_str(id_iterator)+'-id']
                            print table_id

                            table = Table_Binary.objects.get(id=table_id)
                            name = table.name
                            table_form = TB_form.save(commit=False)
                            table_form.name = name
                            table_form.save()
                            id_iterator = id_iterator+1


            #print smart_str(TFW_iterator) + " " + smart_str(TFW_formset.errors)
            if TFW_formset.is_valid() and request.method == 'POST':
                    id_iterator = 0
                    for TFW_form in TFW_formset:
                    if TFW_form.is_valid():
                             table_id = TFW_form.data['character-'+smart_str(id_iterator)+'-id']
                            print table_id
                        print TFW_form.data
                            table = Table_Fixed_Width.objects.get(id=table_id)
                            name = table.name
                            table_form = TFW_form.save(commit=False)
                            table_form.name = name
                            table_form.save()
                            id_iterator = id_iterator+1


        

        return render(request, 'build/data/table_creation.html', context_dict)
    else:
        print 'unauthorized user attempting to access a restricted area.'
        return redirect('main:restricted_access')
'''

# Field Creation takes a table reference and a table type in order to get the properly associated Table Object


def Field_Creation(request, pk_bundle, pk_table, table_type):

    bundle = Bundle.objects.get(pk=pk_bundle)

    if table_type == "Table Delimited":
        table = Table_Delimited.objects.get(pk=pk_table)
    elif table_type == "Table Binary":
        table = Table_Binary.objects.get(pk=pk_table)
    elif table_type == "Table Fixed Width":
        table = Table_Fixed_Width.objects.get(pk=pk_table)
    else:
        raise Exception(table_type + "is an unknown table type")
        return

    if request.user == bundle.user:
        pass
    else:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')


"""
    Context Views
"""


@login_required
def context(request):
    context_dict = {
    }

    return render(request, 'context/repository/repository.html', context_dict)


# @login_required
def investigations(request):
    context_dict = {
        'investigations': Investigation.objects.all(),
    }

    return render(request, 'context/repository/investigations.html', context_dict)


# @login_required
def instrument_hosts(request):
    context_dict = {
        'instrument_hosts': Instrument_Host.objects.all(),
    }

    return render(request, 'context/repository/instrument_hosts.html', context_dict)

# @login_required


def instruments(request):
    context_dict = {
        'instruments': Instrument.objects.all(),
    }

    return render(request, 'context/repository/instruments.html', context_dict)

# delete functions for context products
def delete_target(request, pk_bundle, pk_target):
    bundle = Bundle.objects.get(pk=pk_bundle)
    target = Target.objects.get(pk=pk_target)

    product_bundle = Product_Bundle.objects.get(bundle=bundle)
    product_collections_list = bundle_label_targets(bundle)

    remove_from_label(target, product_bundle, product_collections_list)

    bundle.targets.remove(target)
    mirror_targets_into_products(bundle)
    # target.delete()

    # I'm not convinced this does what I want it to do
    return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/')
    # return HttpResponseRedirect('/build/' + pk_bundle + '/')
    # return HttpResponseRedirect('/elsa/build/' + pk_bundle)
    # return redirect('../../bundle/')

def delete_modification_history(request, pk_bundle, pk_modification_history):
    bundle = Bundle.objects.get(pk=pk_bundle)
    modification_history = Modification_History.objects.get(pk=pk_modification_history)

    product_bundle = Product_Bundle.objects.get(bundle=bundle)
    product_collections_list = bundle_label_targets(bundle)

    remove_from_label(modification_history, product_bundle, product_collections_list)
    bundle.modification_history.remove(modification_history)

    return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/')

def delete_citation_information(request, pk_bundle, pk_citation_information):
    bundle = Bundle.objects.get(pk=pk_bundle)
    citation_information = Citation_Information.objects.get(pk=pk_citation_information)

    product_bundle = Product_Bundle.objects.get(bundle=bundle)
    product_collections_list = bundle_label_targets(bundle)

    remove_from_label(citation_information, product_bundle, product_collections_list)
    # Remove it from the data labels too, so they match the bundle.
    mirror_citation_into_data_products(bundle)
    bundle.citation_information.remove(citation_information)

    return HttpResponseRedirect(reverse('build:citation_information', args=[pk_bundle]))


def delete_instrument(request, pk_bundle, pk_instrument):
    bundle = Bundle.objects.get(pk=pk_bundle)
    instrument = Instrument.objects.get(pk=pk_instrument)

    instrument_host = instrument.instrument_hosts.first()
    investigation = instrument.investigations.first()

    print(instrument_host)

    product_bundle = Product_Bundle.objects.get(bundle=bundle)
    product_collections_list = bundle_label_targets(bundle)

    remove_from_label(instrument, product_bundle, product_collections_list)

    bundle.instruments.remove(instrument)
    # instrument.delete()

    # Back to the bundle page.
    if walkthrough.returning_to_bundle(request):
        return redirect(walkthrough.bundle_url(bundle))

    # Need to add to redirect to ask for a new instrument from
    # re_path(r'^(?P<pk_bundle>\d+)/contextsearch/investigation/(?P<pk_investigation>\d+)/instrument_host/(?P<pk_instrument_host>\d+)/instrument/$', views.context_search_instrument, name='context_search_instrument')

    # I'm not convinced this does what I want it to do
    # return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/contextsearch/investigation/' + str(investigation.pk) + '/instrument_host/' + str(instrument_host.pk) + '/instrument/')
    if instrument_host:
        return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/contextsearch/investigation/' + str(investigation.pk) + '/instrument_host/' + str(instrument_host.pk) + '/instrument/')
    else:
        return HttpResponseRedirect('/elsa/build/' + pk_bundle)
    # return redirect('../../bundle/')

def delete_instrument_host(request, pk_bundle, pk_instrument_host):
    bundle = Bundle.objects.get(pk=pk_bundle)
    instrument_host = Instrument_Host.objects.get(pk=pk_instrument_host)

    # instruments = instrument_host.instruments.all()
    investigation = instrument_host.investigations.first()

    product_bundle = Product_Bundle.objects.get(bundle=bundle)
    product_collections_list = bundle_label_targets(bundle)

    for bundle_instrument in bundle.instruments.all():
        if bundle_instrument in instrument_host.instruments.all():
            remove_from_label(bundle_instrument, product_bundle, product_collections_list)
            bundle.instruments.remove(bundle_instrument)

    remove_from_label(instrument_host, product_bundle, product_collections_list)

    bundle.instrument_hosts.remove(instrument_host)

    # Back to the bundle page.
    if walkthrough.returning_to_bundle(request):
        return redirect(walkthrough.bundle_url(bundle))

    if Instrument_Host.objects.filter(investigations=investigation.pk).count() == 1:
        # have screen to choose between deleting investigation or choosing new host
        # return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/contextsearch/investigation/' + str(investigation.pk) + '/instrument_host_or_facility/')
        return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/contextsearch/investigation/' + str(investigation.pk) + '/instrument_host_or_facility/')
    else: 
        # Have user select new host and probably create new html
        # return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/contextsearch/investigation/' + str(investigation.pk) + '/instrument_host_or_facility/')
        return HttpResponseRedirect('/elsa/build/' + pk_bundle)

def delete_facility(request, pk_bundle, pk_facility):
    bundle = Bundle.objects.get(pk=pk_bundle)
    facility = Facility.objects.get(pk=pk_facility)

    # instruments = instrument_host.instruments.all()
    investigation = facility.investigations.first()

    product_bundle = Product_Bundle.objects.get(bundle=bundle)
    product_collections_list = bundle_label_targets(bundle)

    for bundle_instrument in bundle.instruments.all():
        if bundle_instrument in facility.instruments.all():
            remove_from_label(bundle_instrument, product_bundle, product_collections_list)
            bundle.instruments.remove(bundle_instrument)

    remove_from_label(facility, product_bundle, product_collections_list)

    bundle.facilities.remove(facility)

    # Back to the bundle page.
    if walkthrough.returning_to_bundle(request):
        return redirect(walkthrough.bundle_url(bundle))

    if Facility.objects.filter(investigations=investigation.pk).count() == 1:
        # have screen to choose between deleting investigation or choosing new host
        # return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/contextsearch/investigation/' + str(investigation.pk) + '/instrument_host_or_facility/')
        return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/contextsearch/investigation/' + str(investigation.pk) + '/instrument_host_or_facility/')
    else:
        return HttpResponseRedirect('/elsa/build/' + pk_bundle)

def delete_investigation(request, pk_bundle, pk_investigation):
    bundle = Bundle.objects.get(pk=pk_bundle)
    investigation = Investigation.objects.get(pk=pk_investigation)

    product_bundle = Product_Bundle.objects.get(bundle=bundle)
    product_collections_list = bundle_label_targets(bundle)

    for bundle_instrument_host in bundle.instrument_hosts.all():
        if bundle_instrument_host in investigation.instrument_hosts.all():
            #remove attached instruments to found instrument host
            for bundle_instrument in bundle.instruments.all():
                if bundle_instrument in bundle_instrument_host.instruments.all():
                    remove_from_label(bundle_instrument, product_bundle, product_collections_list)
                    bundle.instruments.remove(bundle_instrument)

            #remove instrument host
            remove_from_label(bundle_instrument_host, product_bundle, product_collections_list)
            bundle.instrument_hosts.remove(bundle_instrument_host)

    remove_from_label(investigation, product_bundle, product_collections_list)

    bundle.investigations.remove(investigation)

    # have screen to choose between deleting investigation or choosing new host
    # return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/contextsearch/investigation/')
    return HttpResponseRedirect('/elsa/build/' + pk_bundle)

@login_required
def delete_citation_information(request, pk_bundle, pk_citation_information):

    print(' \n\n \n\n-------------------------------------------------------------------------')
    print('\n\n---------------------- Remove an Citation Information with ELSA ---------------------------')
    print('------------------------------ DEBUGGER ---------------------------------')

    bundle = Bundle.objects.get(pk=pk_bundle)

    if request.user == bundle.user:
        print("authorized user")

        citation_information = Citation_Information.objects.get(pk=pk_citation_information)

        product_bundle = Product_Bundle.objects.get(bundle=bundle)
        product_collections_list = bundle_label_targets(bundle)

        remove_from_label(citation_information, product_bundle, product_collections_list)
        # Remove it from the data labels too, so they match the bundle.
        mirror_citation_into_data_products(bundle)

        citation_information.delete()

        return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/')

    else:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')
    
@login_required
def delete_modification_history(request, pk_bundle, pk_modification_history):

    print(' \n\n \n\n-------------------------------------------------------------------------')
    print('\n\n---------------------- Remove an Citation Information with ELSA ---------------------------')
    print('------------------------------ DEBUGGER ---------------------------------')

    bundle = Bundle.objects.get(pk=pk_bundle)

    if request.user == bundle.user:
        print("authorized user")

        modification_history = Modification_History.objects.get(pk=pk_modification_history)

        product_bundle = Product_Bundle.objects.get(bundle=bundle)
        product_collections_list = bundle_label_targets(bundle)

        remove_from_label(modification_history, product_bundle, product_collections_list)

        modification_history.delete()

        return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/')

    else:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')

def delete_instrument(request, pk_bundle, pk_instrument):
    bundle = Bundle.objects.get(pk=pk_bundle)
    instrument = Instrument.objects.get(pk=pk_instrument)

    instrument_host = instrument.instrument_hosts.first()
    investigation = instrument.investigations.first()

    print(instrument_host)

    product_bundle = Product_Bundle.objects.get(bundle=bundle)
    product_collections_list = bundle_label_targets(bundle)

    remove_from_label(instrument, product_bundle, product_collections_list)

    bundle.instruments.remove(instrument)
    # instrument.delete()

    # Back to the bundle page.
    if walkthrough.returning_to_bundle(request):
        return redirect(walkthrough.bundle_url(bundle))

    # Need to add to redirect to ask for a new instrument from
    # re_path(r'^(?P<pk_bundle>\d+)/contextsearch/investigation/(?P<pk_investigation>\d+)/instrument_host/(?P<pk_instrument_host>\d+)/instrument/$', views.context_search_instrument, name='context_search_instrument')

    # I'm not convinced this does what I want it to do
    # return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/contextsearch/investigation/' + str(investigation.pk) + '/instrument_host/' + str(instrument_host.pk) + '/instrument/')
    if instrument_host:
        return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/contextsearch/investigation/' + str(investigation.pk) + '/instrument_host/' + str(instrument_host.pk) + '/instrument/')
    else:
        return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/contextsearch/')
    # return redirect('../../bundle/')

def delete_instrument_host(request, pk_bundle, pk_instrument_host):
    bundle = Bundle.objects.get(pk=pk_bundle)
    instrument_host = Instrument_Host.objects.get(pk=pk_instrument_host)

    # instruments = instrument_host.instruments.all()
    investigation = instrument_host.investigations.first()

    product_bundle = Product_Bundle.objects.get(bundle=bundle)
    product_collections_list = bundle_label_targets(bundle)

    for bundle_instrument in bundle.instruments.all():
        if bundle_instrument in instrument_host.instruments.all():
            remove_from_label(bundle_instrument, product_bundle, product_collections_list)
            bundle.instruments.remove(bundle_instrument)

    remove_from_label(instrument_host, product_bundle, product_collections_list)

    bundle.instrument_hosts.remove(instrument_host)

    # Back to the bundle page.
    if walkthrough.returning_to_bundle(request):
        return redirect(walkthrough.bundle_url(bundle))

    if Instrument_Host.objects.filter(investigations=investigation.pk).count() == 1:
        # have screen to choose between deleting investigation or choosing new host
        # return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/contextsearch/investigation/' + str(investigation.pk) + '/instrument_host_or_facility/')
        return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/contextsearch/investigation/' + str(investigation.pk) + '/instrument_host_or_facility/')
    else: 
        # Have user select new host and probably create new html
        # return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/contextsearch/investigation/' + str(investigation.pk) + '/instrument_host_or_facility/')
        return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/contextsearch/investigation/' + str(investigation.pk) + '/instrument_host_or_facility/')

def delete_facility(request, pk_bundle, pk_facility):
    bundle = Bundle.objects.get(pk=pk_bundle)
    facility = Facility.objects.get(pk=pk_facility)

    # instruments = instrument_host.instruments.all()
    investigation = facility.investigations.first()

    product_bundle = Product_Bundle.objects.get(bundle=bundle)
    product_collections_list = bundle_label_targets(bundle)

    for bundle_instrument in bundle.instruments.all():
        if bundle_instrument in facility.instruments.all():
            remove_from_label(bundle_instrument, product_bundle, product_collections_list)
            bundle.instruments.remove(bundle_instrument)

    remove_from_label(facility, product_bundle, product_collections_list)

    bundle.facilities.remove(facility)

    # Back to the bundle page.
    if walkthrough.returning_to_bundle(request):
        return redirect(walkthrough.bundle_url(bundle))

    if Facility.objects.filter(investigations=investigation.pk).count() == 1:
        # have screen to choose between deleting investigation or choosing new host
        # return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/contextsearch/investigation/' + str(investigation.pk) + '/instrument_host_or_facility/')
        return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/contextsearch/investigation/' + str(investigation.pk) + '/instrument_host_or_facility/')
    else:
        return HttpResponseRedirect('/elsa/build/' + pk_bundle + '/contextsearch/')

def delete_investigation(request, pk_bundle, pk_investigation):
    bundle = Bundle.objects.get(pk=pk_bundle)
    investigation = Investigation.objects.get(pk=pk_investigation)

    product_bundle = Product_Bundle.objects.get(bundle=bundle)
    product_collections_list = bundle_label_targets(bundle)

    for bundle_instrument_host in bundle.instrument_hosts.all():
        if bundle_instrument_host in investigation.instrument_hosts.all():
            #remove attached instruments to found instrument host
            for bundle_instrument in bundle.instruments.all():
                if bundle_instrument in bundle_instrument_host.instruments.all():
                    remove_from_label(bundle_instrument, product_bundle, product_collections_list)
                    bundle.instruments.remove(bundle_instrument)

            #remove instrument host
            remove_from_label(bundle_instrument_host, product_bundle, product_collections_list)
            bundle.instrument_hosts.remove(bundle_instrument_host)

    remove_from_label(investigation, product_bundle, product_collections_list)

    bundle.investigations.remove(investigation)

    # Back to the bundle page, same as delete_target.
    return redirect('build:bundle', pk_bundle=pk_bundle)

# Directory View
def _get_abs_virtual_root():
    return _eventual_path(settings.BASE_DIR)

def _eventual_path(path):
    return os.path.abspath(os.path.realpath(path))

# NETCDF (Victoria's Code)
import xarray as xr
import pandas as pd
import xml.etree.ElementTree as ET
from xml.dom import minidom

# to search for files in working directory
import os
import glob

# to extract information from online ldd
import urllib.request

# --- Define Functions ---
# Load and parse the AMA LDD to get the permitted Coordinate elements and their order.
# Cached for an hour so we don't hit the network on every upload. Short timeout in case PDS is down.
def _fetch_ldd_content(ldd_url: str) -> bytes:
    cache_key = 'ldd_schema:' + ldd_url
    xml_content = cache.get(cache_key)
    if xml_content is None:
        with urllib.request.urlopen(ldd_url, timeout=15) as response:
            xml_content = response.read()
        cache.set(cache_key, xml_content, 60 * 60)
    return xml_content


def get_allowed_coord_fields_from_ldd_url(ldd_url: str, ns: dict) -> list:
    xml_content = _fetch_ldd_content(ldd_url)

    root = ET.fromstring(xml_content)

    # Locate the ama:Variable complexType definition
    coord_type = root.find(".//xs:complexType[@name='Coordinate']/xs:sequence", namespaces=ns)
    if coord_type is None:
        raise ValueError("Could not find Coordinate definition in LDD from URL.")

    return [
        elem.attrib["name"]
        for elem in coord_type.findall("xs:element", namespaces=ns) ]

# Function to Load & Parse the AMA LDD so that we know
# what Variable Elements are permitted and in what order they are expected
def get_allowed_variable_fields_from_ldd_url(ldd_url: str, ns: dict) -> list:
    xml_content = _fetch_ldd_content(ldd_url)

    root = ET.fromstring(xml_content)

    # Locate the ama:Variable complexType definition
    variable_type = root.find(".//xs:complexType[@name='Variable']/xs:sequence", namespaces=ns)
    if variable_type is None:
        raise ValueError("Could not find Variable definition in LDD from URL.")

    return [
        elem.attrib["name"]
        for elem in variable_type.findall("xs:element", namespaces=ns)
    ]


# Function to convert DataFrame to XML variable elements
def dataframe_to_variable_elements(variable_metadata: pd.DataFrame, NS: dict, allowed_fields: list) -> list:
    variable_elements = []

    # Map normalized metadata field → original metadata field
    metadata_fields_normalized = {
        normalize(field): field for field in variable_metadata.index
    }

    for variable_name in variable_metadata.columns:
        variable_elem = ET.Element(f"{{{NS['ama']}}}Variable")

        # Always add variable_name
        name_elem = ET.SubElement(variable_elem, f"{{{NS['ama']}}}variable_name")
        name_elem.text = variable_name

        # Iterate in the order from the LDD
        for field in allowed_fields:
            if field == "variable_name":
                continue  # already added

            # normalize name of allowed fields (e.g. lowercase, remove "_")
            norm_field = normalize(field)

            # check if allowed normalized fields exist in the extracted variable fields
            if norm_field in metadata_fields_normalized:
                original_metadata_field = metadata_fields_normalized[norm_field]
                val = variable_metadata.loc[original_metadata_field, variable_name]

                # pandas gives NaN when this variable doesn't have the field. All of these are optional
                # in the AMA dictionary, so skip it instead of writing "nan".
                if pd.isna(val) or str(val).strip().lower() == "nan":
                    continue

                # replace empty strings for units with "[]"
                if field == "units" and str(val).strip() == "":
                    val = "[]"

                # Skip other empty attributes too. WRF sets stagger="" and PDS rejects empty elements.
                if str(val).strip() == "":
                    continue

                # Place in appropriate allowed field (not normalized version since just using field)
                sub_elem = ET.SubElement(variable_elem, f"{{{NS['ama']}}}{field}")
                sub_elem.text = str(val)

        variable_elements.append(variable_elem)

    return variable_elements

# Function to convert DataFrame to XML coordinate elements
def dataframe_to_coord_elements(coord_metadata: pd.DataFrame, NS: dict, allowed_fields: list) -> list:
    coord_elements = []

    # Map normalized metadata field → original metadata field
    metadata_fields_normalized = {
        normalize(field): field for field in coord_metadata.index
    }

    for coord_name in coord_metadata.columns:
        coord_elem = ET.Element(f"{{{NS['ama']}}}Coordinate")

        # Always add variable_name
        name_elem = ET.SubElement(coord_elem, f"{{{NS['ama']}}}coord_name")
        name_elem.text = coord_name

        # Iterate in the order from the LDD
        for field in allowed_fields:
            if field == "coord_name":
                continue  # already added

            # normalize name of allowed fields (e.g. lowercase, remove "_")
            norm_field = normalize(field)

            # check if allowed normalized fields exist in the extracted coordinate fields
            if norm_field in metadata_fields_normalized:
                original_metadata_field = metadata_fields_normalized[norm_field]
                val = coord_metadata.loc[original_metadata_field, coord_name]

                # pandas gives NaN when this variable doesn't have the field. All of these are optional
                # in the AMA dictionary, so skip it instead of writing "nan".
                if pd.isna(val) or str(val).strip().lower() == "nan":
                    continue

                # replace empty strings for units with "[]"
                if field == "units" and str(val).strip() == "":
                    val = "[]"

                # Skip other empty attributes too. WRF sets stagger="" and PDS rejects empty elements.
                if str(val).strip() == "":
                    continue

                # Place in appropriate allowed field (not normalized version since just using field)
                sub_elem = ET.SubElement(coord_elem, f"{{{NS['ama']}}}{field}")
                sub_elem.text = str(val)

        coord_elements.append(coord_elem)

    return coord_elements

# Normalize function: Set all variable and coordinate fields to lowercase and remove underscores
def normalize(field):
    return field.lower().replace("_", "")


# --- AMA user-supplied classes (Model_Metadata, Simulation_Configuration, File_Description) ---
# The file only gives us ama:Variable and ama:Coordinate. The rest comes from the forms.
AMA_LDD_URL = 'https://pds.nasa.gov/pds4/ama/v1/PDS4_AMA_1O00_1300.xsd'


def _ama_namespaces():
    """Namespace map used for every AMA label operation, with prefixes registered for output."""
    NS = {
        'xs': 'http://www.w3.org/2001/XMLSchema',
        'pds': 'http://pds.nasa.gov/pds4/pds/v1',
        'xsi': 'http://www.w3.org/2001/XMLSchema-instance',
        'ama': 'http://pds.nasa.gov/pds4/ama/v1',
    }
    ET.register_namespace('ama', NS['ama'])  # Needed to preserve ama prefix in output
    # Register the PDS namespace with the empty prefix so elements don't come out as <pds:...>.
    ET.register_namespace('', NS['pds'])
    return NS


def _label_prolog(template_path):
    """The XML declaration and xml-model lines above the root element.

    ElementTree drops these on parse. validate needs them to find the schematrons, otherwise it
    skips every schematron rule. Copied from the template.
    """
    prolog = ['<?xml version="1.0" encoding="UTF-8"?>']

    with open(template_path, encoding='utf-8') as template:
        for line in template:
            stripped = line.strip()
            if stripped.startswith('<?xml-model'):
                prolog.append(stripped)
            elif stripped.startswith('<') and not stripped.startswith('<?'):
                # Reached the root element; there is nothing above it left to copy.
                break

    return '\n'.join(prolog) + '\n'


def _fill_ama_container(container, values, NS, unit_attributes=None):
    """Replace a container's children with the non-blank values, in LDD order.

    Every AMA attribute has minLength="1", so a blank field gets no element.
    """
    if container is None:
        return

    for child in list(container):
        container.remove(child)
    container.text = None

    unit_attributes = unit_attributes or {}
    for element_name, text in values.items():
        sub_elem = ET.SubElement(container, '{{{}}}{}'.format(NS['ama'], element_name))
        sub_elem.text = text
        if element_name in unit_attributes:
            # The compass boundaries declare unit as use="required" in the LDD.
            sub_elem.set('unit', unit_attributes[element_name])


def write_ama_user_classes(root, NS, bundle, netcdf_obj=None):
    """Write Model_Metadata, Simulation_Configuration and File_Description into a label.

    Each uses the file's override if it has one, otherwise the collection default.
    The containers stay even when empty, the LDD has them as minOccurs="1" inside ama:AMA.
    """
    ama = root.find('.//pds:Context_Area/pds:Discipline_Area/ama:AMA', namespaces=NS)
    if ama is None:
        print('write_ama_user_classes: no <ama:AMA> in label, nothing to fill.')
        return

    resolved = {}
    for model_class, _form_class, prefix, _label in AMA_SECTIONS:
        resolved[prefix] = model_class.resolve_for_file(netcdf_obj)

    _fill_ama_container(
        ama.find('ama:Model_Metadata', namespaces=NS),
        resolved[AMA_MODEL_METADATA_PREFIX].filled_values()
        if resolved[AMA_MODEL_METADATA_PREFIX] else {},
        NS)

    _fill_ama_container(
        ama.find('ama:Simulation_Configuration', namespaces=NS),
        resolved[AMA_SIMULATION_PREFIX].filled_values()
        if resolved[AMA_SIMULATION_PREFIX] else {},
        NS,
        unit_attributes=SimulationConfiguration.BOUNDARY_UNIT_ATTRIBUTE)

    # File_Description has to stay first in Model_Output, so fill it in place.
    _fill_ama_container(
        ama.find('ama:Model_Output/ama:File_Description', namespaces=NS),
        resolved[AMA_FILE_DESCRIPTION_PREFIX].filled_values()
        if resolved[AMA_FILE_DESCRIPTION_PREFIX] else {},
        NS)


def regenerate_netcdf_labels(bundle, netcdf_objs=None):
    """Rebuild the label for NetCDF files already uploaded.

    Starts from the template each time, so re-running can't double up the logical_identifier.
    Files not on disk are skipped. Returns a list of error strings, empty means all good.
    """
    NS = _ama_namespaces()

    if netcdf_objs is None:
        netcdf_objs = NetCDFFile.objects.filter(bundle=bundle)
    netcdf_objs = list(netcdf_objs)
    if not netcdf_objs:
        return []

    try:
        allowed_variable_fields = get_allowed_variable_fields_from_ldd_url(AMA_LDD_URL, NS)
        allowed_coord_fields = get_allowed_coord_fields_from_ldd_url(AMA_LDD_URL, NS)
    except Exception as e:
        # The values are already saved, so a PDS outage doesn't lose them. Next rebuild picks them up.
        print('regenerate_netcdf_labels: could not load LDD: {}'.format(e))
        return ['Could not reach the PDS data dictionary, so labels were not refreshed: {}'.format(e)]

    os.environ['HDF5_USE_FILE_LOCKING'] = 'FALSE'

    errors = []
    for nc_obj in netcdf_objs:
        try:
            if not nc_obj.file:
                continue
            # NetCDF and label are in the collection directory. directory() falls back to the bundle root for older rows.
            collection_directory = nc_obj.directory()
            nc_path = os.path.join(collection_directory, os.path.basename(nc_obj.file.name))
            if not os.path.exists(nc_path):
                print('regenerate_netcdf_labels: skipping {}, not found at {}.'.format(
                    nc_obj.title, collection_directory))
                continue

            _process_single_netcdf(
                bundle, nc_path, collection_directory, NS, allowed_variable_fields,
                allowed_coord_fields, netcdf_obj=nc_obj)

            nc_obj.processed = True
            nc_obj.processing_error = ''
            nc_obj.save(update_fields=['processed', 'processing_error'])
        except Exception as e:
            print('regenerate_netcdf_labels: error on "{}": {}'.format(nc_obj.title, e))
            nc_obj.processed = False
            nc_obj.processing_error = str(e)
            nc_obj.save(update_fields=['processed', 'processing_error'])
            errors.append('{}: {}'.format(nc_obj.title, e))

    # Rewrite the inventories. Only files with a good label get listed.
    for additional_collection in AdditionalCollections.objects.filter(bundle=bundle):
        additional_collection.build_inventory()

    return errors


def variable_coord_to_product(bundle, netcdf_objs, collection_directory):
    """Extract metadata from each uploaded NetCDF and generate its label.

    Only handles the NetCDFFile objects passed in, and updates `processed` / `processing_error`.
    Returns a list of error strings, empty means all good.
    """
    # --- Set Up XML Namespace ---
    NS = _ama_namespaces()

    # --- Load Online Local Data Dictionary & Extract Permitted Variable/Coordinate Fields (cached for an hour) ---
    allowed_variable_fields = get_allowed_variable_fields_from_ldd_url(AMA_LDD_URL, NS)
    allowed_coord_fields = get_allowed_coord_fields_from_ldd_url(AMA_LDD_URL, NS)

    os.environ['HDF5_USE_FILE_LOCKING'] = 'FALSE'

    errors = []
    for nc_obj in netcdf_objs:
        try:
            nc_path = nc_obj.file.path
            if not os.path.exists(nc_path):
                raise FileNotFoundError('Uploaded file is missing from disk.')

            # shutil.move, not os.rename, since uploads and the archive can be on different filesystems.
            destination = os.path.join(collection_directory, os.path.basename(nc_obj.file.path))
            shutil.move(nc_path, destination)
            nc_path = destination
            print(nc_path)

            _process_single_netcdf(
                bundle, nc_path, collection_directory, NS, allowed_variable_fields, allowed_coord_fields,
                netcdf_obj=nc_obj)

            nc_obj.processed = True
            nc_obj.processing_error = ''
            nc_obj.save(update_fields=['processed', 'processing_error'])
        except Exception as e:
            print('Error processing NetCDF "{}": {}'.format(nc_obj.title, e))
            nc_obj.processed = False
            nc_obj.processing_error = str(e)
            nc_obj.save(update_fields=['processed', 'processing_error'])
            errors.append('{}: {}'.format(nc_obj.title, e))

    # Rewrite the inventories. Only files with a good label get listed.
    for additional_collection in AdditionalCollections.objects.filter(bundle=bundle):
        additional_collection.build_inventory()

    return errors


def _process_single_netcdf(bundle, nc_path, collection_directory, NS, allowed_variable_fields, allowed_coord_fields,
                           netcdf_obj=None):
    """Generate the label for one NetCDF file. Raises on failure.

    Pass `netcdf_obj` so per-file AMA overrides get used.
    """
    # Output file name. Uploads may have no .nc extension, so only strip a trailing .nc and add .xml.
    base = os.path.basename(nc_path)
    nc_filename = base  # e.g., 00000.atmos_average.nc OR 00000.atmos_average
    nc_name = base[:-3] if base.endswith('.nc') else base  # e.g., 00000.atmos_average
    xml_name = nc_name + '.xml'
    subdir_name = os.path.basename(os.path.dirname(nc_path))  # e.g., Simulation02

    # Write XML to the bundle's archive directory
    output_dir = collection_directory
    output_path = os.path.join(output_dir, xml_name)

    # --- 1. Open NetCDF File and Extract Variable & Coordinate Metadata into a Pandas Table ---
    DS = xr.open_dataset(nc_path, decode_times=False, engine='netcdf4')

    # variable metadata
    metadata = {}
    for var_name, var in DS.variables.items():

        metadata[var_name] = var.attrs

        # Add dimensions to the metadata dictionary
        metadata[var_name]['dimensions'] = ', '.join(var.dims)

    # Convert metadata to a DataFrame
    variable_metadata = pd.DataFrame(metadata)


    # now coordinate metadata
    # Get additional "Derived" metadata (e.g. boundaries, grid spacing, etc)
    metadata = {}

    # Iterate over coordinates
    for coord_name, coord in DS.coords.items():

        # Initialize a dictionary for the coordinate's metadata
        coord_metadata = {}

        # Add the minimum_boundary key to the coordinate's metadata
        coord_metadata['minimum_boundary'] = DS[coord_name].min().item()
        coord_metadata['maximum_boundary'] = DS[coord_name].max().item()

        # Check if coordinate is an array (if yes, find spacing assuming spacing is the same)
        #if len(coord) > 1:
        #    coord_metadata['grid_spacing'] = DS[coord_name][1].values - DS[coord_name][0].values
        #else:
        #    coord_metadata['grid_spacing'] = None

        # Check if coordinate has units attribute
        if 'units' in coord.attrs:
            coord_metadata['units'] = coord.attrs['units']
        else:
            coord_metadata['units'] = None

        # Add the coordinate's metadata to the metadata dictionary
        metadata[coord_name] = coord_metadata

    # Create a DataFrame from the metadata dictionary
    coord_metadata= pd.DataFrame(metadata)

    # --- 2. Load & Parse Template XML ---
    source_file = os.path.join(PDS4_LABEL_TEMPLATE_DIRECTORY, 'base_templates')
    source_file = os.path.join(source_file, 'Template_PE.xml') # May have to change to Template_PE_document.xml
    tree = ET.parse(source_file)
    root = tree.getroot()



    # --- 3. Locate <Identification_Area> and Insert <pds:logical_identifier>, <pds:title> ---
    # Find the Identification_Area
    id_area = root.find(".//pds:Identification_Area", namespaces=NS)

    if id_area is not None:

        # Build the identifier from the bundle, not the template, which says sample_bundle.
        # Same format as AdditionalCollections.member_lidvids() so the inventory matches.
        # Needs the namespace prefix or find() misses the element and we'd add a second one.
        # Use pds_lid_segment, not .lower(), since names can have characters PDS4 doesn't allow in a LID.
        product_lid = "{}:{}:{}".format(bundle.lid(),
                                        pds_lid_segment(subdir_name),
                                        pds_lid_segment(nc_filename))
        lid_elem = id_area.find("pds:logical_identifier", namespaces=NS)
        if lid_elem is None:
            lid_elem = ET.SubElement(id_area, f"{{{NS['pds']}}}logical_identifier")
        lid_elem.text = product_lid

        # Find the <pds:title> Element and Populate
        title_elem = id_area.find("pds:title", namespaces=NS)
        if title_elem is None:
            title_elem = ET.SubElement(id_area, f"{{{NS['pds']}}}title")
        title_elem.text = f"{nc_name}"

    # --- 4. Locate <ama:Model_Output> inside <Discipline_Area>/<ama:AMA> ---
    # These are in the default (pds) namespace, so they need the prefix too.
    model_output = root.find(
        ".//pds:Context_Area/pds:Discipline_Area/ama:AMA/ama:Model_Output",
        namespaces=NS
    )

    if model_output is None:
        print("Could not find <ama:Model_Output> under <ama:AMA> in template.")
        raise ValueError("Could not find <ama:Model_Output> under <ama:AMA> in template.")

    # --- 5. Insert New Variable & Coordinate Metadata ---
    variable_elements = dataframe_to_variable_elements(variable_metadata, NS, allowed_variable_fields)

    for elem in variable_elements:
        model_output.append(elem)

    coord_elements = dataframe_to_coord_elements(coord_metadata, NS, allowed_coord_fields)

    for elem in coord_elements:
        model_output.append(elem)

    # --- 5b. Fill the AMA classes the NetCDF cannot supply (from the AMA forms) ---
    # After the Variable/Coordinate append so File_Description stays first in Model_Output.
    write_ama_user_classes(root, NS, bundle, netcdf_obj=netcdf_obj)

    # --- 6. Fill <File_Area_External> with the Actual Uploaded File's Info ---
    file_elem = root.find(".//pds:File_Area_External/pds:File", namespaces=NS)
    if file_elem is not None:
        file_name_elem = file_elem.find("pds:file_name", namespaces=NS)
        if file_name_elem is not None:
            file_name_elem.text = nc_filename
        creation_elem = file_elem.find("pds:creation_date_time", namespaces=NS)
        if creation_elem is not None:
            creation_elem.text = datetime.datetime.utcfromtimestamp(
                os.path.getmtime(nc_path)).strftime('%Y-%m-%dT%H:%MZ')
        size_elem = file_elem.find("pds:file_size", namespaces=NS)
        if size_elem is not None:
            size_elem.text = str(os.path.getsize(nc_path))

    encoded_name_elem = root.find(".//pds:File_Area_External/pds:Encoded_External/pds:name", namespaces=NS)
    if encoded_name_elem is not None:
        encoded_name_elem.text = nc_name

    # --- 7. Write to Output File ---
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(_label_prolog(source_file))
        f.write(ET.tostring(root, encoding='unicode'))

    update = Version()

    # Stamp the PDS4 version into the label. Source has to be output_path, not the template,
    # since version_update_old copies its source over its destination.
    update.version_update_old(bundle.version, output_path, output_path)


    alias_set = Alias.objects.filter(bundle=bundle)
    citation_information_set = Citation_Information.objects.filter(bundle=bundle)
    modification_history_set = Modification_History.objects.filter(bundle=bundle)

    products = []
    products.extend(bundle.investigations.all())
    products.extend(bundle.instrument_hosts.all())
    products.extend(bundle.facilities.all())
    products.extend(bundle.instruments.all())
    products.extend(bundle.telescopes.all())
    products.extend(bundle.targets.all())

    for citation_information in citation_information_set:
        products.append(citation_information)
    for alias in alias_set:
        products.append(alias)
    for modification_history in modification_history_set:
        products.append(modification_history)

    print(products)

    for product in products:
        # print('- Label: {}'.format(label))
        print(' ... Opening Label ... ')
        label_list = open_label_with_tree(output_path)
        label_root = label_list[1]
        print(label_root)
        print(' ... Building Label ... ')
        label_root = product.fill_label(label_root)
        #alias.alias_list.append(label_root)

        # Close appropriate label(s)
        print(' ... Closing Label ... ')
        close_label(output_path, label_root, label_list[2])

    # Temporary remove netcdf file before figuring out how to move it
    # os.remove(nc_path)
    print('netcdf_path:' + nc_path, flush=True)



@login_required
def delete_collection(request, pk_bundle, pk_collection):
    print('\n\n')
    print('-------------------------------------------------------------------------')
    print('\n\n------------------ Delete Collection with ELSA -------------------')
    print('------------------------------ DEBUGGER ---------------------------------')
    
    bundle = Bundle.objects.get(pk=pk_bundle)

    if request.user == bundle.user:
        print('authorized user')

        collection_to_delete = get_object_or_404(AdditionalCollections, pk=pk_collection)

        # Grab the XML file path BEFORE deleting the record
        try:
            xml_path = collection_to_delete.label()
            print('XML path to delete: {}'.format(xml_path))
        except Exception as e:
            xml_path = None
            print('Could not get label path: {}'.format(e))

        # Grab the archive directory path BEFORE deleting the record
        # Structure: ARCHIVE_DIR / username / bundle.directory() / collection_name /
        try:
            archive_dir = os.path.join(
                settings.ARCHIVE_DIR,
                bundle.user.username,
                bundle.directory(),
                collection_to_delete.collection_name
            )
            print('Archive directory to delete: {}'.format(archive_dir))
        except Exception as e:
            archive_dir = None
            print('Could not get archive directory path: {}'.format(e))

        # Remove from label
        try:
            product_bundle = Product_Bundle.objects.get(bundle=bundle)
            product_collections_list = Product_Collection.objects.filter(bundle=bundle).exclude(collection='Data')
            remove_from_label(collection_to_delete, product_bundle, product_collections_list)
        except Exception as e:
            print('Note: Could not remove from label or not applicable. Error: {}'.format(e))

        # Delete the XML file from disk
        if xml_path:
            if os.path.exists(xml_path):
                os.remove(xml_path)
                print('Deleted XML file: {}'.format(xml_path))
            else:
                print('XML file not found at path: {}'.format(xml_path))

        # Delete the archive directory and all its contents
        if archive_dir:
            if os.path.exists(archive_dir):
                shutil.rmtree(archive_dir)
                print('Deleted archive directory: {}'.format(archive_dir))
            else:
                print('Archive directory not found at path: {}'.format(archive_dir))

        # Delete the collection from the database
        collection_to_delete.delete()

        return HttpResponseRedirect('/elsa/build/' + str(pk_bundle) + '/')

    else:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')


@login_required
def submit_feedback(request):
    if request.method != 'POST':
        return JsonResponse({'success': False}, status=405)

    category = request.POST.get('category', '').strip()
    bundle_type = request.POST.get('bundle_type', '').strip()
    description = request.POST.get('description', '').strip()
    user_email = request.POST.get('user_email', '').strip()

    if not category or not description:
        return JsonResponse({'success': False, 'error': 'Category and description are required.'}, status=400)

    submitted_at = localtime(timezone.now()).strftime('%B %d, %Y at %I:%M %p %Z')
    subject = f'[ELSA Beta Feedback] {category} — {request.user.username}'
    body = f"""
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background-color:#f4f4f4;font-family:Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f4f4;padding:32px 0;">
    <tr>
      <td align="center">
        <table width="600" cellpadding="0" cellspacing="0" style="background-color:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08);">

          <!-- Header -->
          <tr>
            <td style="background-color:#2F4F4F;padding:24px 32px;">
              <p style="margin:0;color:#ffffff;font-size:11px;letter-spacing:1px;text-transform:uppercase;">ELSA Beta Feedback</p>
              <h1 style="margin:6px 0 0;color:#ffffff;font-size:22px;">{category}</h1>
            </td>
          </tr>

          <!-- Details -->
          <tr>
            <td style="padding:28px 32px 8px;">
              <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td style="padding:8px 0;border-bottom:1px solid #eeeeee;">
                    <span style="font-size:11px;color:#888888;text-transform:uppercase;letter-spacing:0.5px;">Submitted by</span><br>
                    <span style="font-size:15px;color:#222222;">{request.user.username}</span>
                  </td>
                </tr>
                <tr>
                  <td style="padding:8px 0;border-bottom:1px solid #eeeeee;">
                    <span style="font-size:11px;color:#888888;text-transform:uppercase;letter-spacing:0.5px;">Reply-to email</span><br>
                    <span style="font-size:15px;color:#222222;">{user_email if user_email else '<em style="color:#aaaaaa;">not provided</em>'}</span>
                  </td>
                </tr>
                <tr>
                  <td style="padding:8px 0;border-bottom:1px solid #eeeeee;">
                    <span style="font-size:11px;color:#888888;text-transform:uppercase;letter-spacing:0.5px;">Context</span><br>
                    <span style="font-size:15px;color:#222222;">{bundle_type if bundle_type else 'Not specified'}</span>
                  </td>
                </tr>
                <tr>
                  <td style="padding:8px 0;border-bottom:1px solid #eeeeee;">
                    <span style="font-size:11px;color:#888888;text-transform:uppercase;letter-spacing:0.5px;">Submitted</span><br>
                    <span style="font-size:15px;color:#222222;">{submitted_at}</span>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Description -->
          <tr>
            <td style="padding:8px 32px 32px;">
              <p style="margin:16px 0 8px;font-size:11px;color:#888888;text-transform:uppercase;letter-spacing:0.5px;">Description</p>
              <div style="background-color:#f8f8f8;border-left:4px solid #2F4F4F;border-radius:4px;padding:16px 20px;font-size:15px;color:#333333;line-height:1.6;white-space:pre-wrap;">{description}</div>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="background-color:#f8f8f8;padding:16px 32px;border-top:1px solid #eeeeee;">
              <p style="margin:0;font-size:12px;color:#aaaaaa;">This message was sent automatically by ELSA &mdash; Educational Labeling System @Atmospheres, NMSU.</p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""
    email = EmailMessage(
        subject=subject,
        body=body,
        from_email='atm-elsa@nmsu.edu',
        to=['lneakras@nmsu.edu', 'rupakdey@nmsu.edu'],
        #to=['rupakdey@nmsu.edu'], #for testing only
        reply_to=[user_email] if user_email else [],
    )
    email.content_subtype = 'html'
    email.send(fail_silently=True)

    return JsonResponse({'success': True})

        


# --- PDS validation ---


@login_required
def start_validation(request, pk_bundle):
    """Queue a validation run and report its state. POST only.

    If one is already running, that one is returned (see validate_runner.start).
    """
    bundle = get_object_or_404(Bundle, pk=pk_bundle)

    if request.user != bundle.user:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')

    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    tier = request.POST.get('tier', ValidationRun.TIER_STRUCTURE)
    if tier not in dict(ValidationRun.TIER_CHOICES):
        tier = ValidationRun.TIER_STRUCTURE

    validation_run = validate_runner.start(bundle, tier)
    return JsonResponse(_validation_state(validation_run))


@login_required
def validation_status(request, pk_bundle):
    """Current state of this bundle's latest validation, for polling. No side effects.

    Gives a 404 for a deleted bundle, which is normal if the panel was left open.
    """
    bundle = get_object_or_404(Bundle, pk=pk_bundle)

    if request.user != bundle.user:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')

    tier = request.GET.get('tier') or None
    validation_run = validate_runner.latest_run_for(bundle, tier)

    # Whether Review & Submit should start a check: never checked, or changed since.
    needs = validate_runner.needs_check(bundle)

    if validation_run is None:
        return JsonResponse({'status': 'none', 'needs_check': needs})

    state = _validation_state(validation_run)
    state['needs_check'] = needs
    return JsonResponse(state)


@login_required
def validation_panel(request, pk_bundle):
    """The panel's contents, rendered fresh for the page to swap in.

    Don't reload the page instead. A reload in the middle of a NetCDF upload kills the upload.
    """
    bundle = get_object_or_404(Bundle, pk=pk_bundle)

    if request.user != bundle.user:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')

    context = validation_context(bundle, request.user)
    context['bundle'] = bundle
    # Also has the Review & Submit verdict band and button, which use the same result.
    context['status_dict'] = _submission_status(bundle)
    return render(request, 'build/validation/fragments.html', context)


def _validation_state(validation_run):
    """What a polling client needs. Findings are summarised since this runs every two seconds."""
    return {
        'id': validation_run.pk,
        'status': validation_run.status,
        'status_label': validation_run.get_status_display(),
        'tier': validation_run.tier,
        'phase': validation_run.phase,
        'phase_label': validation_run.phase_label(),
        'percent': validation_run.percent_complete(),
        'products_done': validation_run.products_done,
        'products_total': validation_run.products_total,
        'errors': validation_run.error_count,
        'warnings': validation_run.warning_count,
        # Translated count, same as the Bundle Components badge. The raw error count is much larger.
        # ELSA's own requirements are counted too, since PDS can't see a missing target.
        'blocking': validate_rules.summarise(
            validation_run.findings or [],
            preflight.requirements(validation_run.bundle)
            + validate_rules.unresolved_items(validation_run.findings, validation_run.bundle)
        )['blocking'],
        'finished': validation_run.status in (
            ValidationRun.STATUS_DONE, ValidationRun.STATUS_FAILED),
        # A finished run whose bundle has since changed, so the client can say it's out of date.
        'stale': validation_run.is_stale(),
        'failure_reason': validation_run.failure_reason,
        # Refused because the server is at its limit. Not saved, so no id. The page tries again.
        'busy': validation_run.pk is None,
    }


@login_required
def validation_report(request, pk_run):
    """Staff view of one run: every finding, grouped, with the raw report."""
    if not request.user.is_staff:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')

    validation_run = get_object_or_404(
        ValidationRun.objects.select_related('bundle'), pk=pk_run)
    findings = validation_run.findings or []

    context_dict = {
        'run': validation_run,
        'bundle': validation_run.bundle,
        'errors': validate_report.errors(findings),
        'warnings': validate_report.warnings(findings),
        'by_location': sorted(
            validate_report.group_by_location(findings).items(),
            key=lambda item: (-len(item[1]), item[0])),
        'by_type': sorted(
            validate_report.summarise_types(findings).items(),
            key=lambda item: (-item[1], item[0])),
    }
    return render(request, 'build/validation/report.html', context_dict)


@login_required
def validation_runs(request):
    """Staff list of recent runs across all bundles."""
    if not request.user.is_staff:
        print('unauthorized user attempting to access a restricted area.')
        return redirect('main:restricted_access')

    runs = ValidationRun.objects.select_related('bundle', 'bundle__user')[:100]

    # Across all stored runs, to see which message types come up the most.
    type_counts = {}
    for validation_run in ValidationRun.objects.exclude(findings=None):
        for finding_type, count in validate_report.summarise_types(
                validation_run.findings or []).items():
            type_counts[finding_type] = type_counts.get(finding_type, 0) + count

    context_dict = {
        'runs': runs,
        'by_type': sorted(type_counts.items(), key=lambda item: (-item[1], item[0])),
    }
    return render(request, 'build/validation/runs.html', context_dict)
