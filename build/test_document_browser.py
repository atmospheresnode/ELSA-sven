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

    def both_bundles(self):
        """One bundle of each type, both made before the browser does anything.

        Made inside the loop, the second came from the test thread while the live server was
        still finishing the first bundle's requests in its own threads, and the two collided
        on the shared in-memory database ("database table is locked", about one run in five).
        """
        return [(bundle_type, fill, self.make(bundle_type))
                for bundle_type, fill in (('External', self.fill_external),
                                          ('Archive', self.fill_archive))]

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

    def submit_refused(self, modal):
        """A refused add stays on the page: the window saves in place and shows why."""
        url = self.page.url
        modal.locator('button[type="submit"]').click()
        error = modal.locator('[data-inplace-for="document_file"]')
        error.wait_for(state='visible')
        self.assertEqual(self.page.url, url)
        self.assertTrue(modal.is_visible())
        return error

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
        error = self.submit_refused(modal)
        self.assertFalse(Product_Document.objects.filter(bundle=bundle).exists())
        self.assertIn('ordinary PDF', error.inner_text())

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
        error = self.submit_refused(modal)
        self.assertFalse(Product_Document.objects.filter(bundle=bundle).exists())
        # Seen, not merely present: the window stays open with the reason in it.
        self.assertIn('PDF/A-2', error.inner_text())

    def test_archive_window_stays_shut_on_an_ordinary_visit(self):
        bundle = self.make('Archive')
        self.page.goto(self.live_server_url + reverse('build:bundle', args=[bundle.pk]))
        self.page.wait_for_load_state('load')
        self.page.wait_for_timeout(500)
        self.assertEqual(self.page.locator('.modal.show').count(), 0)

    # -- the edit page -----------------------------------------------------------

    def test_edit_page_replaces_the_file(self):
        for bundle_type, fill, bundle in self.both_bundles():
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

    # -- seeing what was uploaded --------------------------------------------------

    def open_preview(self):
        self.page.locator('button[data-bs-target="#documentPreviewModal"]').locator(
            'visible=true').first.click()
        preview = self.page.locator('#documentPreviewModal.show')
        preview.wait_for(state='visible')
        return preview

    def test_view_shows_the_uploaded_file_on_both_bundle_types(self):
        for bundle_type, fill, bundle in self.both_bundles():
            modal = self.open_documents_window(bundle)
            fill(modal, 'guide', fixture('guide.txt'))
            self.submit(modal)
            document = Product_Document.objects.get(bundle=bundle)

            # Visible on the card without expanding anything.
            preview = self.open_preview()
            self.assertIn('guide.txt', preview.locator('.modal-title').inner_text())
            frame = self.page.frame_locator('#documentPreviewFrame')
            with open(fixture('guide.txt')) as handle:
                first_line = handle.readline().strip()
            frame.locator('body').wait_for(state='attached')
            self.page.wait_for_function(
                "t => { const d = document.getElementById('documentPreviewFrame').contentDocument;"
                " return d && d.body && d.body.innerText.includes(t); }", arg=first_line)
            self.assertEqual(
                preview.locator('#documentPreviewDownload').get_attribute('href'),
                reverse('build:document_file', args=[bundle.pk, document.pk]) + '?download=1',
                bundle_type)

            # Closing unloads the file, so the next View starts clean.
            preview.locator('.btn-close').click()
            # Bootstrap fires hidden after the backdrop fades, so wait for it.
            self.page.wait_for_function(
                "() => document.getElementById('documentPreviewFrame')"
                ".getAttribute('src') === 'about:blank'")

    def test_view_frames_a_pdf_and_it_loads(self):
        bundle = self.make('External')
        modal = self.open_documents_window(bundle)
        self.fill_external(modal, 'guide', fixture('guide_pdfa1b.pdf'))
        with self.page.expect_navigation():
            modal.locator('button[type="submit"]').click()
        document = Product_Document.objects.get(bundle=bundle)
        url = reverse('build:document_file', args=[bundle.pk, document.pk])

        with self.page.expect_response(lambda r: r.url.endswith(url)) as caught:
            self.open_preview()
        response = caught.value
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers['content-type'], 'application/pdf')
        self.assertEqual(response.headers['x-frame-options'], 'SAMEORIGIN')

    def test_the_edit_page_shows_the_current_file(self):
        bundle = self.make('Archive')
        modal = self.open_documents_window(bundle)
        self.fill_archive(modal, 'guide', fixture('guide.txt'))
        self.submit(modal)
        document = Product_Document.objects.get(bundle=bundle)
        self.page.goto(self.live_server_url + reverse(
            'build:product_document', args=[bundle.pk, document.pk]))
        preview = self.open_preview()
        self.assertIn('guide.txt', preview.locator('.modal-title').inner_text())

    # -- seeing a file before it is uploaded -----------------------------------------

    def picker(self, scope):
        box = scope.locator('.doc-pick')
        box.wait_for(state='visible')
        status = box.locator('.doc-pick-status')
        self.page.wait_for_function(
            "el => !el.classList.contains('is-checking') && el.className.includes('is-')",
            arg=status.element_handle())
        return box, status

    def test_a_chosen_file_is_checked_and_previewed_before_upload(self):
        for bundle_type, fill, bundle in self.both_bundles():
            modal = self.open_documents_window(bundle)
            fill(modal, 'guide', fixture('guide_pdfa1b.pdf'))
            box, status = self.picker(modal)
            self.assertIn('is-ok', status.get_attribute('class'), bundle_type)
            self.assertIn('PDF/A-1', status.inner_text())
            self.assertIn('guide_pdfa1b.pdf', box.inner_text())
            box.locator('button', has_text='Preview').click()
            frame = box.locator('iframe')
            frame.wait_for(state='visible')
            self.assertTrue(frame.get_attribute('src').startswith('blob:'))
            self.assertFalse(Product_Document.objects.filter(bundle=bundle).exists())

            # Nothing in the browser stops the upload itself.
            self.submit(modal)
            self.assertTrue(Product_Document.objects.filter(bundle=bundle).exists(), bundle_type)

    def test_files_that_will_be_refused_say_so_before_upload(self):
        bundle = self.make('External')
        modal = self.open_documents_window(bundle)
        file_input = modal.locator('input[name="document_file"]')
        for name, says in (('guide_plain.pdf', 'ordinary PDF'),
                           ('guide_pdfa2b.pdf', 'PDF/A-2')):
            file_input.set_input_files(fixture(name))
            _, status = self.picker(modal)
            self.assertIn('is-bad', status.get_attribute('class'), name)
            self.assertIn(says, status.inner_text(), name)

        for payload, says in (
                ({'name': 'notes.docx', 'mimeType': 'application/octet-stream',
                  'buffer': b'PK'}, 'neither'),
                ({'name': 'latin.txt', 'mimeType': 'text/plain',
                  'buffer': 'café'.encode('latin-1')}, 'not UTF-8'),
                ({'name': 'fake.pdf', 'mimeType': 'application/pdf',
                  'buffer': b'hello'}, 'not a PDF')):
            file_input.set_input_files(payload)
            _, status = self.picker(modal)
            self.assertIn('is-bad', status.get_attribute('class'), payload['name'])
            self.assertIn(says, status.inner_text(), payload['name'])

    def test_text_is_shown_and_the_saved_name_is_told(self):
        bundle = self.make('Archive')
        modal = self.open_documents_window(bundle)
        with open(fixture('guide.txt'), 'rb') as handle:
            data = handle.read()
        modal.locator('input[name="document_file"]').set_input_files(
            {'name': 'User Guide été.txt', 'mimeType': 'text/plain', 'buffer': data})
        box, status = self.picker(modal)
        self.assertIn('is-ok', status.get_attribute('class'))
        self.assertIn('saved as User_Guide_ete.txt', box.inner_text())
        box.locator('button', has_text='Preview').click()
        self.assertIn(data.decode('ascii').splitlines()[0], box.locator('pre').inner_text())
        self.assertEqual(document_files.clean_file_name('User Guide été.txt'),
                         'User_Guide_ete.txt')

    def test_remove_clears_the_choice(self):
        bundle = self.make('External')
        modal = self.open_documents_window(bundle)
        file_input = modal.locator('input[name="document_file"]')
        file_input.set_input_files(fixture('guide.txt'))
        box, _ = self.picker(modal)
        box.locator('button[aria-label="Remove this file"]').click()
        box.wait_for(state='hidden')
        self.assertEqual(file_input.evaluate('el => el.files.length'), 0)

    def test_the_edit_page_previews_a_replacement(self):
        bundle = self.make('External')
        modal = self.open_documents_window(bundle)
        self.fill_external(modal, 'guide', fixture('guide_pdfa1b.pdf'))
        self.submit(modal)
        document = Product_Document.objects.get(bundle=bundle)
        self.page.goto(self.live_server_url + reverse(
            'build:product_document', args=[bundle.pk, document.pk]))
        self.page.locator('input[name="document_file"]').set_input_files(fixture('guide.txt'))
        _, status = self.picker(self.page.locator('#form_product_document'))
        self.assertIn('is-ok', status.get_attribute('class'))

    # -- the list --------------------------------------------------------------------

    def test_delete_from_the_list_asks_then_removes_the_right_one(self):
        for bundle_type, fill, bundle in self.both_bundles():
            for name, path in (('guide', 'guide_pdfa1b.pdf'), ('notes', 'guide.txt')):
                modal = self.open_documents_window(bundle)
                fill(modal, name, fixture(path))
                self.submit(modal)
            notes = Product_Document.objects.get(bundle=bundle, document_name='notes')

            self.page.locator('button[aria-label="Delete notes"]').click()
            confirm = self.page.locator('#documentDeleteModal.show')
            confirm.wait_for(state='visible')
            self.assertIn('notes', confirm.inner_text())
            with self.page.expect_navigation():
                confirm.locator('button[type="submit"]').click()

            names = list(Product_Document.objects.filter(bundle=bundle)
                         .values_list('document_name', flat=True))
            self.assertEqual(names, ['guide'], bundle_type)
            self.assertFalse(os.path.exists(document_files.file_path(notes)), bundle_type)
