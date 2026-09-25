import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings

from .models import Conversation, Message
from .prompts import _bundle_summary, _page_context, _user_data, build_system_prompt
from .retriever import retrieve
from .views import ACTIVE_SESSION_KEY, _question_title, _send_feedback_email

EVALS_FILE = Path(__file__).parent / 'evals.json'


class FakeUpstream:
    """Mimics a requests streaming response from Gemini's SSE endpoint.

    `chunks` entries may be strings (text parts) or dicts (raw parts, e.g.
    {'functionCall': {...}}).
    """

    def __init__(self, chunks, status_code=200):
        self.status_code = status_code
        self._chunks = chunks
        self.closed = False

    def iter_lines(self, decode_unicode=True):
        for chunk in self._chunks:
            if isinstance(chunk, dict) and '__raw__' in chunk:
                payload = json.dumps(chunk['__raw__'])
            elif isinstance(chunk, dict) and '__finish__' in chunk:
                payload = json.dumps({'candidates': [{'content': {'parts': []},
                                                      'finishReason': chunk['__finish__']}]})
            else:
                part = {'text': chunk} if isinstance(chunk, str) else chunk
                payload = json.dumps({'candidates': [{'content': {'parts': [part]}}]})
            yield f'data: {payload}'
            yield ''

    def close(self):
        self.closed = True


def sse_events(response):
    raw = b''.join(response.streaming_content).decode()
    return [json.loads(block[len('data: '):])
            for block in raw.split('\n\n') if block.startswith('data: ')]


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

    def test_empty_message_rejected(self):
        self.assertEqual(self.post_chat({'message': '  '}).status_code, 400)

    @override_settings(GEMINI_API_KEY='')
    def test_missing_key_returns_503(self):
        self.assertEqual(self.post_chat({'message': 'hi'}).status_code, 503)

    @override_settings(ASSISTANT_ENABLED=False)
    def test_kill_switch(self):
        self.assertEqual(self.post_chat({'message': 'hi'}).status_code, 503)

    def test_login_required(self):
        self.client.logout()
        resp = self.client.post('/assistant/chat/', data='{}', content_type='application/json')
        self.assertEqual(resp.status_code, 302)

    @patch('assistant.llm.requests.post')
    def test_streaming_reply_persists_conversation(self, mock_post):
        mock_post.return_value = FakeUpstream(['Hello ', 'there!'])
        resp = self.post_chat({'message': 'hi'})
        self.assertEqual(resp.status_code, 200)
        events = sse_events(resp)

        done = [e for e in events if e['type'] == 'done'][0]
        self.assertEqual(done['reply'], 'Hello there!')
        self.assertFalse(done['feedback_sent'])

        conv = Conversation.objects.get(pk=done['conversation_id'], user=self.user)
        roles = list(conv.messages.values_list('role', flat=True))
        self.assertEqual(roles, ['user', 'model'])
        reply = conv.messages.get(pk=done['message_id'])
        self.assertEqual(reply.text, 'Hello there!')
        self.assertTrue(reply.model_used)
        self.assertIsNotNone(reply.latency_ms)

    @patch('assistant.llm.requests.post')
    def test_conversation_continues_with_history(self, mock_post):
        mock_post.return_value = FakeUpstream(['First'])
        done1 = [e for e in sse_events(self.post_chat({'message': 'one'})) if e['type'] == 'done'][0]

        mock_post.return_value = FakeUpstream(['Second'])
        done2 = [e for e in sse_events(self.post_chat(
            {'message': 'two', 'conversation_id': done1['conversation_id']})) if e['type'] == 'done'][0]

        self.assertEqual(done1['conversation_id'], done2['conversation_id'])
        # The second request should have sent the prior turns as context
        sent_contents = mock_post.call_args.kwargs['json']['contents']
        self.assertEqual(len(sent_contents), 3)  # user, model, user

    @patch('assistant.llm.requests.post')
    def test_foreign_conversation_id_starts_fresh(self, mock_post):
        other = User.objects.create_user(username='other', password='pw')
        other_conv = Conversation.objects.create(user=other)
        mock_post.return_value = FakeUpstream(['ok'])
        done = [e for e in sse_events(self.post_chat(
            {'message': 'hi', 'conversation_id': other_conv.pk})) if e['type'] == 'done'][0]
        self.assertNotEqual(done['conversation_id'], other_conv.pk)

    @patch('assistant.llm.requests.post')
    def test_new_thread_is_active_and_titled_from_the_question(self, mock_post):
        mock_post.return_value = FakeUpstream(['ok'])
        done = [e for e in sse_events(self.post_chat({'message': 'What goes in Citation Information?'}))
                if e['type'] == 'done'][0]
        conv = Conversation.objects.get(pk=done['conversation_id'])
        self.assertEqual((conv.title, conv.title_source),
                         ('What goes in Citation Information?', 'question'))
        self.assertEqual(self.client.session[ACTIVE_SESSION_KEY], conv.pk)

    @patch('assistant.llm.requests.post')
    def test_stream_starts_with_the_thread_id(self, mock_post):
        # Even when the reply then fails, the widget knows which thread to retry in.
        mock_post.return_value = FakeUpstream([], status_code=503)
        events = sse_events(self.post_chat({'message': 'hi'}))
        self.assertEqual(events[0]['type'], 'start')
        conv = Conversation.objects.get(user=self.user)
        self.assertEqual(events[0]['conversation_id'], conv.pk)
        self.assertEqual(events[-1]['type'], 'error')

    @patch('assistant.llm.requests.post')
    def test_retry_does_not_save_the_question_twice(self, mock_post):
        mock_post.return_value = FakeUpstream([], status_code=503)
        start = sse_events(self.post_chat({'message': 'What is a LID?'}))[0]
        cache.clear()  # the failed attempt put the model on cooldown
        mock_post.return_value = FakeUpstream(['A logical identifier.'])
        sse_events(self.post_chat({'message': 'What is a LID?', 'retry': True,
                                   'conversation_id': start['conversation_id']}))
        conv = Conversation.objects.get(pk=start['conversation_id'])
        self.assertEqual(conv.messages.filter(role='user').count(), 1)
        sent = mock_post.call_args.kwargs['json']['contents']
        self.assertEqual([c['role'] for c in sent], ['user'])

    @patch('assistant.llm.requests.post')
    def test_retry_flag_after_an_answer_is_a_normal_message(self, mock_post):
        mock_post.return_value = FakeUpstream(['A logical identifier.'])
        done = [e for e in sse_events(self.post_chat({'message': 'What is a LID?'}))
                if e['type'] == 'done'][0]
        mock_post.return_value = FakeUpstream(['Again.'])
        sse_events(self.post_chat({'message': 'What is a LID?', 'retry': True,
                                   'conversation_id': done['conversation_id']}))
        conv = Conversation.objects.get(pk=done['conversation_id'])
        self.assertEqual(conv.messages.filter(role='user').count(), 2)

    @patch('assistant.llm.requests.post')
    def test_followups_become_chips_and_are_not_saved(self, mock_post):
        mock_post.return_value = FakeUpstream([
            'Add authors in Citation Information.\n\n<follow',
            'ups>How do I add an editor?|What are keywords for?| - Can I edit it later? ',
            '|What is ORCID?</followups>',
        ])
        events = sse_events(self.post_chat({'message': 'How do I add authors?'}))
        done = [e for e in events if e['type'] == 'done'][0]
        self.assertEqual(done['reply'], 'Add authors in Citation Information.')
        self.assertEqual(done['followups'], ['How do I add an editor?', 'What are keywords for?',
                                             'Can I edit it later?'])
        reply = Message.objects.get(pk=done['message_id'])
        self.assertEqual(reply.text, 'Add authors in Citation Information.')
        system = mock_post.call_args.kwargs['json']['system_instruction']['parts'][0]['text']
        self.assertIn('<followups>', system)

    @patch('assistant.llm.requests.post')
    def test_cut_off_followups_are_dropped_and_the_length_note_kept(self, mock_post):
        mock_post.return_value = FakeUpstream(['A long answer.\n<followups>How do I', {'__finish__': 'MAX_TOKENS'}])
        done = [e for e in sse_events(self.post_chat({'message': 'explain'})) if e['type'] == 'done'][0]
        self.assertTrue(done['reply'].startswith('A long answer.'))
        self.assertIn('cut short', done['reply'])
        self.assertNotIn('followups', done['reply'])
        self.assertEqual(done['followups'], ['How do I'])

    @patch('assistant.llm.requests.post')
    def test_no_followups_line_means_no_chips(self, mock_post):
        mock_post.return_value = FakeUpstream(['Hello!'])
        done = [e for e in sse_events(self.post_chat({'message': 'hi'})) if e['type'] == 'done'][0]
        self.assertEqual(done['followups'], [])

    @patch('assistant.views.EmailMessage')
    @patch('assistant.llm.requests.post')
    def test_feedback_function_call(self, mock_post, mock_email):
        # First stream: model calls submit_feedback; second stream: confirmation text
        mock_post.side_effect = [
            FakeUpstream([
                'Sending that now. ',
                {'functionCall': {'name': 'submit_feedback', 'args': {
                    'category': 'Bug Report', 'context': 'External bundle',
                    'description': 'Upload freezes at 90%.'}}},
            ]),
            FakeUpstream(["Done — I've sent that to the ELSA team!"]),
        ]
        resp = self.post_chat({'message': 'yes, send it'})
        events = sse_events(resp)
        done = [e for e in events if e['type'] == 'done'][0]

        self.assertTrue(done['feedback_sent'])
        self.assertIn('sent that to the ELSA team', done['reply'])
        self.assertTrue(mock_email.called)
        kwargs = mock_email.call_args.kwargs
        self.assertIn('Bug Report', kwargs['subject'])
        self.assertIn('Upload freezes', kwargs['body'])
        # The second model round received the function response
        followup = mock_post.call_args.kwargs['json']['contents']
        self.assertEqual(followup[-1]['parts'][0]['functionResponse']['name'], 'submit_feedback')

    @patch('assistant.llm.requests.post')
    def test_all_models_exhausted_streams_error(self, mock_post):
        # The SSE response starts before the model connects, so quota exhaustion
        # arrives as an error event rather than an HTTP status.
        mock_post.return_value = FakeUpstream([], status_code=429)
        resp = self.post_chat({'message': 'hi'})
        self.assertEqual(resp.status_code, 200)
        errors = [e for e in sse_events(resp) if e['type'] == 'error']
        self.assertIn('daily usage limit', errors[0]['error'])

    @patch('assistant.llm.requests.post')
    def test_fallback_to_next_model_with_status_event(self, mock_post):
        mock_post.side_effect = [
            FakeUpstream([], status_code=429),
            FakeUpstream(['Fallback OK']),
        ]
        resp = self.post_chat({'message': 'hi'})
        events = sse_events(resp)
        done = [e for e in events if e['type'] == 'done'][0]
        self.assertEqual(done['reply'], 'Fallback OK')
        # The user got a friendly still-working note (no internals leaked)
        statuses = [e for e in events if e['type'] == 'status']
        self.assertTrue(statuses)
        self.assertNotIn('model', statuses[0]['text'].lower())

    @patch('assistant.llm.requests.post')
    def test_truncated_reply_gets_a_note(self, mock_post):
        mock_post.return_value = FakeUpstream(['Partial answer', {'__finish__': 'MAX_TOKENS'}])
        resp = self.post_chat({'message': 'hi'})
        done = [e for e in sse_events(resp) if e['type'] == 'done'][0]
        self.assertIn('cut short', done['reply'])

    @patch('assistant.llm.requests.post')
    def test_safety_block_gives_clear_error(self, mock_post):
        mock_post.return_value = FakeUpstream([{'__finish__': 'SAFETY'}])
        resp = self.post_chat({'message': 'hi'})
        errors = [e for e in sse_events(resp) if e['type'] == 'error']
        self.assertIn('could not answer', errors[0]['error'])

    @patch('assistant.llm.requests.post')
    def test_failed_model_goes_on_cooldown(self, mock_post):
        # First request: model A 429s, model B serves. Second request: model A
        # is skipped entirely (no new probe), so only one more HTTP call happens.
        mock_post.side_effect = [
            FakeUpstream([], status_code=429),
            FakeUpstream(['One']),
            FakeUpstream(['Two']),
        ]
        # The response is a lazy stream: consume it so the generator runs.
        done1 = [e for e in sse_events(self.post_chat({'message': 'hi'})) if e['type'] == 'done'][0]
        self.assertEqual(done1['reply'], 'One')
        self.assertEqual(mock_post.call_count, 2)
        done2 = [e for e in sse_events(self.post_chat({'message': 'again'})) if e['type'] == 'done'][0]
        self.assertEqual(done2['reply'], 'Two')
        self.assertEqual(mock_post.call_count, 3)  # cooled model skipped

    @patch('assistant.views.RATE_LIMIT_PER_MINUTE', 2)
    @patch('assistant.llm.requests.post')
    def test_per_user_rate_limit(self, mock_post, *_):
        mock_post.side_effect = lambda *a, **kw: FakeUpstream(['ok'])
        self.assertEqual(self.post_chat({'message': 'hi'}).status_code, 200)
        self.assertEqual(self.post_chat({'message': 'hi'}).status_code, 200)
        resp = self.post_chat({'message': 'hi'})
        self.assertEqual(resp.status_code, 429)
        self.assertIn('too fast', resp.json()['error'])

    @patch('assistant.llm.requests.post')
    def test_client_disconnect_saves_partial_reply(self, mock_post):
        mock_post.return_value = FakeUpstream(['Partial ', 'answer ', 'text'])
        resp = self.post_chat({'message': 'hi'})
        stream = iter(resp.streaming_content)
        seen = b''
        while b'"delta"' not in seen:
            seen += next(stream)
        resp.close()  # simulates the tab closing / stop button mid-stream

        conv = Conversation.objects.get(user=self.user)
        reply = conv.messages.filter(role='model').order_by('-created_at').first()
        self.assertIsNotNone(reply)
        self.assertIn('Partial', reply.text)
        self.assertEqual(reply.error, 'client_disconnected')

    @patch('assistant.llm.requests.post')
    def test_prompt_block_gives_honest_error(self, mock_post):
        mock_post.return_value = FakeUpstream(
            [{'__raw__': {'promptFeedback': {'blockReason': 'SAFETY'}}}])
        events = sse_events(self.post_chat({'message': 'hi'}))
        errors = [e for e in events if e['type'] == 'error']
        self.assertTrue(errors)
        self.assertIn('could not answer', errors[0]['error'])
        reply = (Conversation.objects.get(user=self.user)
                 .messages.filter(role='model').order_by('-created_at').first())
        self.assertEqual(reply.error, 'blocked:PROMPT_SAFETY')

    def test_cache_kill_switch_disables_chat(self):
        cache.set('assistant-disabled', 1, None)
        try:
            self.assertEqual(self.post_chat({'message': 'hi'}).status_code, 503)
        finally:
            cache.delete('assistant-disabled')


