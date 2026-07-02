import json
import re

import requests
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.mail import EmailMessage
from django.http import JsonResponse
from django.utils import timezone
from django.utils.html import escape
from django.utils.timezone import localtime

from .prompts import build_system_prompt

GEMINI_MODEL = 'gemini-2.5-flash'
GEMINI_URL = f'https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent'

# Server-side caps so a client can't blow up the free-tier quota with one request
MAX_HISTORY_MESSAGES = 20
MAX_MESSAGE_CHARS = 4000

FEEDBACK_PATTERN = re.compile(
    r'<<FEEDBACK\s+category="(?P<category>[^"]+)"\s+context="(?P<context>[^"]+)">>'
    r'\s*(?P<description>.*?)\s*<</FEEDBACK>>',
    re.DOTALL,
)
FEEDBACK_CATEGORIES = {'Bug Report', 'Suggestion', 'Question', 'Other'}
FEEDBACK_CONTEXTS = {'General', 'External bundle', 'Archive bundle'}


@login_required
def chat(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)

    try:
        payload = json.loads(request.body)
        messages = payload.get('messages', [])
        assert isinstance(messages, list) and messages
    except (json.JSONDecodeError, AssertionError):
        return JsonResponse({'success': False, 'error': 'Invalid request.'}, status=400)

    contents = []
    for msg in messages[-MAX_HISTORY_MESSAGES:]:
        role = 'model' if msg.get('role') == 'model' else 'user'
        text = str(msg.get('text', ''))[:MAX_MESSAGE_CHARS]
        if text.strip():
            contents.append({'role': role, 'parts': [{'text': text}]})
    if not contents:
        return JsonResponse({'success': False, 'error': 'Empty message.'}, status=400)

    api_key = getattr(settings, 'GEMINI_API_KEY', '')
    if not api_key:
        return JsonResponse(
            {'success': False, 'error': 'The assistant is not configured yet (missing API key).'},
            status=503,
        )

    body = {
        'system_instruction': {'parts': [{'text': build_system_prompt(request.user)}]},
        'contents': contents,
        'generationConfig': {'maxOutputTokens': 1024, 'temperature': 0.4},
    }

    try:
        response = requests.post(
            GEMINI_URL,
            headers={'x-goog-api-key': api_key, 'Content-Type': 'application/json'},
            json=body,
            timeout=30,
        )
    except requests.RequestException:
        return JsonResponse(
            {'success': False, 'error': 'Could not reach the assistant service. Please try again.'},
            status=502,
        )

    if response.status_code == 429:
        return JsonResponse(
            {'success': False, 'error': 'The assistant is receiving too many requests right now. Please wait a minute and try again.'},
            status=429,
        )
    if response.status_code != 200:
        return JsonResponse(
            {'success': False, 'error': 'The assistant ran into a problem. Please try again later.'},
            status=502,
        )

    try:
        data = response.json()
        reply = ''.join(
            part.get('text', '')
            for part in data['candidates'][0]['content']['parts']
        )
    except (KeyError, IndexError, ValueError):
        return JsonResponse(
            {'success': False, 'error': 'The assistant returned an unexpected response. Please try again.'},
            status=502,
        )

    reply, feedback_sent = _process_feedback(reply, request.user)

    return JsonResponse({'success': True, 'reply': reply.strip(), 'feedback_sent': feedback_sent})


def _process_feedback(reply, user):
    """Detect the assistant's feedback marker, email it to staff, strip it from the reply."""
    match = FEEDBACK_PATTERN.search(reply)
    if not match:
        return reply, False

    category = match.group('category')
    context = match.group('context')
    description = escape(match.group('description'))

    cleaned = FEEDBACK_PATTERN.sub('', reply).strip()
    if not cleaned:
        cleaned = "Done — I've sent your feedback to the ELSA team. Thank you!"

    if category not in FEEDBACK_CATEGORIES:
        category = 'Other'
    if context not in FEEDBACK_CONTEXTS:
        context = 'General'
    if not description.strip():
        return cleaned, False

    submitted_at = localtime(timezone.now()).strftime('%B %d, %Y at %I:%M %p %Z')
    subject = f'[ELSA Beta Feedback] {category} — {user.username} (via Assistant)'
    email_body = f"""
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background-color:#f4f4f4;font-family:Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f4f4;padding:32px 0;">
    <tr>
      <td align="center">
        <table width="600" cellpadding="0" cellspacing="0" style="background-color:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08);">
          <tr>
            <td style="background-color:#2F4F4F;padding:24px 32px;">
              <p style="margin:0;color:#ffffff;font-size:11px;letter-spacing:1px;text-transform:uppercase;">ELSA Beta Feedback &mdash; via Assistant</p>
              <h1 style="margin:6px 0 0;color:#ffffff;font-size:22px;">{category}</h1>
            </td>
          </tr>
          <tr>
            <td style="padding:28px 32px 8px;">
              <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td style="padding:8px 0;border-bottom:1px solid #eeeeee;">
                    <span style="font-size:11px;color:#888888;text-transform:uppercase;letter-spacing:0.5px;">Submitted by</span><br>
                    <span style="font-size:15px;color:#222222;">{user.username}</span>
                  </td>
                </tr>
                <tr>
                  <td style="padding:8px 0;border-bottom:1px solid #eeeeee;">
                    <span style="font-size:11px;color:#888888;text-transform:uppercase;letter-spacing:0.5px;">Context</span><br>
                    <span style="font-size:15px;color:#222222;">{context}</span>
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
          <tr>
            <td style="padding:8px 32px 32px;">
              <p style="margin:16px 0 8px;font-size:11px;color:#888888;text-transform:uppercase;letter-spacing:0.5px;">Description</p>
              <div style="background-color:#f8f8f8;border-left:4px solid #2F4F4F;border-radius:4px;padding:16px 20px;font-size:15px;color:#333333;line-height:1.6;white-space:pre-wrap;">{description}</div>
            </td>
          </tr>
          <tr>
            <td style="background-color:#f8f8f8;padding:16px 32px;border-top:1px solid #eeeeee;">
              <p style="margin:0;font-size:12px;color:#aaaaaa;">This message was collected by the ELSA Assistant chatbot.</p>
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
        body=email_body,
        from_email='atm-elsa@nmsu.edu',
        to=['lneakras@nmsu.edu', 'rupakdey@nmsu.edu'],
    )
    email.content_subtype = 'html'
    email.send(fail_silently=True)

    return cleaned, True
