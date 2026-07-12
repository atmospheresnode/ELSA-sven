import json
import os
import tempfile
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings

from .models import Conversation, Message
from .prompts import _bundle_summary, _page_context, _user_data, build_system_prompt
from .retriever import retrieve
from .views import _send_feedback_email


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
            if isinstance(chunk, dict) and '__finish__' in chunk:
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


class HistoryAndRatingTests(TestCase):

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='tester', password='pw')
        self.client.force_login(self.user)
        self.conv = Conversation.objects.create(user=self.user)
        self.msg_user = self.conv.messages.create(role='user', text='hi')
        self.msg_model = self.conv.messages.create(role='model', text='hello!')

    def test_history_returns_latest_conversation(self):
        resp = self.client.get('/assistant/history/')
        data = resp.json()
        self.assertEqual(data['conversation_id'], self.conv.pk)
        self.assertEqual([m['role'] for m in data['messages']], ['user', 'model'])

    def test_history_empty_for_new_user(self):
        self.client.force_login(User.objects.create_user(username='fresh', password='pw'))
        data = self.client.get('/assistant/history/').json()
        self.assertIsNone(data['conversation_id'])
        self.assertEqual(data['messages'], [])

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


class KnowledgeCheckTests(SimpleTestCase):

    def test_parse_watches(self):
        from .management.commands.assistant_knowledge_check import parse_watches
        text = '<!-- watches: build/views.py, templates/build -->\n# Title\nBody'
        self.assertEqual(parse_watches(text), ['build/views.py', 'templates/build'])
        self.assertEqual(parse_watches('# No declaration'), [])

    def test_parse_reviewed_marker(self):
        import datetime
        from assistant.knowledge_check import parse_reviewed_ts
        ts = parse_reviewed_ts('<!-- watches: a -->\n<!-- reviewed: 2026-07-10 -->\n# T')
        self.assertIsNotNone(ts)
        # counts as end of that day, so it clears same-day commits to watched files
        eod = datetime.datetime.combine(datetime.date(2026, 7, 10), datetime.time(23, 59, 59))
        self.assertEqual(ts, int(eod.timestamp()))
        self.assertIsNone(parse_reviewed_ts('<!-- watches: a -->\n# no marker'))
