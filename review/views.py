# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

from django.core.mail import EmailMessage
from django.shortcuts import render
from django.template.loader import get_template
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .forms import ReviewForm, UserInfoForm
from .models import ReviewDraft

import json
import uuid
import traceback

# Index view: It handles both GET and POST requests, processes the review form, and sends emails
def index(request):
    request.encoding = 'utf-8'

    draft_id = request.COOKIES.get('draft_id')
    initial_data = {}

    # Try to load draft data if draft_id cookie exists
    if draft_id:
        try:
            draft_uuid = uuid.UUID(draft_id)
            draft = ReviewDraft.objects.get(draft_id=draft_uuid)
            if draft.content:
                initial_data = json.loads(draft.content)
        except (ValueError, ReviewDraft.DoesNotExist, json.JSONDecodeError):
            initial_data = {}

    # Pass initial data as 'initial' to pre-fill form
    review_form = ReviewForm(request.POST or None, initial=initial_data)
    user_info_form = UserInfoForm(request.POST or None)

    context_dict = {
        'review_form': review_form,
        'user_info_form': user_info_form,
        'email_sent': False,
        'draft_id': draft_id or '',
    }

    if review_form.is_valid():
        context_dict['contact_name'] = '{0}'.format(review_form.cleaned_data['user_name'])
        context_dict['contact_email'] = review_form.cleaned_data['user_email']
        context_dict['derived_data'] = review_form.cleaned_data['derived_data']
        context_dict['question1'] = review_form.cleaned_data['question1']
        context_dict['question2'] = review_form.cleaned_data['question2']
        context_dict['question3'] = review_form.cleaned_data['question3']
        context_dict['question4'] = review_form.cleaned_data['question4']

        # Find template used for email
        template = get_template('review/comment_template.txt')
        content = template.render(context_dict)

        email = EmailMessage(
<<<<<<< HEAD
            subject = "Derived Data Peer Review from {}".format(context_dict['contact_name']),
            body = content,
            from_email = 'atm-elsa@nmsu.edu',
            # to = ['lneakras@nmsu.edu', 'lhuber@nmsu.edu'],
            to =['lneakras@nmsu.edu', 'sajomont@nmsu.edu', 'rupakdey@nmsu.edu'],
            # to = ['rupakdey@nmsu.edu'],
            headers = {'Reply-To': context_dict['contact_email'] }
=======
            subject="Derived Data Peer Review from {}".format(context_dict['contact_name']),
            body=content,
            from_email='atm-elsa@nmsu.edu',
            # t
            to=['rupakdey@nmsu.edu'],
            headers={'Reply-To': context_dict['contact_email']}
>>>>>>> 081daf2a55bf910b7679f384564d1fbcac8972f4
        )

        email_confirmation = EmailMessage(
            subject="Thank you for submitting a Derived Data Peer Review!",
            body="Your review for '{}' data set has been received. Your review copy is included for your record:\n{}\nThank you for using ELSA!\n\nRegards,\nTeam ELSA".format(context_dict['derived_data'], content),
            from_email='atm-elsa@nmsu.edu',
            to=[context_dict['contact_email']]
        )

        print("Before sending email")
        email.send()
        email_confirmation.send()
        print("After sending email")

        # Clear the draft after successful submission
        if draft_id:
            try:
                draft_uuid = uuid.UUID(draft_id)
                ReviewDraft.objects.filter(draft_id=draft_uuid).delete()
            except ValueError:
                pass  # Invalid UUID, silently skip
            
        context_dict['email_sent'] = True
        return render(request, 'review/index.html', context_dict)

    return render(request, 'review/index.html', context_dict)

# Save draft view: It accepts a POST request with JSON data, saves it as a draft, and returns a JSON response
@csrf_exempt
def save_draft(request, draft_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'Only POST allowed'}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    # Validate UUID format
    try:
        draft_uuid = uuid.UUID(draft_id)
    except ValueError:
        return JsonResponse({'error': 'Invalid draft ID'}, status=400)

    try:
        draft, created = ReviewDraft.objects.get_or_create(draft_id=draft_uuid)

        # Ensure content is a JSON string before saving
        if isinstance(data, str):
            # if string, validate it's valid JSON string
            try:
                json.loads(data)
                draft.content = data
            except json.JSONDecodeError:
                return JsonResponse({'error': 'Invalid JSON string'}, status=400)
        else:
            draft.content = json.dumps(data)

        if request.user.is_authenticated and not draft.user:
            draft.user = request.user

        draft.save()
    except Exception:
        traceback.print_exc()
        return JsonResponse({'error': 'Internal server error'}, status=500)

    return JsonResponse({'status': 'saved', 'created': created})


# Load draft view: It retrieves the draft by ID and returns its content as JSON
def load_draft(request, draft_id):
    try:
        draft_uuid = uuid.UUID(draft_id)
    except ValueError:
        return JsonResponse({'error': 'Invalid draft ID'}, status=400)

    try:
        draft = ReviewDraft.objects.get(draft_id=draft_uuid)
        content = json.loads(draft.content) if draft.content else {}
        return JsonResponse(content)
    except ReviewDraft.DoesNotExist:
        return JsonResponse({}, status=404)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Stored draft content corrupted'}, status=500)
