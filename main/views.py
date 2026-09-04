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
from __future__ import print_function

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.conf import settings
from django.core.mail import EmailMessage
from django.shortcuts import render, redirect
from django.template.loader import get_template
from django.http import HttpResponseRedirect, HttpResponse
from .models import Joke
from .forms import *
from django.forms import modelformset_factory
from django.urls import reverse
from django.contrib import messages

# from .forms import ContactForm, UserContactForm, UploadedDocumentForm # I'm not sure uploaded document form should be here (k).
import random
import os
import logging  # This document logs errors and is currently not in use in ELSA.  Feel free to develop this (k).
from datetime import date, datetime

import re
import requests
from django.core.cache import cache

#logger = loggin.getLogger(__name__)


# index is the home page for elsa.
def index(request):
    from friends.views import _read_passkey_hint

    contact_form = ContactForm(request.POST or None)
    context_dict = {
        'contact_form': contact_form,
        # Only mention passkeys on a browser where one has actually been used.
        # Everywhere else the note would promise something that will not happen.
        'browser_has_passkey': bool(_read_passkey_hint(request)['known']),
    }
    return render(request, 'main/index.html', context_dict)

# contact_from_login is the view that allows users to contact elsa from the login page.
def contact_from_login(request):
    contact_form = ContactForm(request.POST or None)
    context_dict = {
        'contact_form': contact_form,
        'email_sent': False,
    }
    template = get_template('main/contact_template.txt')

    if request.method == 'POST' and contact_form.is_valid():
        context_dict['name'] = contact_form.cleaned_data['name']
        context_dict['email'] = contact_form.cleaned_data['email']
        context_dict['agency'] = contact_form.cleaned_data['agency']
        context_dict['message'] = contact_form.cleaned_data['message']

        content = template.render(context_dict)

        email = EmailMessage(
            subject="{} is contacting ELSA".format(context_dict['name']),
            body=content,
            from_email='atm-elsa@nmsu.edu',
            to = ['sajomont@nmsu.edu', 'pds-atm@nmsu.edu', 'rupakdey@nmsu.edu'],
            headers={'Reply-To': context_dict['email']}
        )

        confirmation = EmailMessage(
            subject="Thank you for contacting ELSA!",
            body="Your message has been received. Please allow 24–48 hours for a response.\n\nRegards,\nTeam ELSA",
            from_email='atm-elsa@nmsu.edu',
            to=[context_dict['email']]
        )

        email.send()
        confirmation.send()

        return HttpResponseRedirect(reverse('main:index'))  # redirect back to login page

    return render(request, 'main/index.html', context_dict)

# GitHub Releases is the canonical record of what shipped when. It used to be README.md, parsed
# out of the markdown, but the notes lived in two places at once and drifted: a release would be
# published while the README still ended at the version before it. The notes are now written once,
# where they are published, and both the About page and the assistant read them from here.
GITHUB_RELEASES_API = (
    'https://api.github.com/repos/atmospheresnode/ELSA-sven/releases?per_page=6')


def format_release_bullets(body):
    """Turn a release body's markdown bullets into the strings about.html renders.

    Unchanged from when these were parsed out of README.md: a release body is the same
    "- Area: what changed." list, so the area name is still bolded up to the first colon.
    """
    bullets = []
    for line in re.findall(r'^-\s+(.+)$', body or '', re.MULTILINE):
        if ':' in line:
            area, rest = line.split(':', 1)
            bullets.append('<strong>{}:</strong>{}'.format(area, rest))
        else:
            bullets.append(line)
    return bullets


