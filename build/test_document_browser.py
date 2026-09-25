# -*- coding: utf-8 -*-
"""Uploading a document in a real browser, for both bundle types.

The server-side tests post files straight at the views. These cover what only a
browser shows: that the Documents window really sends the file (a form without
multipart/form-data sends only its name, and the upload silently becomes "no
file"), that the Archive page's first #document_modal is the one that opens, and
that a refused file comes back to the user with its reason.

Playwright is a development-only dependency; without it every test here skips.

Run with:
    python manage.py test build.test_document_browser --settings=test_settings
"""

from __future__ import unicode_literals

import os
import shutil
import tempfile

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', '1')

from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse

from build import document_files
from build.corpus.builder import DOCUMENT_FIXTURES
from build.models import Bundle, Investigation, Product_Document

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - dev-only dependency
    sync_playwright = None


def fixture(name):
    return os.path.join(DOCUMENT_FIXTURES, name)


@override_settings(ALLOWED_HOSTS=['*'])
class DocumentUploadBrowserTests(StaticLiveServerTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.playwright = None
        cls.browser = None
        if sync_playwright is None:
            return
        try:
            cls.playwright = sync_playwright().start()
            cls.browser = cls.playwright.chromium.launch()
        except Exception as exc:  # pragma: no cover - environment dependent
            cls.browser = None
            cls.launch_error = exc

    @classmethod
    def tearDownClass(cls):
        if cls.browser is not None:
            cls.browser.close()
        if cls.playwright is not None:
            cls.playwright.stop()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        if self.browser is None:
            self.skipTest('Playwright browser unavailable: {}'.format(
                getattr(self, 'launch_error', 'playwright not installed')))

        self.archive_dir = tempfile.mkdtemp(prefix='elsa-doc-browser-')
        self.addCleanup(shutil.rmtree, self.archive_dir, True)
        self.media_root = tempfile.mkdtemp(prefix='elsa-doc-browser-media-')
        self.addCleanup(shutil.rmtree, self.media_root, True)
        patcher = override_settings(ARCHIVE_DIR=self.archive_dir, MEDIA_ROOT=self.media_root)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.user = User.objects.create_user('docbrowser', password='pw-for-tests')
        Investigation.objects.create(
            name='Atmospheric Modeling Annex', type_of='Individual Investigation',
            lid='urn:nasa:pds:context:investigation:individual.atmospheric_modeling_annex',
            file_ref='')
        self.client.login(username='docbrowser', password='pw-for-tests')

        self.context = self.browser.new_context()
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.set_default_timeout(15000)
        self.page.goto(self.live_server_url + '/static/css/styles.css')
        self.context.add_cookies([{
            'name': 'sessionid', 'value': self.client.cookies['sessionid'].value,
            'url': self.live_server_url}])

    def make(self, bundle_type):
        name = 'browser {}'.format(bundle_type.lower())
        self.client.post(reverse('build:build'), {
            'name': name, 'bundle_type': bundle_type, 'version': '1O00', 'bundleID': ''})
        return Bundle.objects.get(name=name)

    def open_documents_window(self, bundle):
        self.page.goto(self.live_server_url + reverse('build:bundle', args=[bundle.pk]))
        self.page.locator(
            'button[data-bs-target="#document_modal"]').locator('visible=true').first.click()
        modal = self.page.locator('.modal.show')
        modal.wait_for(state='visible')
        return modal

    def submit(self, modal):
        with self.page.expect_navigation():
            modal.locator('button[type="submit"]').click()

    # -- External --------------------------------------------------------------

    def fill_external(self, modal, name, path):
        modal.locator('input[name="document_name"]').fill(name)
        modal.locator('input[name="document_id"]').fill(name)
        modal.locator('input[name="document_file"]').set_input_files(path)

    def test_external_window_uploads_the_file(self):
        bundle = self.make('External')
        modal = self.open_documents_window(bundle)
        self.assertIn('.pdf', modal.locator('input[name="document_file"]').get_attribute('accept'))
        self.fill_external(modal, 'guide', fixture('guide_pdfa1b.pdf'))
        self.submit(modal)

        document = Product_Document.objects.get(bundle=bundle)
        self.assertEqual(document.file_name, 'guide_pdfa1b.pdf')
        self.assertTrue(document_files.has_file(document))

    def test_external_window_says_what_files_it_takes(self):
        bundle = self.make('External')
        modal = self.open_documents_window(bundle)
        self.assertIn('PDF/A-1', modal.inner_text())

    def test_external_refused_file_comes_back_with_its_reason(self):
        bundle = self.make('External')
        modal = self.open_documents_window(bundle)
        self.fill_external(modal, 'guide', fixture('guide_plain.pdf'))
        self.submit(modal)
        self.assertFalse(Product_Document.objects.filter(bundle=bundle).exists())
        self.assertIn('ordinary PDF', self.page.content())

    # -- Archive ---------------------------------------------------------------

    def fill_archive(self, modal, name, path):
        modal.locator('input[name="document_name"]').fill(name)
        modal.locator('input[name="publication_date"]').fill('2026-09-25')
        modal.locator('input[name="document_file"]').set_input_files(path)

    def test_archive_window_uploads_the_file(self):
        bundle = self.make('Archive')
        modal = self.open_documents_window(bundle)
        self.fill_archive(modal, 'guide', fixture('guide.txt'))
        self.submit(modal)

        document = Product_Document.objects.get(bundle=bundle)
        self.assertEqual(document.file_name, 'guide.txt')
        self.assertEqual(document.document_std_id, '7-Bit ASCII Text')
        self.assertTrue(document_files.has_file(document))

    def test_archive_refused_file_is_explained(self):
        bundle = self.make('Archive')
        modal = self.open_documents_window(bundle)
        self.fill_archive(modal, 'guide', fixture('guide_pdfa2b.pdf'))
        self.submit(modal)
        self.assertFalse(Product_Document.objects.filter(bundle=bundle).exists())
        # Seen, not merely present: the window reopens with the reason in it.
        reopened = self.page.locator('#document_modal.show')
        reopened.wait_for(state='visible')
        self.assertIn('PDF/A-2', reopened.inner_text())

    def test_archive_window_stays_shut_on_an_ordinary_visit(self):
        bundle = self.make('Archive')
        self.page.goto(self.live_server_url + reverse('build:bundle', args=[bundle.pk]))
        self.page.wait_for_load_state('load')
        self.page.wait_for_timeout(500)
        self.assertEqual(self.page.locator('.modal.show').count(), 0)

    # -- the edit page -----------------------------------------------------------

    def test_edit_page_replaces_the_file(self):
        for bundle_type, fill in (('External', self.fill_external),
                                  ('Archive', self.fill_archive)):
            bundle = self.make(bundle_type)
            modal = self.open_documents_window(bundle)
            fill(modal, 'guide', fixture('guide_pdfa1b.pdf'))
            self.submit(modal)
            document = Product_Document.objects.get(bundle=bundle)
            old = document_files.file_path(document)

            self.page.goto(self.live_server_url + reverse(
                'build:product_document', args=[bundle.pk, document.pk]))
            self.page.locator('input[name="document_file"]').set_input_files(
                fixture('guide.txt'))
            with self.page.expect_navigation():
                self.page.locator('form[enctype="multipart/form-data"] '
                                  'button[type="submit"], '
                                  'form[enctype="multipart/form-data"] '
                                  'input[type="submit"]').first.click()

            document.refresh_from_db()
            self.assertEqual(document.file_name, 'guide.txt', bundle_type)
            self.assertTrue(document_files.has_file(document), bundle_type)
            self.assertFalse(os.path.exists(old), bundle_type)
