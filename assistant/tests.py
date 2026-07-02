import json
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings

from .prompts import _page_context, build_system_prompt
from .views import _held_back_chars, _process_feedback


class FakeUpstream:
    """Mimics a requests streaming response from Gemini's SSE endpoint."""

    def __init__(self, chunks, status_code=200):
        self.status_code = status_code
        self._chunks = chunks
        self.closed = False

    def iter_lines(self, decode_unicode=True):
        for text in self._chunks:
            payload = json.dumps({'candidates': [{'content': {'parts': [{'text': text}]}}]})
            yield f'data: {payload}'
            yield ''

    def close(self):
        self.closed = True


def sse_events(response):
    """Collect the parsed SSE events from a StreamingHttpResponse."""
    raw = b''.join(response.streaming_content).decode()
    events = []
    for block in raw.split('\n\n'):
        if block.startswith('data: '):
            events.append(json.loads(block[len('data: '):]))
    return events


@override_settings(GEMINI_API_KEY='test-key')
class ChatEndpointTests(TestCase):

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='tester', password='pw')
        self.client.force_login(self.user)

    def post_chat(self, payload):
        return self.client.post(
            '/assistant/chat/', data=json.dumps(payload), content_type='application/json'
        )

    def test_get_not_allowed(self):
        self.assertEqual(self.client.get('/assistant/chat/').status_code, 405)

    def test_bad_json_rejected(self):
        resp = self.client.post('/assistant/chat/', data='not json', content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    def test_empty_messages_rejected(self):
        self.assertEqual(self.post_chat({'messages': []}).status_code, 400)
        self.assertEqual(self.post_chat({'messages': [{'role': 'user', 'text': '  '}]}).status_code, 400)

    @override_settings(GEMINI_API_KEY='')
    def test_missing_key_returns_503(self):
        resp = self.post_chat({'messages': [{'role': 'user', 'text': 'hi'}]})
        self.assertEqual(resp.status_code, 503)

    @patch('assistant.views.requests.post')
    def test_streaming_reply(self, mock_post):
        mock_post.return_value = FakeUpstream(['Hello ', 'there!'])
        resp = self.post_chat({'messages': [{'role': 'user', 'text': 'hi'}]})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'text/event-stream')

        events = sse_events(resp)
        deltas = ''.join(e['text'] for e in events if e['type'] == 'delta')
        done = [e for e in events if e['type'] == 'done'][0]
        self.assertEqual(deltas, 'Hello there!')
        self.assertEqual(done['reply'], 'Hello there!')
        self.assertFalse(done['feedback_sent'])

    @patch('assistant.views.EmailMessage')
    @patch('assistant.views.requests.post')
    def test_feedback_marker_never_reaches_client(self, mock_post, mock_email):
        # Marker split across chunk boundaries — the worst case for streaming
        mock_post.return_value = FakeUpstream([
            'Sending now. ',
            '<<FEED',
            'BACK category="Bug Report" context="General">>\nUpload freezes.\n<</FEED',
            'BACK>>\nDone, feedback sent!',
        ])
        resp = self.post_chat({'messages': [{'role': 'user', 'text': 'report a bug'}]})
        events = sse_events(resp)

        streamed = ''.join(e['text'] for e in events if e['type'] == 'delta')
        done = [e for e in events if e['type'] == 'done'][0]
        self.assertNotIn('<<FEEDBACK', streamed)
        self.assertNotIn('Upload freezes', streamed)
        self.assertIn('Done, feedback sent!', streamed)
        self.assertTrue(done['feedback_sent'])
        self.assertTrue(mock_email.called)
        self.assertIn('Upload freezes', mock_email.call_args.kwargs['body'])

    @patch('assistant.views.requests.post')
    def test_all_models_exhausted_maps_to_429(self, mock_post):
        mock_post.return_value = FakeUpstream([], status_code=429)
        resp = self.post_chat({'messages': [{'role': 'user', 'text': 'hi'}]})
        self.assertEqual(resp.status_code, 429)
        self.assertIn('daily usage limit', resp.json()['error'])
        # One attempt per model in the fallback chain
        from assistant.views import GEMINI_MODELS
        self.assertEqual(mock_post.call_count, len(GEMINI_MODELS))

    @patch('assistant.views.requests.post')
    def test_fallback_to_next_model(self, mock_post):
        mock_post.side_effect = [
            FakeUpstream([], status_code=429),
            FakeUpstream(['Fallback OK']),
        ]
        resp = self.post_chat({'messages': [{'role': 'user', 'text': 'hi'}]})
        self.assertEqual(resp.status_code, 200)
        done = [e for e in sse_events(resp) if e['type'] == 'done'][0]
        self.assertEqual(done['reply'], 'Fallback OK')
        self.assertEqual(mock_post.call_count, 2)

    @patch('assistant.views.RATE_LIMIT_PER_MINUTE', 2)
    @patch('assistant.views.requests.post')
    def test_per_user_rate_limit(self, mock_post, *_):
        mock_post.side_effect = lambda *a, **kw: FakeUpstream(['ok'])
        payload = {'messages': [{'role': 'user', 'text': 'hi'}]}
        self.assertEqual(self.post_chat(payload).status_code, 200)
        self.assertEqual(self.post_chat(payload).status_code, 200)
        resp = self.post_chat(payload)
        self.assertEqual(resp.status_code, 429)
        self.assertIn('too fast', resp.json()['error'])

    def test_login_required(self):
        self.client.logout()
        resp = self.client.post('/assistant/chat/', data='{}', content_type='application/json')
        self.assertEqual(resp.status_code, 302)


