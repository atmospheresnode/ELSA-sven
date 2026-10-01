# -*- coding: utf-8 -*-
"""The disk-space check before a NetCDF upload, and the report a user sends when it fails.

The server's free space is never real in these tests: shutil.disk_usage is patched, so a test can
say "the disk has 1 GB left" without filling one. Gemini is never called either; requests.post is
patched where the draft is under test, and GEMINI_API_KEY is blanked everywhere else.
"""
from __future__ import unicode_literals

import json
import os
import shutil
import tempfile
from collections import namedtuple
from unittest import mock

from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from build import storage_report
from build.models import AdditionalCollections, Bundle, Investigation, NetCDFFile

GB = 1000 ** 3
Usage = namedtuple('Usage', 'total used free')


def free_space(free):
    """Patch every filesystem to report `free` bytes free."""
    return mock.patch('build.storage_report.shutil.disk_usage',
                      return_value=Usage(10 * free + 1, 0, free))


def gemini_reply(text, status=200, finish='STOP'):
    resp = mock.Mock(status_code=status)
    resp.json.return_value = {'candidates': [{
        'content': {'parts': [{'text': text}]}, 'finishReason': finish}]}
    return resp


class CheckSpaceTests(SimpleTestCase):

    def test_an_upload_that_fits_with_the_margin_is_accepted(self):
        with free_space(10 * GB):
            space = storage_report.check_space(1 * GB)
        self.assertTrue(space['ok'])
        self.assertEqual(space['available'], 10 * GB - storage_report.STORAGE_MARGIN)

    def test_the_margin_is_kept_free(self):
        # 3 GB free and a 2 GiB margin leaves under 1 GB usable, so 1 GB does not fit.
        with free_space(3 * GB):
            self.assertFalse(storage_report.check_space(1 * GB)['ok'])

    def test_a_nearly_full_disk_reports_nothing_available_rather_than_a_negative(self):
        with free_space(1 * GB):
            space = storage_report.check_space(1)
        self.assertFalse(space['ok'])
        self.assertEqual(space['available'], 0)

    def test_the_tightest_filesystem_decides(self):
        # The temp directory is where Django buffers the upload, so a full /tmp refuses it even
        # when the archive disk has plenty of room.
        temp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp, True)

        def usage(path):
            return Usage(0, 0, 1 * GB if path == temp else 100 * GB)

        with override_settings(FILE_UPLOAD_TEMP_DIR=temp), \
                mock.patch('build.storage_report.os.stat') as stat, \
                mock.patch('build.storage_report.shutil.disk_usage', side_effect=usage):
            # Every path its own filesystem, as /tmp and /home are on the server.
            stat.side_effect = lambda path: mock.Mock(st_dev=hash(path))
            self.assertFalse(storage_report.check_space(10)['ok'])

    def test_a_path_that_cannot_be_inspected_does_not_block_uploads(self):
        with mock.patch('build.storage_report.os.stat', side_effect=OSError('gone')):
            space = storage_report.check_space(50 * GB)
        self.assertTrue(space['ok'])
        self.assertIsNone(space['available'])


class HelperTests(SimpleTestCase):

    def test_human_size(self):
        self.assertEqual(storage_report.human_size(512), '512 bytes')
        self.assertEqual(storage_report.human_size(1500), '1.5 KB')
        self.assertEqual(storage_report.human_size(2 * GB), '2.0 GB')
        self.assertEqual(storage_report.human_size(None), 'unknown')

    def test_clean_files_keeps_only_names_and_sizes(self):
        files = storage_report.clean_files([
            {'name': '../../etc/passwd', 'size': '12'},
            {'name': 'ok.nc', 'size': 'lots'},
            {'name': '', 'size': 1},
            'not a dict',
        ])
        self.assertEqual(files, [{'name': 'passwd', 'size': 12}, {'name': 'ok.nc', 'size': 0}])
        self.assertEqual(storage_report.clean_files('nope'), [])

    def test_tidy_removes_dashes_and_markdown(self):
        self.assertEqual(storage_report._tidy('**Hi** there \u2014 now 1\u20132'),
                         'Hi there, now 1-2')


