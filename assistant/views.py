import json
import logging
import re
import time

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

from .llm import GeminiClient, LLMUnavailable, QuotaExhausted
from .models import Conversation, Message
from .prompts import build_system_prompt

logger = logging.getLogger('assistant')

# Server-side caps so a client can't blow up the free-tier quota with one request
MAX_HISTORY_MESSAGES = 20
MAX_MESSAGE_CHARS = 4000

# Per-user rate limits (shared free-tier quota protection)
RATE_LIMIT_PER_MINUTE = 20
RATE_LIMIT_PER_DAY = 200
# Global cap across ALL users: per-user limits don't compose, and this is the
# actual spend/quota guard. Override with ASSISTANT_GLOBAL_DAILY_CAP.
GLOBAL_LIMIT_PER_DAY = 2000

FEEDBACK_CATEGORIES = ['Bug Report', 'Suggestion', 'Question', 'Other']
FEEDBACK_CONTEXTS = ['General', 'External bundle', 'Archive bundle']

SUBMIT_FEEDBACK_TOOL = {
    'name': 'submit_feedback',
    'description': (
        'Send user feedback (a bug report, suggestion, or question) to the ELSA '
        'team by email. Only call this after the user has explicitly confirmed '
        'they want the feedback sent.'
    ),
    'parameters': {
        'type': 'OBJECT',
        'properties': {
            'category': {'type': 'STRING', 'enum': FEEDBACK_CATEGORIES},
            'context': {'type': 'STRING', 'enum': FEEDBACK_CONTEXTS},
            'description': {'type': 'STRING',
                            'description': 'The feedback in the user\'s own words.'},
        },
        'required': ['category', 'description'],
    },
}


def _assistant_enabled():
    """Kill switch: settings flag (needs a restart) or cache flag (instant).

    The cache flag is set/cleared with `manage.py assistant_toggle off|on`, so
    the assistant can be disabled in seconds without touching prod config.
    """
    if not getattr(settings, 'ASSISTANT_ENABLED', True):
        return False
    try:
        if cache.get('assistant-disabled'):
            return False
    except Exception:
        pass
    return True


@login_required
def chat(request):
    if not _assistant_enabled():
        return JsonResponse(
            {'success': False, 'error': 'The assistant is temporarily disabled.'}, status=503)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)

    try:
        payload = json.loads(request.body)
        message_text = str(payload.get('message', ''))[:MAX_MESSAGE_CHARS].strip()
        assert message_text
    except (json.JSONDecodeError, AssertionError):
        return JsonResponse({'success': False, 'error': 'Invalid request.'}, status=400)

    limited, limit_message = _rate_limited(request.user)
    if limited:
        return JsonResponse({'success': False, 'error': limit_message}, status=429)

    api_key = getattr(settings, 'GEMINI_API_KEY', '')
    if not api_key:
        return JsonResponse(
            {'success': False, 'error': 'The assistant is not configured yet (missing API key).'},
            status=503,
        )

    # Server-authoritative history: load the conversation from the DB.
    conversation = None
    conversation_id = payload.get('conversation_id')
    if conversation_id:
        conversation = Conversation.objects.filter(pk=conversation_id, user=request.user).first()
    if conversation is None:
        conversation = Conversation.objects.create(user=request.user)

    conversation.messages.create(role='user', text=message_text)
    conversation.save(update_fields=['updated_at'])

    contents = [
        {'role': m.role, 'parts': [{'text': m.text}]}
        for m in conversation.messages.order_by('-created_at')[:MAX_HISTORY_MESSAGES][::-1]
        if m.text.strip()
    ]

    page_path = str(payload.get('page', ''))[:300]
    # Retrieval sees the previous user turn too, so follow-ups like "how do I
    # add one?" still pull the knowledge chunk of the topic being discussed.
    prev_turn = list(conversation.messages.filter(role='user')
                     .order_by('-created_at')
                     .values_list('text', flat=True)[1:2])
    retrieval_query = f'{prev_turn[0][:300]} {message_text}' if prev_turn else message_text
    system_prompt = build_system_prompt(request.user, page_path=page_path, query=retrieval_query)

    # All DB work is done (auth + history + prompt). Release the connection now
    # so a slow upstream call or long stream never holds a MariaDB slot hostage.
    connections.close_all()

    client = GeminiClient(api_key)
    started = time.monotonic()

    # Return the SSE response immediately — the model connection happens inside
    # the stream so the widget can show progress (and fallback status) live.
    response = StreamingHttpResponse(
        _sse_stream(client, request.user, conversation, system_prompt, contents, started),
        content_type='text/event-stream',
    )
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'  # disable proxy buffering so tokens flush immediately
    return response