class FeedbackProcessingTests(SimpleTestCase):

    def setUp(self):
        self.user = MagicMock(username='tester')

    @patch('assistant.views.EmailMessage')
    def test_marker_parsed_and_stripped(self, mock_email):
        reply = ('Okay!\n\n<<FEEDBACK category="Suggestion" context="External bundle">>\n'
                 'Add dark mode.\n<</FEEDBACK>>\n\nSent!')
        cleaned, sent = _process_feedback(reply, self.user)
        self.assertTrue(sent)
        self.assertNotIn('<<FEEDBACK', cleaned)
        kwargs = mock_email.call_args.kwargs
        self.assertIn('Suggestion', kwargs['subject'])
        self.assertEqual(kwargs['to'], ['lneakras@nmsu.edu', 'rupakdey@nmsu.edu'])

    @patch('assistant.views.EmailMessage')
    def test_invalid_category_falls_back(self, mock_email):
        reply = '<<FEEDBACK category="Rant" context="Nowhere">>\nSomething.\n<</FEEDBACK>>'
        cleaned, sent = _process_feedback(reply, self.user)
        self.assertTrue(sent)
        self.assertIn('Other', mock_email.call_args.kwargs['subject'])

    @patch('assistant.views.EmailMessage')
    def test_no_marker_is_passthrough(self, mock_email):
        cleaned, sent = _process_feedback('Just an answer.', self.user)
        self.assertFalse(sent)
        self.assertEqual(cleaned, 'Just an answer.')
        self.assertFalse(mock_email.called)

    def test_held_back_chars(self):
        self.assertEqual(_held_back_chars('Hello '), 0)
        self.assertEqual(_held_back_chars('Hello <'), 1)
        self.assertEqual(_held_back_chars('Hello <<FEED'), 6)
        self.assertEqual(_held_back_chars(''), 0)


class PageContextTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='tester', password='pw')

    def test_known_static_pages(self):
        self.assertIn('Bundle Hub', _page_context(self.user, '/elsa/accounts/profile/'))
        self.assertIn('Contact', _page_context(self.user, '/elsa/contact/'))

    def test_unknown_bundle_pk_returns_none(self):
        self.assertIsNone(_page_context(self.user, '/elsa/build/999999/'))

    def test_no_path(self):
        self.assertIsNone(_page_context(self.user, ''))

    def test_prompt_includes_page_section(self):
        prompt = build_system_prompt(self.user, page_path='/elsa/contact/')
        self.assertIn('CURRENT PAGE', prompt)