class HistoryAndRatingTests(TestCase):

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='tester', password='pw')
        self.client.force_login(self.user)
        self.conv = Conversation.objects.create(user=self.user)
        self.msg_user = self.conv.messages.create(role='user', text='hi')
        self.msg_model = self.conv.messages.create(role='model', text='hello!')

    def activate(self, conv):
        session = self.client.session
        session[ACTIVE_SESSION_KEY] = conv.pk
        session.save()

    def test_history_restores_the_active_thread(self):
        self.activate(self.conv)
        data = self.client.get('/assistant/history/').json()
        self.assertEqual(data['conversation_id'], self.conv.pk)
        self.assertEqual([m['role'] for m in data['messages']], ['user', 'model'])

    def test_new_login_lands_on_a_fresh_chat(self):
        self.activate(self.conv)
        self.client.logout()
        self.client.force_login(self.user)
        data = self.client.get('/assistant/history/').json()
        self.assertIsNone(data['conversation_id'])
        self.assertEqual(data['messages'], [])
        self.assertTrue(data['has_previous'])  # so the widget can offer the old chats

    def test_history_empty_for_new_user(self):
        self.client.force_login(User.objects.create_user(username='fresh', password='pw'))
        data = self.client.get('/assistant/history/').json()
        self.assertIsNone(data['conversation_id'])
        self.assertEqual(data['messages'], [])
        self.assertFalse(data['has_previous'])

    def test_opening_a_past_thread_makes_it_active(self):
        data = self.client.get(f'/assistant/history/?conversation_id={self.conv.pk}').json()
        self.assertEqual(data['conversation_id'], self.conv.pk)
        self.assertEqual(self.client.session[ACTIVE_SESSION_KEY], self.conv.pk)

    def test_cannot_open_someone_elses_thread(self):
        other = User.objects.create_user(username='other', password='pw')
        theirs = Conversation.objects.create(user=other)
        theirs.messages.create(role='user', text='secret')
        data = self.client.get(f'/assistant/history/?conversation_id={theirs.pk}').json()
        self.assertIsNone(data['conversation_id'])
        self.assertEqual(data['messages'], [])

    def test_history_names_the_bundle_being_viewed(self):
        from build.models import Bundle
        with tempfile.TemporaryDirectory() as media, tempfile.TemporaryDirectory() as archive:
            with override_settings(MEDIA_ROOT=media, ARCHIVE_DIR=archive):
                mine = Bundle.objects.create(user=self.user, name='Mars dust', bundle_type='External',
                                             version='1800')
                theirs = Bundle.objects.create(user=User.objects.create_user(username='o2', password='pw'),
                                               name='Secret', bundle_type='Archive', version='1800')

                def looking_at(page):
                    return self.client.get('/assistant/history/', {'page': page}).json()['looking_at']
                self.assertEqual(looking_at(f'/build/{mine.pk}/citation_information/'),
                                 'Mars dust (External)')
                self.assertEqual(looking_at(f'/build/{theirs.pk}/'), '')
                self.assertEqual(looking_at('/accounts/bundles/'), '')
                self.activate(self.conv)  # also on a restored thread
                self.assertEqual(looking_at(f'/build/{mine.pk}/'), 'Mars dust (External)')

    def test_new_chat_forgets_the_active_thread(self):
        self.activate(self.conv)
        self.assertEqual(self.client.post('/assistant/conversations/new/').status_code, 200)
        self.assertNotIn(ACTIVE_SESSION_KEY, self.client.session)
        self.assertIsNone(self.client.get('/assistant/history/').json()['conversation_id'])

    def test_rate_message(self):
        resp = self.client.post('/assistant/rate/',
                                data=json.dumps({'message_id': self.msg_model.pk, 'rating': 1}),
                                content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.msg_model.refresh_from_db()
        self.assertEqual(self.msg_model.rating, 1)

    def test_cannot_rate_user_message_or_foreign_message(self):
        resp = self.client.post('/assistant/rate/',
                                data=json.dumps({'message_id': self.msg_user.pk, 'rating': 1}),
                                content_type='application/json')
        self.assertEqual(resp.status_code, 404)

        other = User.objects.create_user(username='other', password='pw')
        self.client.force_login(other)
        resp = self.client.post('/assistant/rate/',
                                data=json.dumps({'message_id': self.msg_model.pk, 'rating': -1}),
                                content_type='application/json')
        self.assertEqual(resp.status_code, 404)


class ChatListTests(TestCase):

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='tester', password='pw')
        self.client.force_login(self.user)
        self.older = Conversation.objects.create(user=self.user, title='Citation help', title_source='ai')
        self.newer = Conversation.objects.create(user=self.user)
        self.other = Conversation.objects.create(
            user=User.objects.create_user(username='other', password='pw'), title='Not yours')

    def post(self, url, payload):
        return self.client.post(url, data=json.dumps(payload), content_type='application/json')

    def test_lists_only_own_threads_newest_first(self):
        session = self.client.session
        session[ACTIVE_SESSION_KEY] = self.older.pk
        session.save()
        items = self.client.get('/assistant/conversations/').json()['conversations']
        self.assertEqual([c['id'] for c in items], [self.newer.pk, self.older.pk])
        self.assertEqual(items[0]['title'], 'Untitled chat')
        self.assertEqual([c['active'] for c in items], [False, True])
        self.assertTrue(all(c['when'] for c in items))

    def test_rename_is_final_and_does_not_reorder(self):
        resp = self.post('/assistant/conversations/rename/',
                         {'conversation_id': self.older.pk, 'title': '  My   citation  thread '})
        self.assertEqual(resp.json()['title'], 'My citation thread')
        self.older.refresh_from_db()
        self.assertEqual((self.older.title, self.older.title_source), ('My citation thread', 'user'))
        items = self.client.get('/assistant/conversations/').json()['conversations']
        self.assertEqual(items[0]['id'], self.newer.pk)

    def test_rename_rejects_blank_and_foreign(self):
        self.assertEqual(self.post('/assistant/conversations/rename/',
                                   {'conversation_id': self.older.pk, 'title': '   '}).status_code, 400)
        self.assertEqual(self.post('/assistant/conversations/rename/',
                                   {'conversation_id': self.other.pk, 'title': 'Mine now'}).status_code, 404)
        self.other.refresh_from_db()
        self.assertEqual(self.other.title, 'Not yours')

    def test_delete_removes_thread_and_clears_active(self):
        self.older.messages.create(role='user', text='hi')
        session = self.client.session
        session[ACTIVE_SESSION_KEY] = self.older.pk
        session.save()
        self.assertEqual(self.post('/assistant/conversations/delete/',
                                   {'conversation_id': self.older.pk}).status_code, 200)
        self.assertFalse(Conversation.objects.filter(pk=self.older.pk).exists())
        self.assertFalse(Message.objects.filter(conversation_id=self.older.pk).exists())
        self.assertNotIn(ACTIVE_SESSION_KEY, self.client.session)

    def test_cannot_delete_someone_elses_thread(self):
        self.assertEqual(self.post('/assistant/conversations/delete/',
                                   {'conversation_id': self.other.pk}).status_code, 404)
        self.assertTrue(Conversation.objects.filter(pk=self.other.pk).exists())

    def test_search_matches_titles_and_message_text(self):
        self.newer.messages.create(role='user', text='How do I add ORCID ids for authors?')
        self.newer.messages.create(role='model', text='Use the ORCID field.')
        self.other.messages.create(role='user', text='orcid question from someone else')

        def ids(q):
            return [c['id'] for c in self.client.get('/assistant/conversations/', {'q': q})
                    .json()['conversations']]
        self.assertEqual(ids('citation'), [self.older.pk])   # by title
        self.assertEqual(ids('orcid'), [self.newer.pk])      # by message, once, own only
        self.assertEqual(ids('nothing like this'), [])
        self.assertEqual(ids('  '), [self.newer.pk, self.older.pk])

    def test_list_endpoints_need_login_and_post(self):
        self.assertEqual(self.client.get('/assistant/conversations/new/').status_code, 405)
        self.assertEqual(self.client.get('/assistant/conversations/title/').status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.get('/assistant/conversations/').status_code, 302)