@login_required
def history(request):
    """Return the user's most recent conversation so the widget can restore it."""
    if not _assistant_enabled():
        return JsonResponse({'enabled': False, 'conversation_id': None, 'messages': []})
    conversation = Conversation.objects.filter(user=request.user).first()
    if conversation is None:
        return JsonResponse({'enabled': True, 'conversation_id': None, 'messages': []})
    messages = [
        {'id': m.pk, 'role': m.role, 'text': m.text, 'rating': m.rating}
        for m in conversation.messages.order_by('-created_at')[:50][::-1]
    ]
    return JsonResponse({'enabled': True, 'conversation_id': conversation.pk, 'messages': messages})


@login_required
def rate(request):
    """Record a thumbs up/down on one of the user's assistant messages."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)
    try:
        payload = json.loads(request.body)
        rating = int(payload.get('rating'))
        assert rating in (1, -1, 0)
        message_id = int(payload.get('message_id'))
    except (json.JSONDecodeError, AssertionError, TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid request.'}, status=400)

    updated = Message.objects.filter(
        pk=message_id, role='model', conversation__user=request.user,
    ).update(rating=rating)
    if not updated:
        return JsonResponse({'success': False, 'error': 'Message not found.'}, status=404)
    return JsonResponse({'success': True})


def _sse_event(data):
    return f'data: {json.dumps(data)}\n\n'


def _clean_text(text):
    """House style: no em dashes in anything shown to the user."""
    return text.replace(' — ', ', ').replace('—', '-')


def _final_text(accumulated):
    """Scrub for persistence: internal markup out, house style enforced once
    more (an em dash split across two stream deltas escapes the per-delta
    pass)."""
    return re.sub(r'</?user_data>', '', _clean_text(accumulated)).strip()


# Heartbeat cadence and total silence budget for a connected-but-stalled model.
STREAM_HEARTBEAT_SECONDS = 8
STREAM_SILENCE_BUDGET_SECONDS = 60


def _pumped_events(client, upstream):
    """Relay client.iter_events through a reader thread.

    The socket read happens off-thread so this generator regains control every
    STREAM_HEARTBEAT_SECONDS even when the model sends nothing, letting the
    view emit "still working" updates instead of freezing the chat. Yields
    ('event', ev), ('heartbeat', None) on silence, and ('stalled', None) when
    the silence budget is exhausted. Reader exceptions re-raise here.
    """
    import queue
    import threading

    q = queue.Queue()

    def reader():
        try:
            for ev in client.iter_events(upstream):
                q.put(('event', ev))
            q.put(('end', None))
        except Exception as exc:  # relayed to the consumer thread
            q.put(('error', exc))

    threading.Thread(target=reader, daemon=True).start()

    silent_for = 0
    while True:
        try:
            kind, value = q.get(timeout=STREAM_HEARTBEAT_SECONDS)
        except queue.Empty:
            silent_for += STREAM_HEARTBEAT_SECONDS
            if silent_for >= STREAM_SILENCE_BUDGET_SECONDS:
                yield ('stalled', None)
                return
            yield ('heartbeat', None)
            continue
        silent_for = 0
        if kind == 'event':
            yield ('event', value)
        elif kind == 'end':
            return
        else:
            raise value


def _sse_stream(client, user, conversation, system_prompt, contents, started):
    """Connect to a model and relay its stream as delta events.

    Emits status events while falling back between models so the widget never
    sits silent. A submit_feedback call pauses text delivery, emails the
    feedback, then runs a second model turn (with the tool result) so the model
    can confirm to the user in its own words.
    """
    model = ''
    upstream = None
    try:
        attempts = 0
        for kind, m, resp in client.open_stream_events(system_prompt, contents,
                                                       tools=[SUBMIT_FEEDBACK_TOOL]):
            if kind == 'trying':
                attempts += 1
                if attempts == 2:
                    yield _sse_event({'type': 'status',
                                      'text': 'Still thinking, thanks for your patience...'})
            elif kind == 'connected':
                model, upstream = m, resp
                break
    except QuotaExhausted:
        yield _sse_event({'type': 'error', 'error': "The assistant has reached its daily usage limit. It resets overnight, so please try again tomorrow. For urgent questions, use the Contact page."})
        _save_reply(conversation, '', '', started, False, 'QuotaExhausted')
        return
    except LLMUnavailable:
        yield _sse_event({'type': 'error', 'error': 'Could not reach the assistant service. Please try again.'})
        _save_reply(conversation, '', '', started, False, 'LLMUnavailable')
        return

    yield from _stream_reply(client, user, conversation, system_prompt, contents,
                             started, model, upstream)


def _stream_reply(client, user, conversation, system_prompt, contents, started, model, upstream):
    """Relay the connected model stream; owns reply accumulation and persistence."""
    accumulated = ''
    feedback_sent = False
    error_note = ''
    finish_reason = ''
    try:
        function_call = None
        stalled = False
        for kind, event in _pumped_events(client, upstream):
            if kind == 'heartbeat':
                if not accumulated:
                    yield _sse_event({'type': 'status',
                                      'text': 'Still working on it, thanks for your patience...'})
                continue
            if kind == 'stalled':
                stalled = True
                break
            if 'text' in event:
                delta = _clean_text(event['text'])
                accumulated += delta
                yield _sse_event({'type': 'delta', 'text': delta})
            elif 'function_call' in event:
                function_call = event['function_call']
            elif 'finish_reason' in event:
                finish_reason = event['finish_reason']

        if stalled:
            error_note = 'stalled'
            if not accumulated:
                yield _sse_event({'type': 'error', 'error': 'The assistant is taking too long to respond. Please try again in a moment.'})
                _save_reply(conversation, '', model, started, feedback_sent, error_note)
                return

        if function_call and function_call.get('name') == 'submit_feedback':
            args = function_call.get('args', {})
            feedback_sent = _send_feedback_email(
                user,
                category=str(args.get('category', 'Other')),
                context=str(args.get('context', 'General')),
                description=str(args.get('description', '')),
            )
            followup = contents + [
                {'role': 'model', 'parts': [{'functionCall': function_call}]},
                {'role': 'user', 'parts': [{'functionResponse': {
                    'name': 'submit_feedback',
                    'response': {'result': 'sent' if feedback_sent else 'failed'},
                }}]},
            ]
            model, upstream2 = client.open_stream(system_prompt, followup,
                                                  tools=[SUBMIT_FEEDBACK_TOOL])
            for event in client.iter_events(upstream2):
                if 'text' in event:
                    delta = _clean_text(event['text'])
                    accumulated += delta
                    yield _sse_event({'type': 'delta', 'text': delta})
    except GeneratorExit:
        # Client disconnected mid-stream (tab closed, stop button): keep what
        # the model already said so the conversation history stays complete.
        if accumulated.strip():
            _save_reply(conversation, _final_text(accumulated), model, started,
                        feedback_sent, 'client_disconnected')
        raise
    except (requests.RequestException, QuotaExhausted, LLMUnavailable) as exc:
        error_note = type(exc).__name__
        if not accumulated:
            yield _sse_event({'type': 'error', 'error': 'The connection to the assistant was interrupted.'})
            _save_reply(conversation, '', model, started, feedback_sent, error_note)
            return

    if not accumulated.strip():
        if finish_reason and finish_reason != 'MAX_TOKENS':
            # Blocked by the provider (SAFETY, RECITATION, ...): be honest about it.
            yield _sse_event({'type': 'error', 'error': 'The assistant could not answer that request. Please rephrase and try again, or use the Contact page.'})
            _save_reply(conversation, '', model, started, feedback_sent, f'blocked:{finish_reason}')
        else:
            yield _sse_event({'type': 'error', 'error': 'The assistant returned an empty response. Please try again.'})
            _save_reply(conversation, '', model, started, feedback_sent, error_note or 'empty')
        return

    if finish_reason == 'MAX_TOKENS':
        # The reply was cut off at the output limit; tell the user rather than
        # ending mid-sentence as if nothing happened.
        note = '\n\n*(This reply hit the length limit and was cut short. Ask me to continue for the rest.)*'
        accumulated += note
        yield _sse_event({'type': 'delta', 'text': note})
        error_note = error_note or 'truncated:MAX_TOKENS'

    # The model occasionally echoes the internal <user_data> markup; scrub it,
    # and enforce house style once more for anything split across deltas.
    final_text = _final_text(accumulated)
    reply = _save_reply(conversation, final_text, model, started, feedback_sent, error_note)
    yield _sse_event({
        'type': 'done',
        'reply': final_text,
        'feedback_sent': feedback_sent,
        'conversation_id': conversation.pk,
        'message_id': reply.pk if reply else None,
    })


def _save_reply(conversation, text, model, started, feedback_sent, error_note):
    """Persist the assistant's reply with observability metadata."""
    latency_ms = int((time.monotonic() - started) * 1000)
    try:
        message = conversation.messages.create(
            role='model', text=text, model_used=model,
            latency_ms=latency_ms, feedback_sent=feedback_sent,
            error=error_note[:200],
        )
        conversation.save(update_fields=['updated_at'])
        logger.info('assistant: user=%s conv=%s model=%s latency_ms=%s chars=%s feedback=%s error=%s',
                    conversation.user_id, conversation.pk, model, latency_ms,
                    len(text), feedback_sent, error_note or '-')
        return message
    except Exception:
        logger.exception('assistant: failed to persist reply for conv=%s', conversation.pk)
        return None