@override_settings(GEMINI_API_KEY='')
class StorageViewTests(TestCase):

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.archive = tempfile.mkdtemp(prefix='elsa-storage-')
        self.addCleanup(shutil.rmtree, self.archive, True)
        patcher = override_settings(ARCHIVE_DIR=self.archive)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.user = User.objects.create_user('uploader', password='pw', email='u@example.com',
                                             first_name='Uma', last_name='Loader')
        self.bundle = Bundle.objects.create(name='storage bundle', user=self.user,
                                           version='1O00', bundle_type='External')
        self.client.login(username='uploader', password='pw')
        self.files = [{'name': 'big.nc', 'size': 5 * GB}, {'name': 'bigger.nc', 'size': 7 * GB}]

    def url(self, name):
        return reverse('build:' + name, kwargs={'pk_bundle': self.bundle.pk})

    def post(self, name, **body):
        body.setdefault('collection', 'model_output')
        body.setdefault('files', self.files)
        return self.client.post(self.url(name), json.dumps(body), content_type='application/json')

    def test_check_refuses_an_upload_the_disk_cannot_hold(self):
        with free_space(4 * GB):
            data = self.post('netcdf_storage_check').json()
        self.assertFalse(data['ok'])
        self.assertEqual(data['needed'], '12.0 GB')
        self.assertEqual(data['available_bytes'], 4 * GB - storage_report.STORAGE_MARGIN)

    def test_check_accepts_an_upload_that_fits(self):
        with free_space(100 * GB):
            self.assertTrue(self.post('netcdf_storage_check').json()['ok'])

    def test_someone_elses_bundle_is_refused(self):
        User.objects.create_user('stranger', password='pw')
        self.client.login(username='stranger', password='pw')
        for name in ('netcdf_storage_check', 'netcdf_storage_draft', 'netcdf_storage_report'):
            self.assertEqual(self.post(name, message='hi').status_code, 403, name)
        self.assertEqual(len(mail.outbox), 0)

    def test_get_is_refused(self):
        self.assertEqual(self.client.get(self.url('netcdf_storage_check')).status_code, 405)

    def test_draft_falls_back_to_the_template_without_gemini(self):
        data = self.post('netcdf_storage_draft').json()
        self.assertEqual(data['source'], 'template')
        for expected in ('storage bundle', 'model_output', 'big.nc (5.0 GB)',
                         'bigger.nc (7.0 GB)', '12.0 GB', 'Uma Loader (uploader)'):
            self.assertIn(expected, data['message'])
        self.assertNotIn('\u2014', data['message'])

    def test_an_account_with_no_name_signs_with_the_username_once(self):
        self.user.first_name = self.user.last_name = ''
        self.user.save()
        message = self.post('netcdf_storage_draft').json()['message']
        self.assertTrue(message.endswith('Thank you,\nuploader'), message)

    def test_report_emails_staff_with_details_the_user_cannot_edit(self):
        with free_space(4 * GB):
            response = self.post('netcdf_storage_report', message='Help <b>please</b>')
        self.assertEqual(response.json(), {'sent': True, 'email': 'u@example.com'})
        self.assertEqual(len(mail.outbox), 2)
        staff, confirmation = mail.outbox
        self.assertEqual(staff.to, storage_report.STAFF_RECIPIENTS)
        self.assertEqual(staff.from_email, 'atm-elsa@nmsu.edu')
        self.assertEqual(staff.reply_to, ['u@example.com'])
        self.assertIn('uploader', staff.subject)
        self.assertIn('Help &lt;b&gt;please&lt;/b&gt;', staff.body)
        self.assertIn('storage bundle', staff.body)
        self.assertIn('bigger.nc (7.0 GB)', staff.body)
        # Measured on the server at send time, not taken from the page.
        self.assertIn(storage_report.human_size(4 * GB - storage_report.STORAGE_MARGIN), staff.body)
        self.assertNotIn('&mdash;', staff.body)
        self.assertEqual(confirmation.to, ['u@example.com'])

    def test_an_empty_message_is_not_sent(self):
        self.assertEqual(self.post('netcdf_storage_report', message='   ').status_code, 400)
        self.assertEqual(len(mail.outbox), 0)

    def test_sends_are_capped_per_hour(self):
        for _ in range(storage_report.SEND_LIMIT_PER_HOUR):
            self.assertEqual(self.post('netcdf_storage_report', message='hi').status_code, 200)
        self.assertEqual(self.post('netcdf_storage_report', message='hi').status_code, 429)


