"""
K. Sweebe

elsa.main.views contains all of the views responsible for the main core of elsa.  This includes elsa's homepage, contact information for elsa, a service's page that details all of the various apps in elsa, and other views listed below.  

To decide if a view belongs in elsa.main.views, ask yourself one of two questions:
	1. Does the view pertain specifically to elsa and not to another app?
	2. Is the view one that could be used in multiple apps?

If you said yes to either, then the view belongs in elsa.main.views.

"""






# -*- coding: utf-8 -*-
from __future__ import unicode_literals


from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.conf import settings
from django.core.mail import EmailMessage
from django.shortcuts import render
from django.template.loader import get_template
from .models import Joke
from .forms import ContactForm, UserContactForm, UploadedDocumentForm # I'm not sure uploaded document form should be here (k).
import random
import os
import logging  # This document logs errors and is currently not in use in ELSA.  Feel free to develop this (k).
from datetime import date

#logger = loggin.getLogger(__name__)



# Create your views here.

# index is the home page for elsa.
def index(request):
    return render(request, 'main/index.html', {})

# about describes elsa's purpose, goal, etc.
def about(request):
    return render(request, 'main/about.html', {})

# contact provides a means for users to contact atmos through contact cards and direct email to elsa@nmsu.edu.
def contact(request):
    contact_form = ContactForm(request.POST or None)
    user_contact_form = UserContactForm(request.POST or None)
    context_dict = {}
    context_dict['contact_form'] = contact_form
    context_dict['user_contact_form'] = user_contact_form
    context_dict['email_sent'] = False
    context_dict['user_logged_in'] = False
    template = get_template('main/contact_template.txt')
           
    if request.user.is_authenticated():
        context_dict['user_logged_in'] = True
        if user_contact_form.is_valid():
            print 'user_contact_form is valid'
           
            context_dict['name'] = '{0}, {1}'.format(request.user.last_name, request.user.first_name)
            context_dict['email'] = request.user.email
            context_dict['agency'] = request.user.userprofile.agency
            context_dict['message'] = user_contact_form.cleaned_data['message']
            content = template.render(context_dict)
            email = EmailMessage(
                subject = "{} is contacting ELSA".format(context_dict['name']),
                body = content,
                from_email = context_dict['email'],
                to = ['elsa@atmos.nmsu.edu',],
                headers = {'Reply-To': context_dict['email'] }
            )
            email.send()
            context_dict['email_sent'] = True

        #else:
         #   logger.error('{}: user_contact_form is not valid'.format(date.today()))

    else:
        if contact_form.is_valid():
            print 'contact_form is valid'

            # Email the profile with the contact information
            context_dict['name'] = contact_form.cleaned_data['name']
            context_dict['email'] = contact_form.cleaned_data['email']
            context_dict['agency'] = contact_form.cleaned_data['agency']
            context_dict['message'] = contact_form.cleaned_data['message']
            content = template.render(context_dict)
            email = EmailMessage(
                subject = "{} is contacting ELSA".format(context_dict['name']),
                body = content,
                from_email = context_dict['email'],
                to = ['elsa@atmos.nmsu.edu',],
                headers = {'Reply-To': context_dict['email'] }
            )
            email.send()
            context_dict['email_sent'] = True

        #else:
            #logger.error('{}: contact_form is not valid.'.format(datetime.now()))

    return render(request, 'main/contact.html', context_dict)

# restricted_access is the page that displays if a user is travelling to an area they have no business being in.
@login_required
def restricted_access(request):
    return render(request, 'main/restricted_access.html', {})


# services displays the various apps encompassed by elsa.
@login_required
def services(request):
    return render(request, 'main/services.html', {})


# this is a simple_upload view used to upload a document.  I have no idea why this is here other than that I used it to upload a document at some point in time.  I should really comment more (k).
@login_required
def simple_upload(request):
    context_dict = {}
    if request.method =='POST':
        form = UploadedDocumentForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
    else:
        form = UploadedDocumentForm()
    context_dict['form'] = form
    return render(request, 'main/simple_upload.html', context_dict)


# anytime a page is under construction, we use this view.
@login_required
def construction(request):
    return render(request, 'main/construction.html', {})


# ------ TEST VIEWS -------

def error(request):
    random_index = random.randint(0, Joke.objects.count()-1)
    random_joke = Joke.objects.all()[random_index]
    return render(request, 'main/error.html', {'random_joke':random_joke})


def success(request):
    return render(request, 'main/success.html')