def _bump(key, timeout):
    """Increment a cache counter, tolerating the add/incr expiry race."""
    cache.add(key, 0, timeout=timeout)
    try:
        return cache.incr(key)
    except ValueError:
        return 1


def _rate_limited(user):
    """Cache-based throttle: per-user minute/day caps plus a global daily cap.

    Checked in escalating order so a blocked request doesn't burn the larger
    buckets: minute-limited spam never consumes the user's daily allowance,
    and user-limited requests never consume the global one.
    """
    if _bump(f'assistant-rl-minute-{user.pk}', 60) > RATE_LIMIT_PER_MINUTE:
        return True, "You're sending messages a little too fast. Please wait a minute and try again."

    if _bump(f'assistant-rl-day-{user.pk}', 60 * 60 * 24) > RATE_LIMIT_PER_DAY:
        return True, "You've reached the daily limit for the assistant. It resets tomorrow. For urgent questions, please use the Contact page."

    global_cap = getattr(settings, 'ASSISTANT_GLOBAL_DAILY_CAP', GLOBAL_LIMIT_PER_DAY)
    if _bump('assistant-rl-global-day', 60 * 60 * 24) > global_cap:
        logger.warning('assistant: global daily cap (%s) reached', global_cap)
        return True, "The assistant has been very busy today and reached its daily capacity. It resets overnight; for urgent questions, please use the Contact page."

    return False, ''


def _send_feedback_email(user, category, context, description):
    """Email feedback to staff, mirroring the retired Beta Feedback form."""
    description = escape(description.strip())
    if not description:
        return False
    if category not in FEEDBACK_CATEGORIES:
        category = 'Other'
    if context not in FEEDBACK_CONTEXTS:
        context = 'General'

    submitted_at = localtime(timezone.now()).strftime('%B %d, %Y at %I:%M %p %Z')
    subject = f'[ELSA Beta Feedback] {category} - {user.username} (via Assistant)'
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
    return True
