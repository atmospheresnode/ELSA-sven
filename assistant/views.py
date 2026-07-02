import json
import re

import requests
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.mail import EmailMessage
from django.db import connections
from django.http import JsonResponse, StreamingHttpResponse
from django.utils import timezone
from django.utils.html import escape
from django.utils.timezone import localtime

from .prompts import build_system_prompt

# The free tier allows only ~20 requests/day *per model*, so we fall through a
# chain of models — each has its own daily bucket. Newer models (gemini-3.x)
# hang or 429 on the free tier entirely. Override the chain via GEMINI_MODELS
# in settings (list, best model first) once the account has paid quota.
GEMINI_MODELS = getattr(settings, 'GEMINI_MODELS', [
    'gemini-2.5-flash',
    'gemini-2.5-flash-lite',
    'gemini-flash-lite-latest',
    'gemini-2.0-flash',
])


def _stream_url(model):
    return (
        f'https://generativelanguage.googleapis.com/v1beta/models/{model}'
        ':streamGenerateContent?alt=sse'
    )

# Server-side caps so a client can't blow up the free-tier quota with one request
MAX_HISTORY_MESSAGES = 20
MAX_MESSAGE_CHARS = 4000

# Per-user rate limits (shared free-tier quota protection)
RATE_LIMIT_PER_MINUTE = 20
RATE_LIMIT_PER_DAY = 200

FEEDBACK_MARKER = '<<FEEDBACK'
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

    limited, message = _rate_limited(request.user)
    if limited:
        return JsonResponse({'success': False, 'error': message}, status=429)

    api_key = getattr(settings, 'GEMINI_API_KEY', '')
    if not api_key:
        return JsonResponse(
            {'success': False, 'error': 'The assistant is not configured yet (missing API key).'},
            status=503,
        )

    page_path = str(payload.get('page', ''))[:300]
    system_prompt = build_system_prompt(request.user, page_path=page_path)

    body = {
        'system_instruction': {'parts': [{'text': system_prompt}]},
        'contents': contents,
        'generationConfig': {'maxOutputTokens': 1024, 'temperature': 0.4},
    }

    # All DB work is done (auth + prompt). Release the connection now so a slow
    # upstream call or long stream never holds a MariaDB slot hostage.
    connections.close_all()

    # Try each model in the chain until one accepts the request. A 429 means
    # that model's own free-tier bucket is exhausted; 5xx means it's overloaded
    # — either way the next model may still work.
    upstream = None
    saw_429 = False
    saw_network_error = False
    for model in GEMINI_MODELS:
        try:
            candidate = requests.post(
                _stream_url(model),
                headers={'x-goog-api-key': api_key, 'Content-Type': 'application/json'},
                json=body,
                stream=True,
                timeout=(10, 60),
            )
        except requests.RequestException:
            saw_network_error = True
            continue
        if candidate.status_code == 200:
            upstream = candidate
            break
        saw_429 = saw_429 or candidate.status_code == 429
        candidate.close()

    if upstream is None:
        if saw_429:
            return JsonResponse(
                {'success': False, 'error': "The assistant has reached its free daily usage limit across all backup models. The limit resets overnight — please try again tomorrow, or use the Contact page for urgent questions."},
                status=429,
            )
        if saw_network_error:
            return JsonResponse(
                {'success': False, 'error': 'Could not reach the assistant service. Please try again.'},
                status=502,
            )
        return JsonResponse(
            {'success': False, 'error': 'The assistant ran into a problem. Please try again later.'},
            status=502,
        )

    response = StreamingHttpResponse(
        _sse_stream(upstream, request.user),
        content_type='text/event-stream',
    )
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'  # disable proxy buffering so tokens flush immediately
    return response


def _sse_event(data):
    return f'data: {json.dumps(data)}\n\n'


def _held_back_chars(text):
    """How many trailing chars of `text` could be the start of a feedback marker."""
    for k in range(len(FEEDBACK_MARKER) - 1, 0, -1):
        if text.endswith(FEEDBACK_MARKER[:k]):
            return k
    return 0


def _sse_stream(upstream, user):
    """Relay Gemini's SSE stream as delta events, withholding the feedback marker.

    The marker may arrive split across chunks, so text is only emitted once it
    can no longer be a marker prefix; everything from the marker onward is held
    until the stream ends, then processed and replaced with the cleaned tail.
    """
    accumulated = ''
    emitted = 0
    marker_found = False

    try:
        for line in upstream.iter_lines(decode_unicode=True):
            if not line or not line.startswith('data: '):
                continue
            try:
                chunk = json.loads(line[len('data: '):])
                delta = ''.join(
                    part.get('text', '')
                    for part in chunk['candidates'][0]['content']['parts']
                )
            except (KeyError, IndexError, ValueError):
                continue
            if not delta:
                continue

            accumulated += delta
            if marker_found:
                continue

            marker_at = accumulated.find(FEEDBACK_MARKER)
            if marker_at != -1:
                marker_found = True
                safe_end = marker_at
            else:
                safe_end = len(accumulated) - _held_back_chars(accumulated)

            if safe_end > emitted:
                yield _sse_event({'type': 'delta', 'text': accumulated[emitted:safe_end]})
                emitted = safe_end
    except requests.RequestException:
        yield _sse_event({'type': 'error', 'error': 'The connection to the assistant was interrupted.'})
        return
    finally:
        upstream.close()

    if not accumulated.strip():
        yield _sse_event({'type': 'error', 'error': 'The assistant returned an empty response. Please try again.'})
        return

    cleaned, feedback_sent = _process_feedback(accumulated, user)
    # Emit whatever the client hasn't seen yet (text held back around the marker,
    # or the fallback confirmation if the reply was only a marker block).
    tail = cleaned[emitted:] if cleaned.startswith(accumulated[:emitted]) else ''
    if not tail and feedback_sent and emitted == 0:
        tail = cleaned
    if tail:
        yield _sse_event({'type': 'delta', 'text': tail})

    yield _sse_event({'type': 'done', 'reply': cleaned.strip(), 'feedback_sent': feedback_sent})


def _rate_limited(user):
    """Cache-based throttle: per-minute and per-day message caps per user."""
    minute_key = f'assistant-rl-minute-{user.pk}'
    day_key = f'assistant-rl-day-{user.pk}'

    cache.add(minute_key, 0, timeout=60)
    cache.add(day_key, 0, timeout=60 * 60 * 24)
    try:
        minute_count = cache.incr(minute_key)
        day_count = cache.incr(day_key)
    except ValueError:
        # Key expired between add() and incr() — let the request through
        return False, ''

    if day_count > RATE_LIMIT_PER_DAY:
        return True, "You've reached the daily limit for the assistant. It resets tomorrow — for urgent questions, please use the Contact page."
    if minute_count > RATE_LIMIT_PER_MINUTE:
        return True, "You're sending messages a little too fast. Please wait a minute and try again."
    return False, ''


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