@override_settings(GEMINI_API_KEY='test-key')
class AutoTitleTests(TestCase):

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='tester', password='pw')
        self.client.force_login(self.user)
        self.conv = Conversation.objects.create(
            user=self.user, title='How do I fill in the citation', title_source='question')
        self.exchange('How do I fill in the citation info for my bundle?',
                      'Open Citation Information and add authors, editors, and keywords.')

    def exchange(self, question, answer):
        self.conv.messages.create(role='user', text=question)
        self.conv.messages.create(role='model', text=answer)

    def request_title(self):
        return self.client.post('/assistant/conversations/title/',
                                data=json.dumps({'conversation_id': self.conv.pk}),
                                content_type='application/json').json()

    @patch('assistant.llm.requests.post')
    def test_names_the_thread_from_question_and_answer(self, mock_post):
        mock_post.return_value = FakeUpstream(['Citation Information help'])
        data = self.request_title()
        self.assertEqual((data['title'], data['title_source']), ('Citation Information help', 'ai'))
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.title_source, 'ai')

        body = mock_post.call_args.kwargs['json']
        self.assertIn('gemini-2.5-flash-lite', mock_post.call_args.args[0])
        sent = body['contents'][0]['parts'][0]['text']
        self.assertIn('User: How do I fill in the citation info', sent)
        self.assertIn('Assistant: Open Citation Information', sent)
        self.assertNotIn('tools', body)

    @patch('assistant.llm.requests.post')
    def test_one_attempt_per_exchange(self, mock_post):
        mock_post.return_value = FakeUpstream(['SKIP'])
        self.request_title()
        self.request_title()
        self.assertEqual(mock_post.call_count, 1)

    @patch('assistant.llm.requests.post')
    def test_small_talk_is_skipped_then_retried_on_the_next_exchange(self, mock_post):
        mock_post.return_value = FakeUpstream(['SKIP'])
        data = self.request_title()
        self.assertEqual((data['title'], data['title_source']),
                         ('How do I fill in the citation', 'question'))

        self.exchange('Also, what is an Alias?', 'An Alias is an alternate name.')
        mock_post.return_value = FakeUpstream(['Citation and Alias help'])
        self.assertEqual(self.request_title()['title'], 'Citation and Alias help')

    @patch('assistant.llm.requests.post')
    def test_user_title_is_never_replaced(self, mock_post):
        Conversation.objects.filter(pk=self.conv.pk).update(title='Mine', title_source='user')
        data = self.request_title()
        self.assertEqual((data['title'], data['title_source']), ('Mine', 'user'))
        mock_post.assert_not_called()

    @patch('assistant.llm.requests.post')
    def test_not_before_the_reply_arrives(self, mock_post):
        self.conv.messages.create(role='user', text='and one more thing')
        self.request_title()
        mock_post.assert_not_called()

    @patch('assistant.llm.requests.post')
    def test_refreshes_once_when_the_thread_has_settled(self, mock_post):
        Conversation.objects.filter(pk=self.conv.pk).update(title='Citation help', title_source='ai')
        self.exchange('q2', 'a2')
        self.request_title()  # turn 2: already named, not due
        mock_post.assert_not_called()
        self.exchange('q3', 'a3')
        self.exchange('Actually, how do I submit?', 'Use Review & Submit.')
        mock_post.return_value = FakeUpstream(['Submitting a bundle'])
        self.assertEqual(self.request_title()['title'], 'Submitting a bundle')  # turn 4
        self.exchange('q5', 'a5')
        self.request_title()
        self.assertEqual(mock_post.call_count, 1)

    @patch('assistant.llm.requests.post')
    def test_gives_up_after_three_turns_of_small_talk(self, mock_post):
        self.exchange('hi', 'hello')
        self.exchange('hey', 'hello again')
        self.exchange('yo', 'hi there')  # turn 4, never named
        self.request_title()
        mock_post.assert_not_called()

    @patch('assistant.llm.requests.post')
    def test_model_output_is_tidied(self, mock_post):
        mock_post.return_value = FakeUpstream(['Title: "**Bundle ID — Alias** difference."\nExtra line'])
        self.assertEqual(self.request_title()['title'], 'Bundle ID, Alias difference')

    @patch('assistant.llm.requests.post')
    def test_pds4_underscores_survive_tidying(self, mock_post):
        mock_post.return_value = FakeUpstream(['Citation_Information fields'])
        self.assertEqual(self.request_title()['title'], 'Citation_Information fields')

    @patch('assistant.llm.requests.post')
    def test_quota_failure_keeps_the_placeholder(self, mock_post):
        mock_post.return_value = FakeUpstream([], status_code=429)
        data = self.request_title()
        self.assertEqual(data['title_source'], 'question')

    @override_settings(ASSISTANT_GLOBAL_DAILY_CAP=0)
    @patch('assistant.llm.requests.post')
    def test_counts_against_the_global_cap(self, mock_post):
        self.request_title()
        mock_post.assert_not_called()

    def test_question_placeholder(self):
        self.assertEqual(_question_title('  What   is a LID? '), 'What is a LID?')
        long = _question_title('word ' * 30)
        self.assertLessEqual(len(long), 51)
        self.assertTrue(long.endswith('…'))