COMPLETE_DRAFT = ('Hello Team ELSA,\n\nMy upload of "a.nc" to "mars bundle", collection '
                  '"model_output", was refused for lack of space.\n\nThank you,\ndrafter')


class GeminiDraftTests(TestCase):

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = User.objects.create_user('drafter', password='pw')
        self.bundle = mock.Mock(name='bundle', bundle_type='External', pk=1)
        self.bundle.name = 'mars bundle'
        self.files = [{'name': 'a.nc', 'size': GB}]

    def draft(self):
        return storage_report.draft_message(self.user, self.bundle, 'model_output', self.files)

    @override_settings(GEMINI_API_KEY='key')
    def test_a_gemini_draft_is_used_and_tidied(self):
        text = ('Hello Team ELSA, my upload of "a.nc" \u2014 1.0 GB \u2014 to "mars bundle", '
                'collection "model_output", was refused.\n\nThank you,\ndrafter')
        with mock.patch('build.storage_report.requests.post', return_value=gemini_reply(text)) as post:
            message, source = self.draft()
        self.assertEqual(source, 'ai')
        self.assertNotIn('\u2014', message)
        self.assertIn('"a.nc", 1.0 GB, to "mars bundle"', message)
        prompt = post.call_args.kwargs['json']['contents'][0]['parts'][0]['text']
        self.assertIn('mars bundle', prompt)
        self.assertIn('a.nc (1.0 GB)', prompt)

    @override_settings(GEMINI_API_KEY='key')
    def test_a_draft_that_rewords_an_identifier_is_not_used(self):
        # What gemini-2.5-flash-lite did five times out of five before the prompt said otherwise.
        reworded = COMPLETE_DRAFT.replace('model_output', 'model output')
        with mock.patch('build.storage_report.requests.post',
                        return_value=gemini_reply(reworded)) as post:
            message, source = self.draft()
        self.assertEqual(post.call_count, len(storage_report.GEMINI_MODELS))
        self.assertEqual(source, 'template')
        self.assertIn('"model_output"', message)

    @override_settings(GEMINI_API_KEY='key')
    def test_a_long_file_list_only_has_to_name_the_first_file(self):
        self.files = [{'name': 'run_{:02d}.nc'.format(i), 'size': 1} for i in range(12)]
        text = COMPLETE_DRAFT.replace('"a.nc"', '"run_00.nc" and 11 more files')
        with mock.patch('build.storage_report.requests.post', return_value=gemini_reply(text)):
            self.assertEqual(self.draft()[1], 'ai')

    @override_settings(GEMINI_API_KEY='key')
    def test_a_failing_model_falls_through_to_the_next_then_the_template(self):
        with mock.patch('build.storage_report.requests.post',
                        return_value=gemini_reply('', status=429)) as post:
            message, source = self.draft()
        self.assertEqual(post.call_count, len(storage_report.GEMINI_MODELS))
        self.assertEqual(source, 'template')
        self.assertIn('mars bundle', message)

    @override_settings(GEMINI_API_KEY='key')
    def test_a_slow_chain_gives_up_within_the_budget(self):
        # The first model eats the whole budget; the second is never tried.
        clock = iter([0, 0, storage_report.GEMINI_BUDGET + 1])
        with mock.patch('build.storage_report.time.monotonic', side_effect=lambda: next(clock)), \
                mock.patch('build.storage_report.requests.post',
                           side_effect=storage_report.requests.Timeout) as post:
            message, source = self.draft()
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.kwargs['timeout'],
                         (storage_report.GEMINI_CONNECT_TIMEOUT, storage_report.GEMINI_READ_TIMEOUT))
        self.assertEqual(source, 'template')

    @override_settings(GEMINI_API_KEY='key')
    def test_a_truncated_reply_is_not_used(self):
        reply = gemini_reply('Hello Team ELSA, I tried to upload a file and it', finish='MAX_TOKENS')
        with mock.patch('build.storage_report.requests.post', return_value=reply):
            self.assertEqual(self.draft()[1], 'template')

    @override_settings(GEMINI_API_KEY='key')
    def test_the_assistant_kill_switch_also_stops_drafts(self):
        cache.set('assistant-disabled', True)
        with mock.patch('build.storage_report.requests.post') as post:
            self.assertEqual(self.draft()[1], 'template')
        post.assert_not_called()

    @override_settings(GEMINI_API_KEY='key')
    def test_drafts_past_the_hourly_cap_use_the_template(self):
        text = COMPLETE_DRAFT
        with mock.patch('build.storage_report.requests.post', return_value=gemini_reply(text)) as post:
            for _ in range(storage_report.DRAFT_LIMIT_PER_HOUR):
                self.assertEqual(self.draft()[1], 'ai')
            self.assertEqual(self.draft()[1], 'template')
        self.assertEqual(post.call_count, storage_report.DRAFT_LIMIT_PER_HOUR)


