# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.http import HttpResponse
from build.forms import *
from build.models import *

# Create your views here.
@login_required
def index(request):
    return render(request, 'tutorial/index.html', {})

@login_required
def build_a_bundle(request):
    return render(request, 'tutorial/introduction_build.html', {})

@login_required
def bundle_and_collections(request):
    print 'DEBUG START --------------------'
    form_bundle = BundleForm(request.POST or None)
    form_collections = CollectionsForm(request.POST or None)
    context_dict = {
        'form_bundle':form_bundle,
        'form_collections':form_collections,
    }

    if form_bundle.is_valid() and form_collections.is_valid():
        print 'all forms valid'

        # Check Uniqueness
        bundle_name = form_bundle.cleaned_data['name']
        bundle_user = request.user
        bundle_count = Bundle.objects.filter(name=bundle_name, user=bundle_user).count()
        # If user and bundle name are unique, then...
        if bundle_count == 0:

            # Build Bundle model object and begin PDS4 Compliant Bundle directory in User Directory.
            # This Bundle Directory will contain a Product_Bundle label.
            bundle = build.Bundle(request, form_bundle)
            
            # Build Collections model object and continue with PDS4 compliant Bundle directory in User 
            # directory.  Bundle directory will contain a collection folder for each collection and a
            # Product_Collection label.            
            build.Collections(request, form_collections, bundle)
            
            # Further develop context_dict entries for templates            
            context_dict['Bundle'] = bundle
            context_dict['Product_Bundle'] = Product_Bundle.objects.get(bundle=bundle)
            context_dict['Product_Collection_Set'] = Product_Collection.objects.filter(bundle=bundle)

            return HttpResponse("Yeah, we're getting here")

    return render(request, 'tutorial/bundle_and_collections.html', context_dict)


@login_required
def collection_context(request, pk_bundle):
    context_dict = {}
    return render(request, 'tutorial/collection_context.html', context_dict)

@login_required
def collection_data(request, pk_bundle):
    context_dict = {}
    return render(request, 'tutorial/collection_data.html', context_dict)


@login_required
def collection_document(request, pk_bundle):
    context_dict = {}
    return render(request, 'tutorial/collection_document.html', context_dict)



def new(request):
    return HttpResponse("Begin Build Tutorial")