class FeedbackEmailTests(SimpleTestCase):

    @patch('assistant.views.EmailMessage')
    def test_email_sent_with_validated_fields(self, mock_email):
        user = MagicMock(username='tester')
        sent = _send_feedback_email(user, 'Rant', 'Nowhere', 'Add dark mode.')
        self.assertTrue(sent)
        kwargs = mock_email.call_args.kwargs
        self.assertIn('Other', kwargs['subject'])  # invalid category falls back
        self.assertEqual(kwargs['to'], ['lneakras@nmsu.edu', 'rupakdey@nmsu.edu'])

    @patch('assistant.views.EmailMessage')
    def test_empty_description_not_sent(self, mock_email):
        sent = _send_feedback_email(MagicMock(username='t'), 'Bug Report', 'General', '   ')
        self.assertFalse(sent)
        self.assertFalse(mock_email.called)


class RetrieverTests(SimpleTestCase):

    def test_watches_comments_never_reach_prompt_text(self):
        from .retriever import _CHUNKS
        for chunk in _CHUNKS:
            self.assertNotIn('watches:', chunk['text'])
            self.assertNotIn('watches', chunk['title'].lower())
            self.assertTrue(chunk['title'], chunk['name'])

    def test_release_notes_live_chunk_from_cache(self):
        from django.core.cache import cache
        cache.set('elsa_release_notes', [
            {'version': '1.38.0', 'date': 'July 2026',
             'bullets': ['<strong>Assistant:</strong> chatbot pilot added']},
        ], 60)
        try:
            names = [c['name'] for c in retrieve("what's new in the latest ELSA version?")]
            self.assertIn('release_notes_live', names)
        finally:
            cache.delete('elsa_release_notes')

    def test_release_notes_failure_is_silent(self):
        from django.core.cache import cache
        cache.delete('elsa_release_notes')
        with patch('requests.get', side_effect=Exception('offline')):
            # Static retrieval keeps working even when the live chunk fails
            names = [c['name'] for c in retrieve('citation information')]
            self.assertIn('citation_information', names)

    def test_citation_query_finds_citation_chunk(self):
        names = [c['name'] for c in retrieve('What goes in citation information?')]
        self.assertIn('citation_information', names)

    def test_alias_vs_bundle_id_query(self):
        names = [c['name'] for c in retrieve('Bundle ID vs Alias?')]
        self.assertIn('alias', names)

    def test_gibberish_returns_nothing_relevant(self):
        self.assertEqual(retrieve('xyzzy plugh'), [])


class BundleSummaryTests(SimpleTestCase):

    def make_bundle(self, mod=True, cit=False, targets=False, netcdf=2,
                    description='', keyword='', target_names=(),
                    docs=(), collections=(('data', 'External'),)):
        b = MagicMock()
        b.name = 'Now'
        b.bundle_type = 'External'
        b.get_status.return_value = 'in_progress'
        b.submitted_at = None
        b.modification_history_set.exists.return_value = mod
        b.citation_information_set.exists.return_value = cit
        if description or keyword:
            citation = MagicMock(description=description, keyword=keyword)
        else:
            citation = None
        b.citation_information_set.first.return_value = citation
        b.targets.exists.return_value = targets
        b.targets.values_list.return_value = list(target_names)
        b.netcdf_files.values_list.return_value = [
            (f'file{i}.nc', True) for i in range(netcdf)]
        b.product_document_set.values_list.return_value = list(docs)
        b.additionalcollections_set.values_list.return_value = list(collections)
        return b

    def test_missing_components_are_listed(self):
        line = _bundle_summary(self.make_bundle())
        self.assertIn('missing required: Citation Information, Targets', line)
        self.assertIn('already has: Modification History', line)
        self.assertIn('2 NetCDF files', line)

    def test_contents_are_listed(self):
        line = _bundle_summary(self.make_bundle(
            netcdf=2, docs=['User Guide'], collections=[('mydata', 'External')]))
        self.assertIn('2 NetCDF files uploaded: <user_data>file0.nc</user_data>, <user_data>file1.nc</user_data>', line)
        self.assertIn('1 document: <user_data>User Guide</user_data>', line)
        self.assertIn('data collections: <user_data>mydata</user_data> (External)', line)

    def test_empty_contents_are_stated_not_omitted(self):
        line = _bundle_summary(self.make_bundle(netcdf=0, docs=(), collections=()))
        self.assertIn('no NetCDF files uploaded yet', line)
        self.assertIn('no documents yet', line)
        self.assertIn('no data collection created yet', line)

    def test_unprocessed_files_are_flagged(self):
        b = self.make_bundle()
        b.netcdf_files.values_list.return_value = [('good.nc', True), ('bad.nc', False)]
        line = _bundle_summary(b)
        self.assertIn('(1 not processed)', line)

    def test_description_and_targets_included(self):
        line = _bundle_summary(self.make_bundle(
            cit=True, targets=True,
            description='Mars GCM dust storm simulations',
            keyword='mars; dust', target_names=['Mars']))
        self.assertIn('about: <user_data>Mars GCM dust storm simulations</user_data>', line)
        self.assertIn('keywords: <user_data>mars; dust</user_data>', line)
        self.assertIn('targets: Mars', line)

    def test_complete_bundle(self):
        line = _bundle_summary(self.make_bundle(mod=True, cit=True, targets=True))
        self.assertIn('all required components complete', line)