class UploadBackstopTests(TestCase):
    """The bundle view's own check, for an upload that arrives despite the preflight."""

    def setUp(self):
        self.archive = tempfile.mkdtemp(prefix='elsa-storage-up-')
        self.addCleanup(shutil.rmtree, self.archive, True)
        self.media = tempfile.mkdtemp(prefix='elsa-storage-up-media-')
        self.addCleanup(shutil.rmtree, self.media, True)
        patcher = override_settings(ARCHIVE_DIR=self.archive, MEDIA_ROOT=self.media)
        patcher.enable()
        self.addCleanup(patcher.disable)

        User.objects.create_user('backstop', password='pw')
        self.client.login(username='backstop', password='pw')
        Investigation.objects.create(
            name='Atmospheric Modeling Annex', type_of='Individual Investigation',
            lid='urn:nasa:pds:context:investigation:individual.atmospheric_modeling_annex',
            file_ref='')
        self.client.post(reverse('build:build'), {
            'name': 'backstop bundle', 'bundle_type': 'External', 'version': '1O00', 'bundleID': ''})
        self.bundle = Bundle.objects.get(name='backstop bundle')
        self.collection = AdditionalCollections.objects.create(
            bundle=self.bundle, collection_name='alpha', collection_type='External')
        os.makedirs(self.collection.directory(), exist_ok=True)

    def test_an_upload_the_disk_cannot_hold_is_refused_with_a_code_the_page_understands(self):
        with free_space(1 * GB):
            response = self.client.post(
                reverse('build:bundle', kwargs={'pk_bundle': self.bundle.pk}),
                {'collection': 'alpha', 'netcdf_files': SimpleUploadedFile('x.nc', b'CDF\x01' + b'\0' * 64)},
                HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 507)
        self.assertEqual(response.json()['code'], 'insufficient_storage')
        self.assertEqual(response.json()['available_bytes'], 0)
        self.assertFalse(NetCDFFile.objects.filter(bundle=self.bundle).exists())

    def test_the_bundle_page_carries_the_storage_dialog(self):
        body = self.client.get(reverse('build:bundle', kwargs={'pk_bundle': self.bundle.pk})).content.decode()
        self.assertIn('id="netcdfStorageModal"', body)
        self.assertIn(reverse('build:netcdf_storage_check', kwargs={'pk_bundle': self.bundle.pk}), body)
