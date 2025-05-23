# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

from django.contrib.auth.decorators import login_required
from django.core.mail import EmailMessage
from django.core.mail import send_mail
from django.shortcuts import render, redirect, HttpResponseRedirect
from django.template import Context
from django.template.loader import get_template
from .forms import ReviewForm, UserInfoForm




# Create your views here.


def index(request):

    request.encoding = 'utf-8'

    # Forms
    review_form = ReviewForm(request.POST or None)
    user_info_form = UserInfoForm(request.POST or None)

    # Context Dictionary
    context_dict = {}
    context_dict['review_form'] = review_form
    context_dict['user_info_form'] = user_info_form
    context_dict['email_sent'] = False

    
    if review_form.is_valid():
        print('is valid')

        # Find template used for email
        template = get_template('review/comment_template.txt')

        # if a user is logged in, we can simply grab their info for them
        # if request.user.is_authenticated():
        #     context_dict['contact_name'] = '{0}, {1}'.format(request.user.last_name, request.user.first_name)
        #     context_dict['contact_email'] = request.user.email
        #     context_dict['agency'] = request.user.userprofile.agency

        # # else a seperate form is displayed for them to fill in the information.
        # else:


        #     context_dict['contact_name'] = '{0}'.format(review_form.cleaned_data['user_name'])
        #     context_dict['contact_email'] = review_form.cleaned_data['user_email']
        #     context_dict['agency'] = 'User was not logged in to retrieve extra information.'

        context_dict['contact_name'] = '{0}'.format(review_form.cleaned_data['user_name'])
        context_dict['contact_email'] = review_form.cleaned_data['user_email']
        #context_dict['agency'] = 'User was not logged in to retrieve extra information.'

        # Rest of the information not dependent upon user model is listed below
        context_dict['derived_data'] = review_form.cleaned_data['derived_data']
#        context_dict['reviewer'] = review_form.cleaned_data['reviewer']
        context_dict['question1'] = review_form.cleaned_data['question1']
        context_dict['question2'] = review_form.cleaned_data['question2']
        context_dict['question3'] = review_form.cleaned_data['question3']
        context_dict['question4'] = review_form.cleaned_data['question4']

        # Render template with the context_dict values
        content = template.render(context_dict)

        # Email the form to elsa@atmos.nmsu.edu
        email = EmailMessage(
            subject = "Derived Data Peer Review from {}".format(context_dict['contact_name']),
            body = content,
            from_email = 'atm-elsa@nmsu.edu',
            # to = ['lneakras@nmsu.edu', 'lhuber@nmsu.edu'],
            #to =['lneakras@nmsu.edu', 'sajomont@nmsu.edu', 'rupakdey@nmsu.edu'],
            to = ['rupakdey@nmsu.edu'],
            headers = {'Reply-To': context_dict['contact_email'] }
        )

        # Email confirmation to user
        email_confirmation = EmailMessage(
            subject = "Thank you for submitting a Derived Data Peer Review!",
            body = "Your review for '{}' data set has been received. Your review copy is included for your record: \n {} \n Thank you for using ELSA! \n\nRegards,\nTeam ELSA".format(context_dict['derived_data'], content),
            from_email = 'atm-elsa@nmsu.edu',
            to = [context_dict['contact_email']]
        )

        print('before')
        # send_mail("Derived Data Peer Review from {}".format(context_dict['contact_name']),
        #           content,
        #           'atm-elsa@nmsu.edu',
        #           ['sajomont@nmsu.edu'],
        #           fail_silently=False)
        
        email.send()
        email_confirmation.send()
        print('after')
        context_dict['email_sent'] = True
        return render(request, 'review/index.html', context_dict)

    return render(request, 'review/index.html', context_dict)