class NetCDFContentsTests(TestCase):
    """The assistant can describe what an uploaded NetCDF file contains."""

    def _write_nc(self, tmpdir, name='tiny.nc'):
        import numpy as np
        import xarray as xr
        path = os.path.join(tmpdir, name)
        ds = xr.Dataset(
            {'temp': (('time', 'lat'), np.zeros((2, 3)),
                      {'long_name': 'air temperature', 'units': 'K'})},
            coords={'time': [0, 1], 'lat': [0.0, 1.0, 2.0]},
            attrs={'title': 'Tiny test model output'},
        )
        ds.to_netcdf(path)
        return path

    def test_contents_summary_reads_header(self):
        from .prompts import _netcdf_contents
        cache.clear()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_nc(tmpdir)
            nc = MagicMock(pk=99991)
            nc.file.path = path
            summary = _netcdf_contents(nc)
        self.assertIn('title: Tiny test model output', summary)
        self.assertIn('time=2', summary)
        self.assertIn('lat=3', summary)
        self.assertIn('temp (air temperature, K)', summary)
        self.assertTrue(summary.startswith('<user_data>'))

    def test_unreadable_file_yields_empty(self):
        from .prompts import _netcdf_contents
        cache.clear()
        nc = MagicMock(pk=99992)
        nc.file.path = '/nonexistent/nope.nc'
        self.assertEqual(_netcdf_contents(nc), '')

    def test_moved_file_is_found_in_bundle_directory(self):
        # Processing moves the .nc from uploads/ into the bundle directory
        # without updating the FileField; the summary must follow it.
        from .prompts import _netcdf_contents
        from build.models import Bundle, NetCDFFile
        cache.clear()
        user = User.objects.create_user(username='mvuser', password='pw')
        with tempfile.TemporaryDirectory() as media, tempfile.TemporaryDirectory() as archive:
            with override_settings(MEDIA_ROOT=media, ARCHIVE_DIR=archive):
                b = Bundle.objects.create(user=user, name='mvbundle', bundle_type='External', version='1800')
                os.makedirs(b.directory(), exist_ok=True)
                self._write_nc(b.directory(), 'moved.nc')
                nc = NetCDFFile.objects.create(bundle=b, title='moved.nc', file='moved.nc', processed=True)
                summary = _netcdf_contents(nc)
        self.assertIn('temp (air temperature, K)', summary)

    def test_bundle_page_context_includes_file_contents(self):
        from build.models import Bundle, NetCDFFile
        cache.clear()
        user = User.objects.create_user(username='ncuser', password='pw')
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_nc(tmpdir, 'sim.nc')
            with override_settings(MEDIA_ROOT=tmpdir):
                b = Bundle.objects.create(user=user, name='ncbundle', bundle_type='External', version='1800')
                NetCDFFile.objects.create(bundle=b, title='sim.nc', file='sim.nc', processed=True)
                context = _page_context(user, f'/build/{b.pk}/')
        self.assertIn('Contents of their uploaded NetCDF file', context)
        self.assertIn('temp (air temperature, K)', context)


class BundleSummaryRealModelTests(TestCase):
    """Guards the reverse-accessor names against the real models.

    The MagicMock-based tests above cannot catch a wrong accessor (mocks
    auto-create any attribute); a typo like `netcdffile_set` silently dropped
    the NetCDF info from every summary until this test existed.
    """

    def test_summary_reads_real_relations(self):
        from build.models import AdditionalCollections, Bundle, NetCDFFile, Product_Document
        user = User.objects.create_user(username='summaryuser', password='pw')
        b = Bundle.objects.create(user=user, name='realsum', bundle_type='External', version='1800')
        NetCDFFile.objects.create(bundle=b, title='sim.nc', file='sim.nc', processed=True)
        NetCDFFile.objects.create(bundle=b, title='raw.nc', file='raw.nc', processed=False)
        AdditionalCollections.objects.create(bundle=b, collection_name='mydata', collection_type='External')
        Product_Document.objects.create(
            bundle=b, document_name='User Guide', author_list='', copyright='',
            description='', document_editions='', publication_date='', revision_id='')

        line = _bundle_summary(b)

        self.assertIn('2 NetCDF files uploaded', line)
        self.assertIn('sim.nc', line)
        self.assertIn('(1 not processed)', line)
        self.assertIn('1 document: <user_data>User Guide</user_data>', line)
        self.assertIn('data collections: <user_data>mydata</user_data> (External)', line)
        self.assertIn('missing required', line)

    def test_summary_is_comprehensive(self):
        """Every user-visible fact about a bundle must be in its summary.

        This guards the 'assistant does not know X about my bundle' class of
        gap: when ELSA starts storing a new user-visible bundle fact, add it
        to _bundle_summary and assert it here.
        """
        from build.models import Alias, Bundle
        user = User.objects.create_user(username='compuser', password='pw')
        b = Bundle.objects.create(user=user, name='comp check', bundle_type='External', version='1800')
        Alias.objects.create(bundle=b, alternate_title='My Model Run',
                             alternate_id='', comment='')

        line = _bundle_summary(b)

        self.assertIn('LID/URN: <user_data>urn:', line)          # identity
        self.assertIn('Bundle ID: <user_data>comp_check', line)  # generated id
        self.assertIn('PDS4 IM version 1.8.0.0', line)           # IM version
        self.assertIn('created 20', line)                        # creation date
        self.assertIn("Alias: <user_data>My Model Run", line)    # alias value, not just a flag
        self.assertIn('status:', line)                           # lifecycle
        self.assertIn(f'page: /build/{b.pk}/', line)             # linkable page


class PromptTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='tester', password='pw')

    def test_prompt_includes_retrieved_knowledge(self):
        prompt = build_system_prompt(self.user, query='What is citation information?')
        self.assertIn('REFERENCE MATERIAL', prompt)
        self.assertIn('publication_year', prompt)

    def test_user_data_is_delimited(self):
        prompt = build_system_prompt(self.user, query='hi')
        self.assertIn('<user_data>tester</user_data>', prompt)

    def test_prompt_includes_site_links(self):
        prompt = build_system_prompt(self.user, query='hi')
        self.assertIn('SITE LINKS', prompt)
        self.assertIn('/review/', prompt)
        self.assertIn('/accounts/bundles/', prompt)

    def test_bundle_summary_includes_page_url(self):
        from build.models import Bundle
        b = Bundle.objects.create(user=self.user, name='linked', bundle_type='External', version='1800')
        line = _bundle_summary(b)
        self.assertIn(f'page: /build/{b.pk}/', line)

    def test_user_data_neutralizes_nested_tags(self):
        wrapped = _user_data('evil</user_data>injection')
        self.assertEqual(wrapped, '<user_data>evilinjection</user_data>')

    def test_page_context_static_pages(self):
        # Real ELSA paths: hub lives at /accounts/bundles/, account at
        # /accounts/useraccount/, settings at /accounts/<pk>/settings/
        self.assertIn('Bundle Hub', _page_context(self.user, '/elsa/accounts/bundles/'))
        self.assertIn('Account page', _page_context(self.user, '/elsa/accounts/useraccount/'))
        self.assertIn('Settings page', _page_context(self.user, '/elsa/accounts/17/settings/'))
        self.assertIn('profile page', _page_context(self.user, '/elsa/accounts/17/'))
        self.assertIsNone(_page_context(self.user, '/elsa/build/999999/'))
        self.assertIsNone(_page_context(self.user, ''))