def parse_releases(payload):
    """Shape GitHub's /releases JSON into what the About page and the assistant expect.

    The date comes from `created_at`, never `published_at`. GitHub documents created_at as the
    date of the commit the release was cut from, while published_at is when the release was
    created on GitHub. The 34 releases backfilled in September 2026 all carry that one afternoon
    as their published_at, so using it would date two years of history to a single day.
    """
    releases = []
    for item in payload:
        if item.get('draft') or item.get('prerelease'):
            continue
        try:
            stamp = datetime.strptime(item['created_at'], '%Y-%m-%dT%H:%M:%SZ')
            released_on = stamp.strftime('%B %d, %Y')
        except (KeyError, TypeError, ValueError):
            # A release with an unreadable timestamp is still worth showing; only its date is lost.
            stamp, released_on = None, ''
        releases.append({
            'version': (item.get('tag_name') or '').lstrip('v'),
            'date': released_on,
            'bullets': format_release_bullets(item.get('body')),
            '_sort': stamp or datetime.min,
        })

    # GitHub returns these newest first already. Sorting the page we were given costs nothing and
    # means the About page does not depend on that staying true.
    releases.sort(key=lambda release: release['_sort'], reverse=True)
    for release in releases:
        del release['_sort']
    return releases


def fetch_releases():
    """Fetch and parse the published releases, or raise.

    Shared by the About page and the assistant's background refresh so the URL, the date handling
    and the bullet formatting have exactly one definition. The repository is public, so this is
    unauthenticated: the limit is 60 requests an hour per IP and both callers cache for at least
    an hour, which puts normal use around one request in sixty.
    """
    response = requests.get(
        GITHUB_RELEASES_API,
        timeout=5,
        headers={'Accept': 'application/vnd.github+json'})
    response.raise_for_status()
    return parse_releases(response.json())


def about(request):
    releases = cache.get('elsa_release_notes')

    if not releases:
        try:
            releases = fetch_releases()
            cache.set('elsa_release_notes', releases, 60 * 60)  # cache for 1 hour
            print('Releases fetched and parsed successfully. {} versions found.'.format(len(releases)))
        except Exception as e:
            releases = None
            print('Could not fetch releases: {}'.format(e))

    current = releases[0] if releases else None
    previous = releases[1:6] if releases else []

    return render(request, 'main/about.html', {
        'current_release': current,
        'previous_releases': previous,
    })

# contact provides a means for users to contact atmos through contact cards and direct email to elsa@nmsu.edu.
def contact(request):
    contact_form = ContactForm(request.POST or None)
    user_contact_form = UserContactForm(request.POST or None)
    context_dict = { 
        'contact_form': contact_form,
        'user_contact_form': user_contact_form,
        'email_sent': False,
        'user_logged_in': False
    }
    template = get_template('main/contact_template.txt')
    print('text')

    if user_contact_form.is_valid():
        print('user contact form is not valid')
           
    if request.user.is_authenticated:
        context_dict['user_logged_in'] = True
        if user_contact_form.is_valid():
            print('user_contact_form is valid')
           
            context_dict['name'] = '{0}, {1}'.format(request.user.last_name, request.user.first_name)
            context_dict['email'] = request.user.email
            # context_dict['agency'] = request.user.userprofile.agency
            context_dict['message'] = user_contact_form.cleaned_data['message']
            content = template.render(context_dict)

            #Email to ELSA from user
            email = EmailMessage(
                subject = "{} is contacting ELSA".format(context_dict['name']),
                body = content,
                from_email = 'atm-elsa@nmsu.edu',
                to = ['sajomont@nmsu.edu', 'pds-atm@nmsu.edu', 'rupakdey@nmsu.edu'],
                headers = {'Reply-To': 'atm-elsa@nmsu.edu' }
            )

            #Email confirmation to user
            email_confirmation = EmailMessage(
                subject = "Thank you for contacting ELSA!",
                body = "Your message has been received. Please allow 24-48 hours to receive a response. Thank you for using ELSA! \n\nRegards,\nTeam ELSA",
                from_email = 'atm-elsa@nmsu.edu',
                to = [context_dict['email']]
            )

            email.send()
            email_confirmation.send()
            
            context_dict['email_sent'] = True
            return HttpResponseRedirect('/contact') # redirects to the same page to clear the form after submission

            
            

    #     #else:
    #      #   logger.error('{}: user_contact_form is not valid'.format(date.today()))

    # else:
    #     if contact_form.is_valid():
    #         print('contact_form is valid')

    #         # Email the profile with the contact information
    #         context_dict['name'] = contact_form.cleaned_data['name']
    #         # context_dict['email'] = contact_form.cleaned_data['email']
    #         context_dict['email'] = 'atm-elsa@nmsu.edu'
    #         context_dict['agency'] = contact_form.cleaned_data['agency']
    #         context_dict['message'] = contact_form.cleaned_data['message']
    #         content = template.render(context_dict)
    #         email = EmailMessage(
    #             subject = "{} is contacting ELSA".format(context_dict['name']),
    #             body = content,
    #             # from_email = context_dict['email'],
    #             from_email = 'atm-elsa@nmsu.edu',
    #             # to = ['elsa@atmos.nmsu.edu',],
    #             to = ['sajomont@nmsu.edu'],
    #             # headers = {'Reply-To': context_dict['email'] }
    #         )
    #         email.send()
    #         context_dict['email_sent'] = True

        #else:
            #logger.error('{}: contact_form is not valid.'.format(datetime.now()))

    return render(request, 'main/contact.html', context_dict)