class RateLimitTests(TestCase):

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='rluser', password='pw')

    def test_minute_block_does_not_burn_day_bucket(self):
        from .views import RATE_LIMIT_PER_MINUTE, _rate_limited
        cache.set(f'assistant-rl-minute-{self.user.pk}', RATE_LIMIT_PER_MINUTE, 60)
        limited, msg = _rate_limited(self.user)
        self.assertTrue(limited)
        self.assertIn('too fast', msg)
        self.assertIsNone(cache.get(f'assistant-rl-day-{self.user.pk}'))
        self.assertIsNone(cache.get('assistant-rl-global-day'))

    def test_global_daily_cap(self):
        from .views import _rate_limited
        with override_settings(ASSISTANT_GLOBAL_DAILY_CAP=1):
            cache.set('assistant-rl-global-day', 1, 600)
            limited, msg = _rate_limited(self.user)
        self.assertTrue(limited)
        self.assertIn('busy', msg)

    def test_kill_switch_via_cache(self):
        from .views import _assistant_enabled
        self.assertTrue(_assistant_enabled())
        cache.set('assistant-disabled', 1, None)
        try:
            self.assertFalse(_assistant_enabled())
        finally:
            cache.delete('assistant-disabled')


class StreamPumpTests(SimpleTestCase):

    def test_heartbeats_then_stall_on_silent_model(self):
        import time as _time
        from unittest.mock import patch as _patch

        from . import views

        class SilentClient:
            @staticmethod
            def iter_events(upstream):
                _time.sleep(1)
                if False:
                    yield None

        with _patch.object(views, 'STREAM_HEARTBEAT_SECONDS', 0.05), \
                _patch.object(views, 'STREAM_SILENCE_BUDGET_SECONDS', 0.15):
            kinds = [k for k, _ in views._pumped_events(SilentClient(), None)]
        self.assertIn('heartbeat', kinds)
        self.assertEqual(kinds[-1], 'stalled')


class RetrieverScoringTests(SimpleTestCase):

    def test_unique_matches_beat_repetition(self):
        from .retriever import _make_chunk, _score, _tokens
        q = set(_tokens('how do I upload netcdf data files'))
        short_relevant = _make_chunk(
            'short', '# Uploading data files\nUpload NetCDF data files to your bundle.')
        long_spam = _make_chunk(
            'long', '# Something else\n' + 'bundle bundle data data data ' * 50)
        self.assertGreater(_score(short_relevant, q), _score(long_spam, q))


class OpsCommandTests(TestCase):

    def test_toggle_purge_stats(self):
        from datetime import timedelta
        from io import StringIO

        from django.core.management import call_command
        from django.utils import timezone

        cache.clear()
        out = StringIO()
        call_command('assistant_toggle', 'off', stdout=out)
        self.assertTrue(cache.get('assistant-disabled'))
        call_command('assistant_toggle', 'on', stdout=out)
        self.assertFalse(cache.get('assistant-disabled'))

        user = User.objects.create_user(username='purgeuser', password='pw')
        conv = Conversation.objects.create(user=user)
        Conversation.objects.filter(pk=conv.pk).update(
            updated_at=timezone.now() - timedelta(days=120))

        out = StringIO()
        call_command('assistant_purge', stdout=out)
        self.assertIn('would be deleted', out.getvalue())
        self.assertTrue(Conversation.objects.filter(pk=conv.pk).exists())

        out = StringIO()
        call_command('assistant_purge', '--delete', stdout=out)
        self.assertFalse(Conversation.objects.filter(pk=conv.pk).exists())

        out = StringIO()
        call_command('assistant_stats', stdout=out)
        self.assertIn('Assistant stats', out.getvalue())


class KnowledgeCheckTests(SimpleTestCase):
    """The staleness check is content-based: a chunk goes stale when the source it
    describes changes, not when anything gets committed. The tests that matter are the
    ones separating those two, since conflating them is what made the check useless."""

    def test_parse_watches(self):
        from .management.commands.assistant_knowledge_check import parse_watches
        text = '<!-- watches: build/views.py, templates/build -->\n# Title\nBody'
        self.assertEqual(parse_watches(text), ['build/views.py', 'templates/build'])
        self.assertEqual(parse_watches('# No declaration'), [])

    def test_parse_watches_keeps_anchors(self):
        from assistant.knowledge_check import parse_watches, split_spec
        specs = parse_watches('<!-- watches: build/models.py#Bundle, build/views.py -->')
        self.assertEqual(specs, ['build/models.py#Bundle', 'build/views.py'])
        self.assertEqual(split_spec('build/models.py#Bundle'), ('build/models.py', 'Bundle'))
        self.assertEqual(split_spec('build/views.py'), ('build/views.py', None))

    def test_parse_fingerprints(self):
        from assistant.knowledge_check import parse_fingerprints
        text = ('<!-- watches: a, b -->\n'
                '<!-- fingerprint:\n     a = 1111aaaa\n     b = 2222bbbb\n-->\n# T')
        self.assertEqual(parse_fingerprints(text), {'a': '1111aaaa', 'b': '2222bbbb'})
        self.assertEqual(parse_fingerprints('# nothing here'), {})

    def test_reviewed_marker_is_informational_only(self):
        """It used to decide staleness, which is what made every merge trip the check."""
        from assistant.knowledge_check import parse_reviewed
        self.assertEqual(parse_reviewed('<!-- reviewed: 2026-07-10 -->'), '2026-07-10')
        self.assertIsNone(parse_reviewed('<!-- watches: a -->\n# no marker'))

    def test_normalize_ignores_formatting_but_not_content(self):
        from assistant.knowledge_check import normalize
        self.assertEqual(normalize('a = 1   \n\n\nb = 2\n'), normalize('a = 1\nb = 2'))
        self.assertNotEqual(normalize('a = 1'), normalize('a = 2'))

    def test_extract_anchor_takes_the_whole_block_and_stops(self):
        from assistant.knowledge_check import extract_anchor
        source = ('import os\n\n'
                  'class Wanted:\n'
                  '    def method(self):\n'
                  '        return 1\n\n'
                  'class Other:\n'
                  '    pass\n')
        block = extract_anchor(source, 'Wanted', 'x#Wanted')
        self.assertIn('def method', block)
        self.assertNotIn('class Other', block)
        self.assertNotIn('import os', block)

    def test_extract_anchor_covers_every_definition_of_a_name(self):
        """models.py defines several names twice; hashing only the first would leave
        the definition that actually runs unwatched."""
        from assistant.knowledge_check import extract_anchor
        source = ('class Dup:\n    first = 1\n\n\nclass Dup:\n    second = 2\n')
        block = extract_anchor(source, 'Dup', 'x#Dup')
        self.assertIn('first = 1', block)
        self.assertIn('second = 2', block)

    def test_prefix_anchor_covers_a_family_of_definitions(self):
        from assistant.knowledge_check import extract_anchor
        source = ('def search(self):\n    a = 1\n\n'
                  'def search_target(self):\n    b = 2\n\n'
                  'def unrelated(self):\n    c = 3\n')
        block = extract_anchor(source, 'search*', 'x#search*')
        self.assertIn('a = 1', block)
        self.assertIn('b = 2', block)
        self.assertNotIn('c = 3', block)

    def test_plain_anchor_does_not_match_a_longer_name(self):
        from assistant.knowledge_check import WatchError, extract_anchor
        with self.assertRaises(WatchError):
            extract_anchor('def search_target(self):\n    pass\n', 'search', 'x#search')

    def test_extract_anchor_reports_a_name_that_is_not_there(self):
        from assistant.knowledge_check import WatchError, extract_anchor
        with self.assertRaises(WatchError):
            extract_anchor('class Real:\n    pass\n', 'Missing', 'x#Missing')

    def test_a_watch_that_resolves_to_nothing_is_reported(self):
        """The old check silently skipped these, so a renamed file left a chunk unguarded."""
        from assistant.knowledge_check import WatchError, fingerprint
        with self.assertRaises(WatchError):
            fingerprint('build/no_such_file_here.py')
        with self.assertRaises(WatchError):
            fingerprint('build/models.py#NoSuchClassAnywhere')

    def test_fingerprint_is_stable_and_region_scoped(self):
        from assistant.knowledge_check import fingerprint
        self.assertEqual(fingerprint('build/models.py#Bundle'),
                         fingerprint('build/models.py#Bundle'))
        self.assertNotEqual(fingerprint('build/models.py#Bundle'),
                            fingerprint('build/models.py#Product_Collection'))

    def test_parse_baseline(self):
        from assistant.knowledge_check import parse_baseline
        self.assertEqual(parse_baseline('<!-- baseline: d8df4b96320a -->'), 'd8df4b96320a')
        self.assertIsNone(parse_baseline('<!-- reviewed: 2026-08-28 -->'))

    def test_every_chunk_records_a_baseline_to_diff_against(self):
        from assistant.knowledge_check import KNOWLEDGE_DIR, parse_baseline
        missing = [c.name for c in sorted(KNOWLEDGE_DIR.glob('*.md'))
                   if not parse_baseline(c.read_text())]
        self.assertEqual(missing, [], 'run knowledge_check.py --update to record baselines')

    def test_region_diff_degrades_without_a_baseline(self):
        """A shallow clone or an unknown commit costs the diff, never the check itself."""
        from assistant.knowledge_check import region_diff
        self.assertEqual(region_diff(None, 'build/models.py#Alias'), [])
        self.assertEqual(region_diff('0' * 40, 'build/models.py#Alias'), [])

    def test_every_shipped_chunk_is_currently_in_sync(self):
        """Guards the committed fingerprints: if this fails, a chunk needs review and
        a re-baseline via `manage.py assistant_knowledge_check --update`."""
        from assistant.knowledge_check import run_check
        stale, unwatched, broken = run_check(out=lambda *a, **k: None)
        self.assertEqual(broken, [], 'knowledge chunks watch paths that no longer resolve')
        self.assertEqual(unwatched, [], 'knowledge chunks with no watches declared')
        self.assertEqual([chunk for chunk, _ in stale], [])


@override_settings(GEMINI_API_KEY='test-key')
class KnowledgeUsedTests(TestCase):
    """Each reply records the knowledge chunks it was built from, so a bad
    answer can be traced to retrieval, the chunk, or the prompt."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='tester', password='pw')
        self.client.force_login(self.user)

    def post_chat(self, message):
        return self.client.post('/assistant/chat/', data=json.dumps({'message': message}),
                                content_type='application/json')

    @patch('assistant.llm.requests.post')
    def test_reply_records_the_chunks_it_used(self, mock_post):
        mock_post.return_value = FakeUpstream(['Authors and a year.'])
        done = [e for e in sse_events(self.post_chat('What goes in Citation Information?'))
                if e['type'] == 'done'][0]
        reply = Message.objects.get(pk=done['message_id'])
        names = reply.knowledge_used.split(',')
        self.assertEqual(names[0], 'citation_information')
        # The chunks recorded are exactly the ones put in the prompt
        sent_prompt = mock_post.call_args.kwargs['json']['system_instruction']['parts'][0]['text']
        for chunk in retrieve('What goes in Citation Information?'):
            self.assertIn(f'--- {chunk["title"]} ---', sent_prompt)
            self.assertIn(chunk['name'], names)

    @patch('assistant.llm.requests.post')
    def test_failed_reply_still_records_the_chunks(self, mock_post):
        mock_post.return_value = FakeUpstream([], status_code=429)
        sse_events(self.post_chat('What goes in Citation Information?'))
        reply = Message.objects.get(role='model')
        self.assertEqual(reply.error, 'QuotaExhausted')
        self.assertIn('citation_information', reply.knowledge_used)

    @patch('assistant.llm.requests.post')
    def test_no_matching_chunk_records_nothing(self, mock_post):
        mock_post.return_value = FakeUpstream(['?'])
        sse_events(self.post_chat('xqzv blorptangle'))
        self.assertEqual(Message.objects.get(role='model').knowledge_used, '')

    def test_prompt_uses_given_chunks_over_the_query(self):
        prompt = build_system_prompt(self.user, query='What is citation information?', chunks=[])
        self.assertNotIn('REFERENCE MATERIAL', prompt)


class RatingReasonTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='tester', password='pw')
        self.client.force_login(self.user)
        conv = Conversation.objects.create(user=self.user)
        conv.messages.create(role='user', text='hi')
        self.reply = conv.messages.create(role='model', text='hello!')

    def rate(self, **payload):
        payload.setdefault('message_id', self.reply.pk)
        resp = self.client.post('/assistant/rate/', data=json.dumps(payload),
                                content_type='application/json')
        self.reply.refresh_from_db()
        return resp

    def test_thumbs_down_then_reason_and_comment(self):
        self.assertEqual(self.rate(rating=-1).status_code, 200)
        self.assertEqual(self.reply.rating, -1)
        self.assertIsNotNone(self.reply.rated_at)
        self.assertEqual(self.reply.rating_reason, '')

        self.assertEqual(self.rate(rating=-1, reason='wrong', comment='  The year is wrong.  ').status_code, 200)
        self.assertEqual(self.reply.rating_reason, 'wrong')
        self.assertEqual(self.reply.rating_comment, 'The year is wrong.')

    def test_a_late_bare_vote_keeps_the_reason(self):
        # The widget's two requests can arrive in either order
        self.rate(rating=-1, reason='outdated', comment='old')
        self.rate(rating=-1)
        self.assertEqual((self.reply.rating_reason, self.reply.rating_comment), ('outdated', 'old'))

    def test_changing_the_vote_clears_the_reason(self):
        self.rate(rating=-1, reason='wrong', comment='nope')
        self.rate(rating=1)
        self.assertEqual((self.reply.rating, self.reply.rating_reason, self.reply.rating_comment),
                         (1, '', ''))
        self.assertIsNotNone(self.reply.rated_at)
        self.rate(rating=-1, reason='other', comment='x')
        self.rate(rating=0)
        self.assertEqual((self.reply.rating, self.reply.rating_reason, self.reply.rating_comment),
                         (0, '', ''))
        self.assertIsNone(self.reply.rated_at)

    def test_reason_on_a_thumbs_up_is_ignored(self):
        self.rate(rating=1, reason='wrong', comment='ignored')
        self.assertEqual((self.reply.rating_reason, self.reply.rating_comment), ('', ''))

    def test_reason_only_comment_only_both_accepted(self):
        self.rate(rating=-1, reason='unanswered')
        self.assertEqual((self.reply.rating_reason, self.reply.rating_comment), ('unanswered', ''))
        self.rate(rating=-1, comment='just words')
        self.assertEqual((self.reply.rating_reason, self.reply.rating_comment), ('', 'just words'))

    def test_bad_input_rejected(self):
        self.assertEqual(self.rate(rating=-1, reason='rude').status_code, 400)
        self.assertEqual(self.rate(rating=-1, reason=['wrong']).status_code, 400)
        resp = self.client.post('/assistant/rate/', data='[1, 2]', content_type='application/json')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.reply.rating, 0)

    def test_comment_is_capped(self):
        self.rate(rating=-1, comment='x' * 5000)
        self.assertEqual(len(self.reply.rating_comment), 1000)

    def test_cannot_set_a_reason_on_someone_elses_reply(self):
        self.client.force_login(User.objects.create_user(username='other', password='pw'))
        self.assertEqual(self.rate(rating=-1, reason='wrong', comment='hijack').status_code, 404)
        self.assertEqual((self.reply.rating, self.reply.rating_comment), (0, ''))


class FeedbackLoopCommandTests(TestCase):
    """assistant_stats reports, emails and exports thumbs-downs; assistant_eval
    refuses a case with nothing to check; assistant_purge keeps rated threads."""

    def setUp(self):
        from django.core import mail
        from django.utils import timezone
        self.mail = mail
        self.now = timezone.now()
        self.user = User.objects.create_user(username='rater', password='pw')

    def thread(self, *turns, rating=0, reason='', comment='', knowledge='', rated_days_ago=0,
               created_days_ago=0):
        """A conversation of alternating user/model turns; the last reply gets the rating."""
        from datetime import timedelta
        conv = Conversation.objects.create(user=self.user)
        reply = None
        for i, text in enumerate(turns):
            msg = conv.messages.create(role='user' if i % 2 == 0 else 'model', text=text)
            Message.objects.filter(pk=msg.pk).update(
                created_at=self.now - timedelta(days=created_days_ago, seconds=len(turns) - i))
            reply = msg
        Message.objects.filter(pk=reply.pk).update(
            rating=rating, rating_reason=reason, rating_comment=comment, knowledge_used=knowledge,
            rated_at=(self.now - timedelta(days=rated_days_ago)) if rating else None)
        return conv, Message.objects.get(pk=reply.pk)

    def stats(self, *args):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command('assistant_stats', *args, stdout=out)
        return out.getvalue()

    def test_report_shows_reason_comment_chunks_and_hint(self):
        self.thread('Is the Alias required?', 'Yes, always.', rating=-1, reason='wrong',
                    comment='It is optional.', knowledge='alias,bundle_structure')
        out = self.stats()
        self.assertIn('Q: Is the Alias required?', out)
        self.assertIn('A: Yes, always.', out)
        self.assertIn('Reason: Wrong information', out)
        self.assertIn('Comment: It is optional.', out)
        self.assertIn('Knowledge used: alias, bundle_structure', out)
        self.assertIn('Look at: Check the knowledge chunks', out)

    def test_window_follows_when_it_was_rated(self):
        self.thread('old reply, rated yesterday', 'a1', rating=-1, created_days_ago=30, rated_days_ago=1)
        self.thread('old reply, rated long ago', 'a2', rating=-1, created_days_ago=30, rated_days_ago=20)
        conv, legacy = self.thread('legacy rating, no timestamp', 'a3', rating=-1, created_days_ago=2)
        Message.objects.filter(pk=legacy.pk).update(rated_at=None)
        out = self.stats('--days', '7')
        self.assertIn('rated yesterday', out)
        self.assertIn('legacy rating', out)
        self.assertNotIn('rated long ago', out)
        self.assertIn('rated in this window: 2', out)

    def test_email_goes_to_staff_escaped(self):
        self.thread('<b>bold question</b>', 'answer <script>x</script>', rating=-1,
                    reason='other', comment='<img src=x onerror=alert(1)>')
        self.thread('fine', 'good', rating=1)
        out = self.stats('--email')
        self.assertIn('Emailed 1 thumbs-down', out)
        self.assertEqual(len(self.mail.outbox), 1)
        sent = self.mail.outbox[0]
        self.assertEqual(sent.to, ['lneakras@nmsu.edu', 'rupakdey@nmsu.edu'])
        self.assertEqual(sent.from_email, 'atm-elsa@nmsu.edu')
        self.assertEqual(sent.subject, '[ELSA Assistant] 1 thumbs-down answer to review')
        # HTML part: users' words and the model's are escaped, never markup
        html, mimetype = sent.alternatives[0]
        self.assertEqual(mimetype, 'text/html')
        self.assertNotIn('<script>', html)
        self.assertNotIn('<img src=x', html)
        self.assertNotIn('<b>bold', html)
        self.assertIn('&lt;img src=x', html)
        # Plain-text part reads as the user wrote it
        self.assertIn('<img src=x onerror=alert(1)>', sent.body)
        for part in (html, sent.body):
            self.assertNotIn('\u2014', part)
            self.assertNotIn('{{', part)
            self.assertNotIn('{%', part)

    def test_email_summarises_reasons_and_chunks(self):
        self.thread('q1', 'a1', rating=-1, reason='wrong', knowledge='alias,bundle_structure')
        self.thread('q2', 'a2', rating=-1, reason='wrong', knowledge='alias')
        self.thread('q3', 'a3', rating=-1, reason='outdated', knowledge='review_submit_help')
        self.thread('q4', 'a4', rating=-1)
        self.thread('q5', 'a5', rating=1)
        self.stats('--email')
        sent = self.mail.outbox[0]
        html = sent.alternatives[0][0]
        self.assertIn('4 answers to review', html)
        self.assertIn('Wrong information &nbsp;2', html)
        self.assertIn('Out of date &nbsp;1', html)
        self.assertIn('No reason given &nbsp;1', html)
        self.assertIn('alias.md &times;2', html)
        self.assertIn('No knowledge chunks recorded', html)
        self.assertIn('Why: Wrong information 2, Out of date 1, No reason given 1', sent.body)
        self.assertIn('alias.md x2', sent.body)
        # The ELSA logo travels inline, as in the sign-in emails
        if sent.attachments:
            self.assertIn('cid:elsa_logo', html)
            self.assertEqual(sent.mixed_subtype, 'related')

    def test_no_email_for_a_quiet_week(self):
        self.thread('fine', 'good', rating=1)
        out = self.stats('--email')
        self.assertIn('no email sent', out)
        self.assertEqual(len(self.mail.outbox), 0)

    def test_long_weeks_are_capped_and_counted(self):
        from assistant.management.commands import assistant_stats
        with patch.object(assistant_stats, 'MAX_LISTED', 2):
            for i in range(3):
                self.thread(f'q{i}', f'a{i}', rating=-1)
            out = self.stats('--email')
        self.assertIn('rated in this window: 3', out)
        self.assertIn('1 more in the admin', out)
        self.assertIn('3 thumbs-down answers to review', self.mail.outbox[0].subject)
        self.assertIn('1 more in the', self.mail.outbox[0].body)

    def test_export_evals_drafts(self):
        existing = json.loads(Path(EVALS_FILE).read_text())[0]['question']
        self.thread('How do I add a target?', 'Click Targets.', rating=-1, reason='unanswered',
                    comment='Where?', knowledge='targets_context_products')
        # A follow-up: the draft carries the exchange before it
        self.thread('What is an Alias?', 'An optional second name.', 'Is it required?', 'Yes.',
                    rating=-1, reason='wrong')
        self.thread(existing.upper(), 'whatever', rating=-1)  # already an eval case
        self.thread('How do I add a target?', 'Again.', rating=-1)  # repeated question

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'drafts.json')
            out = self.stats('--export-evals', path)
            drafts = json.loads(Path(path).read_text())
            self.assertIn('Wrote 2 draft eval case(s)', out)
            self.assertIn('2 skipped', out)

            by_q = {d['question']: d for d in drafts}
            target = by_q['How do I add a target?']
            self.assertEqual(target['must_include'], [])
            self.assertNotIn('history', target)
            self.assertEqual(target['_review']['comment'], 'Where?')
            self.assertEqual(target['_review']['knowledge_used'], 'targets_context_products')
            follow = by_q['Is it required?']
            self.assertEqual(follow['history'], ['What is an Alias?', 'An optional second name.'])
            self.assertEqual(follow['_review']['bad_answer'], 'Yes.')

            # Never overwrites
            from django.core.management.base import CommandError
            with self.assertRaises(CommandError):
                self.stats('--export-evals', path)

    @override_settings(GEMINI_API_KEY='test-key')
    def test_eval_refuses_a_case_with_nothing_to_check(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from assistant.management.commands import assistant_eval
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'evals.json'
            path.write_text(json.dumps([
                {'question': 'Checked', 'must_include': ['x']},
                {'question': 'Unfinished draft', 'must_include': [], 'must_not_include': []},
            ]))
            with patch.object(assistant_eval, 'EVALS_PATH', path), \
                    patch('assistant.llm.requests.post') as mock_post:
                with self.assertRaisesMessage(CommandError, 'Unfinished draft'):
                    call_command('assistant_eval', stdout=open(os.devnull, 'w'))
                mock_post.assert_not_called()  # refused before spending any quota

    def test_purge_keeps_rated_threads_longer(self):
        from datetime import timedelta
        from io import StringIO
        from django.core.management import call_command

        def idle(conv, days):
            Conversation.objects.filter(pk=conv.pk).update(updated_at=self.now - timedelta(days=days))

        unrated, _ = self.thread('q', 'a')
        rated_recent, _ = self.thread('q', 'a', rating=-1)
        rated_ancient, _ = self.thread('q', 'a', rating=1)
        fresh, _ = self.thread('q', 'a')
        idle(unrated, 120)
        idle(rated_recent, 120)
        idle(rated_ancient, 400)
        idle(fresh, 10)

        out = StringIO()
        call_command('assistant_purge', stdout=out)
        self.assertIn('2 conversation(s)', out.getvalue())
        self.assertIn('1 idle conversation(s) with ratings are kept', out.getvalue())

        call_command('assistant_purge', '--delete', stdout=StringIO())
        remaining = set(Conversation.objects.values_list('pk', flat=True))
        self.assertEqual(remaining, {rated_recent.pk, fresh.pk})

        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            call_command('assistant_purge', '--days', '90', '--rated-days', '30', stdout=StringIO())