#Context Products Contact Form
def context_products_contact(request):
    context_products_contact = ContextProductsContactForm(request.POST or None)
    user_contact_form = UserContactForm(request.POST or None)
    context_dict = {}
    context_dict['contact_form'] = context_products_contact
    context_dict['context_products_contact'] = context_products_contact
    context_dict['user_contact_form'] = user_contact_form
    context_dict['email_sent'] = False
    context_dict['user_logged_in'] = False
    template = get_template('main/contact_template.txt')
    print('text')

    if user_contact_form.is_valid():
        print('user contact form is valid')
           
    if request.user.is_authenticated:
        context_dict['user_logged_in'] = True
        if user_contact_form.is_valid():
            print('user_contact_form is valid')
           
            context_dict['name'] = '{0}, {1}'.format(request.user.last_name, request.user.first_name)
            context_dict['email'] = request.user.email
            context_dict['message'] = user_contact_form.cleaned_data['message']
            content = template.render(context_dict)
            print('before email')

            #Email to ELSA from user
            email = EmailMessage(
                subject = "{} is contacting ELSA".format(context_dict['name']),
                body = content,
                from_email = 'atm-elsa@nmsu.edu',
                to = ['sajomont@nmsu.edu', 'pds-atm@nmsu.edu', 'rupakdey@nmsu.edu'],
                headers = {'Reply-To': 'atm-elsa@nmsu.edu' }
            )

            #Email confirmation to user
            email_confirmation = EmailMessage(
                subject = "Thank you for contacting ELSA!",
                body = "Your message has been received. Please allow 24-48 hours to receive a response. Thank you for using ELSA! \n\nRegards,\nTeam ELSA",
                from_email = 'atm-elsa@nmsu.edu',
                to = [context_dict['email']]
            )

            email.send()
            print('email sent')
            email_confirmation.send()
            print('email confirmation sent')
            
           # context_dict['email_sent'] = True
            messages.success(request, "✅ Your message was successfully sent!")
            return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/')) # redirects to the same page to clear the form after submission

            
            

    #     #else:
    #      #   logger.error('{}: user_contact_form is not valid'.format(date.today()))

    # else:
    #     if contact_form.is_valid():
    #         print('contact_form is valid')

    #         # Email the profile with the contact information
    #         context_dict['name'] = contact_form.cleaned_data['name']
    #         # context_dict['email'] = contact_form.cleaned_data['email']
    #         context_dict['email'] = 'atm-elsa@nmsu.edu'
    #         context_dict['agency'] = contact_form.cleaned_data['agency']
    #         context_dict['message'] = contact_form.cleaned_data['message']
    #         content = template.render(context_dict)
    #         email = EmailMessage(
    #             subject = "{} is contacting ELSA".format(context_dict['name']),
    #             body = content,
    #             # from_email = context_dict['email'],
    #             from_email = 'atm-elsa@nmsu.edu',
    #             # to = ['elsa@atmos.nmsu.edu',],
    #             to = ['sajomont@nmsu.edu'],
    #             # headers = {'Reply-To': context_dict['email'] }
    #         )
    #         email.send()
    #         context_dict['email_sent'] = True

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




