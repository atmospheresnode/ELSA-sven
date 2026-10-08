# -*- coding: utf-8 -*-
"""A document is uploaded with its file, and that file is what the label names.

Before this, both document forms took a typed file name and nothing else, so every
document in every bundle named a file that did not exist and validate reported
"URI reference does not exist" for each one. The rules on what is accepted are
PDS's (PDF/A-1 or UTF-8 text); see build/document_files.py.

Driven through the real views and read back off disk, for both bundle types, since
Archive and External documents take different forms and write different labels.
"""

from __future__ import unicode_literals

import os
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET

from django import forms as django_forms
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from build import document_files, validate_rules, validate_runner
from build.corpus import runner
from build.corpus.builder import DOCUMENT_FIXTURES, document_upload
from build.forms import AnnexProductDocumentForm, ProductDocumentForm
from build.models import Bundle, Investigation, Product_Document, pds_document_standard

NS = {'pds': 'http://pds.nasa.gov/pds4/pds/v1'}


def fixture_bytes(name):
    with open(os.path.join(DOCUMENT_FIXTURES, name), 'rb') as handle:
        return handle.read()


def upload(name, data):
    return SimpleUploadedFile(name, data)


# -- what is accepted -------------------------------------------------------------

class FileNameTests(TestCase):
    """The uploaded file's own name becomes the PDS file name."""

    def test_a_legal_name_is_kept(self):
        self.assertEqual(document_files.clean_file_name('User_Guide.pdf'), 'User_Guide.pdf')

    def test_spaces_become_underscores(self):
        self.assertEqual(document_files.clean_file_name('User Guide v2.pdf'),
                         'User_Guide_v2.pdf')

    def test_accents_are_dropped_not_refused(self):
        self.assertEqual(document_files.clean_file_name('Résumé.txt'), 'Resume.txt')

    def test_the_extension_is_lower_cased(self):
        self.assertEqual(document_files.clean_file_name('GUIDE.PDF'), 'GUIDE.pdf')

    def test_a_leading_dot_or_underscore_is_stripped(self):
        self.assertEqual(document_files.clean_file_name('_guide.pdf'), 'guide.pdf')
        self.assertEqual(document_files.clean_file_name('.hidden.txt'), 'hidden.txt')

    def test_a_path_is_reduced_to_its_file_name(self):
        """Old browsers sent the full client path."""
        self.assertEqual(document_files.clean_file_name('C:/Users/me/guide.pdf'), 'guide.pdf')

    def test_other_formats_are_refused_with_the_way_forward(self):
        for name in ('guide.docx', 'guide.md', 'guide.html', 'guide', 'guide.pdf.exe'):
            with self.assertRaises(django_forms.ValidationError) as raised:
                document_files.clean_file_name(name)
            self.assertIn('PDF/A-1b', raised.exception.messages[0], name)

    def test_a_name_with_nothing_legal_left_is_refused(self):
        with self.assertRaises(django_forms.ValidationError):
            document_files.clean_file_name('日本語.pdf')


class ContentTests(TestCase):
    """PDF/A-1 or UTF-8 text, judged by the bytes, not the extension."""

    def prepare(self, name, data):
        return document_files.prepare(upload(name, data))

    def refused(self, name, data):
        with self.assertRaises(django_forms.ValidationError) as raised:
            self.prepare(name, data)
        return raised.exception.messages[0]

    def test_pdfa_1b_is_accepted_as_pdfa(self):
        prepared = self.prepare('guide.pdf', fixture_bytes('guide_pdfa1b.pdf'))
        self.assertEqual(prepared.standard, 'PDF/A')
        self.assertEqual(prepared.file_name, 'guide.pdf')

    def test_pdfa_2b_is_refused_by_name(self):
        """A real archival PDF, but PDS and validate only accept part 1."""
        message = self.refused('guide.pdf', fixture_bytes('guide_pdfa2b.pdf'))
        self.assertIn('PDF/A-2', message)
        self.assertIn('Word', message)

    def test_an_ordinary_pdf_is_refused_with_how_to_export_one(self):
        message = self.refused('guide.pdf', fixture_bytes('guide_plain.pdf'))
        self.assertIn('ordinary PDF', message)
        self.assertIn('LibreOffice', message)

    def test_something_named_pdf_that_is_not_one_is_refused(self):
        self.assertIn('not a PDF', self.refused('guide.pdf', b'hello, not a pdf'))

    def test_an_empty_file_is_refused(self):
        self.assertIn('empty', self.refused('guide.txt', b''))

    @override_settings(DOCUMENT_MAX_UPLOAD_MB=0.00001)
    def test_a_file_over_the_limit_is_refused(self):
        self.assertIn('larger than', self.refused('guide.txt', b'x' * 100))

    def test_ascii_text_is_seven_bit_ascii(self):
        prepared = self.prepare('readme.txt', fixture_bytes('guide.txt'))
        self.assertEqual(prepared.standard, '7-Bit ASCII Text')

    def test_utf8_text_is_utf8(self):
        prepared = self.prepare('readme.txt', 'Température en kelvin\n'.encode('utf-8'))
        self.assertEqual(prepared.standard, 'UTF-8 Text')

    def test_text_in_another_encoding_is_refused(self):
        self.assertIn('not UTF-8',
                      self.refused('readme.txt', 'Température\n'.encode('latin-1')))

    def test_the_pdfa_claim_is_read_in_either_xmp_form(self):
        self.assertEqual(document_files.pdfa_part(b"<x pdfaid:part='1'/>"), 1)
        self.assertEqual(document_files.pdfa_part(b'<pdfaid:part>2</pdfaid:part>'), 2)
        self.assertIsNone(document_files.pdfa_part(b'%PDF-1.4 nothing here'))

    def test_the_upload_can_still_be_read_after_checking(self):
        """prepare() reads the whole file; store() has to read it again."""
        uploaded = upload('guide.txt', fixture_bytes('guide.txt'))
        document_files.prepare(uploaded)
        self.assertEqual(b''.join(uploaded.chunks()), fixture_bytes('guide.txt'))


class StandardNameTests(TestCase):
    """The old forms stored "ASCII", which is not a PDS4 value."""

    def test_ascii_becomes_the_pds4_name(self):
        self.assertEqual(pds_document_standard('ASCII'), '7-Bit ASCII Text')

    def test_pds4_values_pass_through(self):
        for value in ('PDF/A', 'UTF-8 Text', '7-Bit ASCII Text'):
            self.assertEqual(pds_document_standard(value), value)


# -- the forms --------------------------------------------------------------------

class FormTests(TestCase):

    def annex(self, files=None, **kwargs):
        form = AnnexProductDocumentForm(
            {'document_name': 'guide', 'document_id': 'guide', 'comment': ''},
            files, **kwargs)
        form.is_valid()
        return form

    def archive(self, files=None, **kwargs):
        form = ProductDocumentForm(
            {'document_name': 'guide', 'publication_date': '2026-09-25'}, files, **kwargs)
        form.is_valid()
        return form

    def test_a_new_document_needs_its_file(self):
        for form in (self.annex(), self.archive()):
            self.assertIn('document_file', form.errors)
            self.assertIn('Attach the document itself', form.errors['document_file'][0])

    def test_with_a_file_both_forms_are_valid_and_carry_it(self):
        for build in (self.annex, self.archive):
            form = build({'document_file': document_upload('guide')})
            self.assertEqual(form.errors, {})
            self.assertEqual(form.prepared_file.file_name, 'guide.pdf')

    def test_a_refused_file_is_a_field_error_not_a_crash(self):
        bad = upload('guide.pdf', fixture_bytes('guide_plain.pdf'))
        form = self.annex({'document_file': bad})
        self.assertIn('ordinary PDF', form.errors['document_file'][0])
        self.assertIsNone(form.prepared_file)

    def test_the_typed_name_and_format_fields_are_gone(self):
        """They could disagree with the file; now the file is the answer."""
        for form in (self.annex(), self.archive()):
            self.assertNotIn('file_name', form.fields)
            self.assertNotIn('document_std_id', form.fields)
            self.assertIn('document_file', form.fields)

    def test_the_file_input_only_offers_accepted_types(self):
        widget = AnnexProductDocumentForm().fields['document_file'].widget
        self.assertIn('.pdf', widget.attrs['accept'])
        self.assertIn('.txt', widget.attrs['accept'])


# -- through the views, both bundle types ----------------------------------------

class UploadViewTests(TestCase):

    def setUp(self):
        self.archive = tempfile.mkdtemp(prefix='elsa-docup-')
        self.addCleanup(shutil.rmtree, self.archive, True)
        self.media = tempfile.mkdtemp(prefix='elsa-docup-media-')
        self.addCleanup(shutil.rmtree, self.media, True)
        patcher = override_settings(ARCHIVE_DIR=self.archive, MEDIA_ROOT=self.media)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.user = User.objects.create_user('docup', password='pw')
        self.client.login(username='docup', password='pw')
        Investigation.objects.create(
            name='Atmospheric Modeling Annex', type_of='Individual Investigation',
            lid='urn:nasa:pds:context:investigation:individual.atmospheric_modeling_annex',
            file_ref='')

    def make(self, bundle_type):
        name = 'docup {}'.format(bundle_type.lower())
        self.client.post(reverse('build:build'), {
            'name': name, 'bundle_type': bundle_type, 'version': '1O00', 'bundleID': ''})
        return Bundle.objects.get(name=name)

    # adding, by every route a template posts to

    def add_external(self, bundle, name='guide', document_file=None):
        return self.client.post(
            reverse('build:annex_collection_document', args=[str(bundle.pk)]),
            {'form_name': 'document_form', 'document_name': name, 'document_id': name,
             'comment': 'a document', 'source': 'bundle',
             'document_file': document_file or document_upload(name)})

    def add_archive(self, bundle, name='guide', document_file=None):
        """The Documents window on the Archive bundle page posts to the bundle view."""
        return self.client.post(
            reverse('build:bundle', args=[str(bundle.pk)]),
            {'form_name': 'document_form', 'document_name': name,
             'publication_date': '2026-09-25', 'author_list': 'R. Dey',
             'description': 'A guide', 'revision_id': '1.0', 'document_editions': '1',
             'edition_name': 'First', 'language': 'English',
             'document_file': document_file or document_upload(name)})

    def add_archive_collection_page(self, bundle, name='guide', document_file=None):
        return self.client.post(
            reverse('build:collection_document', args=[str(bundle.pk)]),
            {'document_name': name, 'publication_date': '2026-09-25',
             'document_file': document_file or document_upload(name)})

    def label(self, document):
        return ET.parse(document.label()).getroot()

    def test_external_the_file_is_stored_next_to_its_label(self):
        bundle = self.make('External')
        self.add_external(bundle)
        document = Product_Document.objects.get(bundle=bundle)
        self.assertEqual(document.file_name, 'guide.pdf')
        stored = os.path.join(document.directory(), 'guide.pdf')
        self.assertTrue(os.path.isfile(stored))
        self.assertEqual(os.path.dirname(stored), os.path.dirname(document.label()))
        with open(stored, 'rb') as handle:
            self.assertEqual(handle.read(), fixture_bytes('guide_pdfa1b.pdf'))

    def test_external_the_label_names_the_file_and_its_standard(self):
        bundle = self.make('External')
        self.add_external(bundle, document_file=document_upload('notes', 'guide.txt'))
        root = self.label(Product_Document.objects.get(bundle=bundle))
        area = root.find('pds:File_Area_External', NS)
        self.assertEqual(area.findtext('pds:File/pds:file_name', None, NS), 'notes.txt')
        self.assertEqual(area.findtext('pds:Encoded_External/pds:encoding_standard_id',
                                       None, NS), '7-Bit ASCII Text')

    def test_archive_the_bundle_window_stores_the_file_and_labels_it(self):
        bundle = self.make('Archive')
        self.add_archive(bundle)
        document = Product_Document.objects.get(bundle=bundle)
        self.assertTrue(document_files.has_file(document))
        root = self.label(document)
        files = root.find('.//pds:Document_File', NS)
        self.assertEqual(files.findtext('pds:file_name', None, NS), 'guide.pdf')
        self.assertEqual(files.findtext('pds:document_standard_id', None, NS), 'PDF/A')

    def test_archive_text_is_labelled_with_a_pds4_value(self):
        """"ASCII" used to be written straight into document_standard_id."""
        bundle = self.make('Archive')
        self.add_archive(bundle, document_file=document_upload('notes', 'guide.txt'))
        root = self.label(Product_Document.objects.get(bundle=bundle))
        self.assertEqual(root.findtext('.//pds:Document_File/pds:document_standard_id',
                                       None, NS), '7-Bit ASCII Text')

    def test_archive_the_collection_page_stores_the_file_too(self):
        bundle = self.make('Archive')
        self.add_archive_collection_page(bundle)
        self.assertTrue(document_files.has_file(Product_Document.objects.get(bundle=bundle)))

    def test_without_a_file_no_document_is_created_on_any_route(self):
        for bundle_type, add in (('External', self.add_external),
                                 ('Archive', self.add_archive)):
            bundle = self.make(bundle_type)
            data_without_file = {'form_name': 'document_form', 'document_name': 'guide',
                                 'document_id': 'guide', 'publication_date': '2026-09-25',
                                 'source': 'bundle'}
            url = (reverse('build:annex_collection_document', args=[str(bundle.pk)])
                   if bundle_type == 'External'
                   else reverse('build:bundle', args=[str(bundle.pk)]))
            self.client.post(url, data_without_file)
            self.assertFalse(Product_Document.objects.filter(bundle=bundle).exists(),
                             bundle_type)

    def test_a_refused_file_creates_nothing_and_says_why(self):
        bundle = self.make('External')
        response = self.add_external(
            bundle, document_file=upload('guide.pdf', fixture_bytes('guide_plain.pdf')))
        self.assertFalse(Product_Document.objects.filter(bundle=bundle).exists())
        self.assertContains(response, 'ordinary PDF')

    def test_two_documents_cannot_share_a_file_name(self):
        bundle = self.make('External')
        self.add_external(bundle, name='guide')
        response = self.add_external(bundle, name='second',
                                     document_file=document_upload('GUIDE'))
        self.assertEqual(Product_Document.objects.filter(bundle=bundle).count(), 1)
        self.assertContains(response, 'already uses the file name')

    # editing

    def edit(self, bundle, document, data, document_file=None):
        payload = dict(data)
        if document_file is not None:
            payload['document_file'] = document_file
        return self.client.post(
            reverse('build:product_document', args=[str(bundle.pk), str(document.pk)]),
            payload)

    def external_fields(self, document):
        return {'document_name': document.document_name,
                'document_id': document.document_id, 'comment': document.comment}

    def test_editing_without_a_file_keeps_the_one_it_has(self):
        bundle = self.make('External')
        self.add_external(bundle)
        document = Product_Document.objects.get(bundle=bundle)
        fields = self.external_fields(document)
        fields['comment'] = 'changed'
        self.edit(bundle, document, fields)
        document.refresh_from_db()
        self.assertEqual(document.comment, 'changed')
        self.assertEqual(document.file_name, 'guide.pdf')
        self.assertTrue(document_files.has_file(document))

    def test_a_replacement_under_a_new_name_removes_the_old_file(self):
        bundle = self.make('External')
        self.add_external(bundle)
        document = Product_Document.objects.get(bundle=bundle)
        old = document_files.file_path(document)
        self.edit(bundle, document, self.external_fields(document),
                  document_upload('guide_v2', 'guide.txt'))
        document.refresh_from_db()
        self.assertEqual(document.file_name, 'guide_v2.txt')
        self.assertFalse(os.path.exists(old))
        self.assertTrue(document_files.has_file(document))
        root = self.label(document)
        self.assertEqual(root.findtext('.//pds:File/pds:file_name', None, NS),
                         'guide_v2.txt')
        self.assertEqual(root.findtext('.//pds:encoding_standard_id', None, NS),
                         '7-Bit ASCII Text')

    def test_a_replacement_under_the_same_name_replaces_the_bytes(self):
        bundle = self.make('External')
        self.add_external(bundle, document_file=document_upload('guide', 'guide.txt'))
        document = Product_Document.objects.get(bundle=bundle)
        self.edit(bundle, document, self.external_fields(document),
                  upload('guide.txt', b'Second edition\n'))
        document.refresh_from_db()
        with open(document_files.file_path(document), 'rb') as handle:
            self.assertEqual(handle.read(), b'Second edition\n')
        leftovers = [n for n in os.listdir(document.directory()) if n.startswith('.upload-')]
        self.assertEqual(leftovers, [])

    def test_a_document_saved_before_uploads_can_be_given_its_file(self):
        """The fix a user is sent to by "Attach the file for this document"."""
        bundle = self.make('External')
        self.add_external(bundle)
        document = Product_Document.objects.get(bundle=bundle)
        document_files.remove(document)
        Product_Document.objects.filter(pk=document.pk).update(
            file_name='typed_name.pdf', document_std_id='PDF/A')
        document.refresh_from_db()
        self.assertFalse(document_files.has_file(document))

        self.edit(bundle, document, self.external_fields(document),
                  document_upload('guide'))
        document.refresh_from_db()
        self.assertEqual(document.file_name, 'guide.pdf')
        self.assertTrue(document_files.has_file(document))

    def test_archive_edit_page_accepts_a_replacement(self):
        bundle = self.make('Archive')
        self.add_archive(bundle)
        document = Product_Document.objects.get(bundle=bundle)
        # Every field, as the edit page posts them.
        fields = {name: getattr(document, name) for name in (
            'document_name', 'publication_date', 'author_list', 'copyright',
            'description', 'revision_id', 'document_editions', 'edition_name',
            'language', 'local_id')}
        self.edit(bundle, document, fields, document_upload('guide_v2'))
        document.refresh_from_db()
        self.assertEqual(document.file_name, 'guide_v2.pdf')
        self.assertTrue(document_files.has_file(document))
        self.assertFalse(os.path.exists(os.path.join(document.directory(), 'guide.pdf')))

    def test_the_edit_page_offers_the_file_input(self):
        for bundle_type, add in (('External', self.add_external),
                                 ('Archive', self.add_archive)):
            bundle = self.make(bundle_type)
            add(bundle)
            document = Product_Document.objects.get(bundle=bundle)
            page = self.client.get(
                reverse('build:product_document', args=[str(bundle.pk), str(document.pk)]))
            body = page.content.decode()
            self.assertIn('name="document_file"', body, bundle_type)
            self.assertIn('multipart/form-data', body, bundle_type)

    def test_the_bundle_page_documents_window_is_multipart(self):
        for bundle_type in ('External', 'Archive'):
            bundle = self.make(bundle_type)
            body = self.client.get(reverse('build:bundle', args=[str(bundle.pk)])).content.decode()
            self.assertIn('name="document_file"', body, bundle_type)
            for chunk in body.split('id="form_document"')[1:]:
                self.assertIn('multipart/form-data', chunk.split('>')[0], bundle_type)

    # deleting

    def test_deleting_the_document_deletes_its_file(self):
        for bundle_type, add in (('External', self.add_external),
                                 ('Archive', self.add_archive)):
            bundle = self.make(bundle_type)
            add(bundle)
            document = Product_Document.objects.get(bundle=bundle)
            path = document_files.file_path(document)
            self.client.post(reverse('build:delete_product_document',
                                     args=[str(bundle.pk), str(document.pk)]))
            self.assertFalse(Product_Document.objects.filter(pk=document.pk).exists())
            self.assertFalse(os.path.exists(path), bundle_type)

    def test_someone_else_cannot_upload_into_a_bundle(self):
        bundle = self.make('External')
        User.objects.create_user('intruder', password='pw')
        self.client.login(username='intruder', password='pw')
        self.add_external(bundle)
        self.assertFalse(Product_Document.objects.filter(bundle=bundle).exists())

    # viewing what was uploaded

    def file_url(self, bundle, document, download=False):
        url = reverse('build:document_file', args=[str(bundle.pk), str(document.pk)])
        return url + '?download=1' if download else url

    def body(self, response):
        try:
            return b''.join(response.streaming_content)
        finally:
            response.close()

    def test_the_owner_sees_a_pdf_in_place_on_both_bundle_types(self):
        for bundle_type, add in (('External', self.add_external),
                                 ('Archive', self.add_archive)):
            bundle = self.make(bundle_type)
            add(bundle)
            document = Product_Document.objects.get(bundle=bundle)
            response = self.client.get(self.file_url(bundle, document))
            self.assertEqual(response.status_code, 200, bundle_type)
            self.assertEqual(response['Content-Type'], 'application/pdf')
            self.assertTrue(response['Content-Disposition'].startswith('inline'))
            self.assertIn('guide.pdf', response['Content-Disposition'])
            # Framed by the bundle page's preview window, even where prod says DENY.
            self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')
            self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
            self.assertEqual(self.body(response), fixture_bytes('guide_pdfa1b.pdf'))

    def test_text_is_served_as_utf8_text(self):
        bundle = self.make('External')
        self.add_external(bundle, document_file=document_upload('notes', 'guide.txt'))
        document = Product_Document.objects.get(bundle=bundle)
        response = self.client.get(self.file_url(bundle, document))
        self.assertEqual(response['Content-Type'], 'text/plain; charset=utf-8')
        self.assertEqual(self.body(response), fixture_bytes('guide.txt'))

    def test_download_saves_instead_of_showing(self):
        bundle = self.make('Archive')
        self.add_archive(bundle)
        document = Product_Document.objects.get(bundle=bundle)
        response = self.client.get(self.file_url(bundle, document, download=True))
        self.assertTrue(response['Content-Disposition'].startswith('attachment'))
        self.body(response)

    def test_a_replaced_file_is_what_is_shown_next(self):
        bundle = self.make('External')
        self.add_external(bundle)
        document = Product_Document.objects.get(bundle=bundle)
        self.assertIn('no-cache', self.client.get(self.file_url(bundle, document))['Cache-Control'])
        document_files.store(document, document_files.prepare(
            document_upload('guide', 'guide.txt')))
        document.refresh_from_db()
        response = self.client.get(self.file_url(bundle, document))
        self.assertEqual(self.body(response), fixture_bytes('guide.txt'))

    def test_nobody_else_can_see_the_file(self):
        bundle = self.make('External')
        self.add_external(bundle)
        document = Product_Document.objects.get(bundle=bundle)
        url = self.file_url(bundle, document)

        User.objects.create_user('intruder', password='pw')
        self.client.login(username='intruder', password='pw')
        response = self.client.get(url)
        self.assertRedirects(response, reverse('main:restricted_access'),
                             fetch_redirect_response=False)

        self.client.logout()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('restricted', response['Location'])

    def test_a_document_is_only_reachable_through_its_own_bundle(self):
        mine = self.make('External')
        self.add_external(mine)
        document = Product_Document.objects.get(bundle=mine)
        other = self.make('Archive')
        response = self.client.get(self.file_url(other, document))
        self.assertEqual(response.status_code, 404)

    def test_no_file_and_escaping_names_are_not_found(self):
        bundle = self.make('External')
        self.add_external(bundle)
        document = Product_Document.objects.get(bundle=bundle)
        os.remove(document_files.file_path(document))
        self.assertEqual(self.client.get(self.file_url(bundle, document)).status_code, 404)

        # A typed name from before uploads existed is not trusted to stay put.
        secret = os.path.join(self.archive, 'secret.pdf')
        with open(secret, 'wb') as handle:
            handle.write(b'%PDF-1.4 secret')
        relative = os.path.relpath(secret, document.directory())
        Product_Document.objects.filter(pk=document.pk).update(file_name=relative)
        document.refresh_from_db()
        self.assertTrue(os.path.isfile(document_files.file_path(document)))
        self.assertEqual(self.client.get(self.file_url(bundle, document)).status_code, 404)

    def test_the_bundle_page_offers_view_and_download_on_both_types(self):
        for bundle_type, add in (('External', self.add_external),
                                 ('Archive', self.add_archive)):
            bundle = self.make(bundle_type)
            add(bundle)
            document = Product_Document.objects.get(bundle=bundle)
            response = self.client.get(reverse('build:bundle', args=[str(bundle.pk)]))
            self.assertContains(response, 'data-doc-url="{}"'.format(
                self.file_url(bundle, document)), count=1)
            self.assertContains(response, self.file_url(bundle, document, download=True))
            self.assertContains(response, 'id="documentPreviewModal"', count=1)

    def test_a_document_without_its_file_says_so(self):
        bundle = self.make('Archive')
        self.add_archive(bundle)
        document = Product_Document.objects.get(bundle=bundle)
        os.remove(document_files.file_path(document))
        response = self.client.get(reverse('build:bundle', args=[str(bundle.pk)]))
        self.assertContains(response, 'No file attached')
        self.assertNotContains(response, 'data-doc-url=')

    def test_the_list_puts_every_action_on_the_row_with_one_delete_window(self):
        for bundle_type, add in (('External', self.add_external),
                                 ('Archive', self.add_archive)):
            bundle = self.make(bundle_type)
            add(bundle, name='guide')
            add(bundle, name='notes', document_file=document_upload('notes', 'guide.txt'))
            response = self.client.get(reverse('build:bundle', args=[str(bundle.pk)]))
            self.assertContains(response, '2 documents')
            self.assertContains(response, 'id="documentDeleteModal"', count=1)
            self.assertNotContains(response, 'deleteDocModal')
            self.assertNotContains(response, 'doc_details_')
            for document in Product_Document.objects.filter(bundle=bundle):
                self.assertContains(response, 'data-delete-url="{}"'.format(reverse(
                    'build:delete_product_document', args=[str(bundle.pk), str(document.pk)])))

    def test_tiles_show_the_document_itself(self):
        bundle = self.make('External')
        self.add_external(bundle, name='guide')
        self.add_external(bundle, name='notes', document_file=document_upload('notes', 'guide.txt'))
        guide = Product_Document.objects.get(bundle=bundle, document_name='guide')
        response = self.client.get(reverse('build:bundle', args=[str(bundle.pk)]))
        # The PDF's first page is drawn in the browser from its own file URL...
        self.assertContains(response, 'data-pdf-thumb="{}"'.format(self.file_url(bundle, guide)))
        # ...and the text file's opening lines are in the page already.
        first_line = fixture_bytes('guide.txt').decode('ascii').splitlines()[0]
        self.assertContains(response, first_line)
        self.assertContains(response, 'Add a document')

    def test_an_empty_collection_offers_to_add_one(self):
        for bundle_type in ('External', 'Archive'):
            bundle = self.make(bundle_type)
            response = self.client.get(reverse('build:bundle', args=[str(bundle.pk)]))
            self.assertContains(response, 'No documents yet')

    def test_the_editor_shows_the_current_file(self):
        bundle = self.make('External')
        self.add_external(bundle)
        document = Product_Document.objects.get(bundle=bundle)
        response = self.client.get(
            reverse('build:product_document', args=[str(bundle.pk), str(document.pk)]))
        self.assertContains(response, 'Current file')
        self.assertContains(response, 'data-doc-url="{}"'.format(self.file_url(bundle, document)))
        self.assertContains(response, 'id="documentPreviewModal"', count=1)


# -- what the validation panel says -------------------------------------------------

class RuleTests(TestCase):

    def finding(self, **kwargs):
        base = {'severity': 'ERROR', 'type': 'error.label.missing_file',
                'message': 'URI reference does not exist: '
                           'file:/a/u/b_bundle/document/guide.pdf',
                'label': 'guide.xml', 'label_path': '/a/u/b_bundle/document/guide.xml',
                'line': None, 'element_path': ''}
        base.update(kwargs)
        return base

    def test_a_missing_document_file_is_now_the_users_to_fix(self):
        rule = validate_rules.classify(self.finding())
        self.assertEqual(rule.key, 'document-file-missing')
        self.assertEqual(rule.audience, validate_rules.USER)
        self.assertIn('Attach the file', rule.title)
        self.assertIsNotNone(rule.card)

    def test_a_pdf_that_fails_pdfa_is_explained(self):
        rule = validate_rules.classify(self.finding(
            type='error.pdf.file.not_pdfa_compliant',
            message='Validation failed for flavour PDF/A-1b'))
        self.assertEqual(rule.key, 'document-not-pdfa')
        self.assertEqual(rule.audience, validate_rules.USER)
        self.assertIn('PDF/A-1b', rule.detail)

    def test_a_missing_data_file_is_still_its_own_rule(self):
        rule = validate_rules.classify(self.finding(
            message='URI reference does not exist: file:/a/u/b_bundle/data/x.nc',
            label='x.xml', label_path='/a/u/b_bundle/data/x.xml'))
        self.assertNotEqual(rule.key, 'document-file-missing')


# -- validate's temporary files --------------------------------------------------

class JavaTmpdirTests(TestCase):

    def test_the_environment_points_java_at_the_run_directory(self):
        environment = validate_runner._environment('/work/tmp/run-7')
        self.assertIn('-Djava.io.tmpdir=/work/tmp/run-7', environment['_JAVA_OPTIONS'])

    def test_without_one_the_environment_is_unchanged(self):
        environment = validate_runner._environment()
        self.assertNotIn('java.io.tmpdir', environment.get('_JAVA_OPTIONS', ''))

    def test_the_heap_cap_survives_alongside_it(self):
        with override_settings(VALIDATE_JAVA_MAX_HEAP='1g'):
            options = validate_runner._environment('/t')['_JAVA_OPTIONS']
        self.assertIn('-Xmx1g', options)
        self.assertIn('-Djava.io.tmpdir=/t', options)


    def test_an_unwritable_work_dir_does_not_stop_the_check(self):
        """Only reports/ was writable for apache on prod at first."""
        from unittest import mock
        from build.models import ValidationRun
        user = User.objects.create_user('tmpdir', password='pw')
        bundle = Bundle.objects.create(name='tmpdir', user=user, version='1O00',
                                       bundle_type='External')
        run = ValidationRun.objects.create(bundle=bundle)
        seen = {}

        def fake_popen(command, **kwargs):
            seen['options'] = kwargs['env'].get('_JAVA_OPTIONS', '')
            raise OSError('stop here')

        real_makedirs = os.makedirs

        def refusing_makedirs(path, *args, **kwargs):
            if os.sep + 'tmp' + os.sep + 'run-' in path:
                raise PermissionError(13, 'Permission denied', path)
            return real_makedirs(path, *args, **kwargs)

        with mock.patch.object(validate_runner, 'build_command', return_value=['x']), \
                mock.patch.object(validate_runner.subprocess, 'Popen', fake_popen), \
                mock.patch.object(validate_runner.os, 'makedirs', refusing_makedirs):
            validate_runner.run(run.pk)
        run.refresh_from_db()
        self.assertIn('Could not run validate', run.failure_reason)
        self.assertNotIn('java.io.tmpdir', seen['options'])


@unittest.skipUnless(runner.validate_available(),
                     'VALIDATE_HOME is not configured; see docs/pds_validation_setup.md')
class RealValidateTests(TestCase):
    """The real tool, over bundles built through the views."""

    def setUp(self):
        self.archive = tempfile.mkdtemp(prefix='elsa-docup-real-')
        self.addCleanup(shutil.rmtree, self.archive, True)
        self.work = tempfile.mkdtemp(prefix='elsa-docup-work-')
        self.addCleanup(shutil.rmtree, self.work, True)
        patcher = override_settings(ARCHIVE_DIR=self.archive, VALIDATE_WORK_DIR=self.work)
        patcher.enable()
        self.addCleanup(patcher.disable)
        User.objects.create_user('docreal', password='pw')
        self.client.login(username='docreal', password='pw')
        Investigation.objects.create(
            name='Atmospheric Modeling Annex', type_of='Individual Investigation',
            lid='urn:nasa:pds:context:investigation:individual.atmospheric_modeling_annex',
            file_ref='')

    def bundle_with(self, bundle_type, document_file):
        name = 'real {}'.format(bundle_type.lower())
        self.client.post(reverse('build:build'), {
            'name': name, 'bundle_type': bundle_type, 'version': '1O00', 'bundleID': ''})
        bundle = Bundle.objects.get(name=name)
        if bundle_type == 'External':
            self.client.post(
                reverse('build:annex_collection_document', args=[str(bundle.pk)]),
                {'form_name': 'document_form', 'document_name': 'guide',
                 'document_id': 'guide', 'comment': '', 'source': 'bundle',
                 'document_file': document_file})
        else:
            self.client.post(
                reverse('build:bundle', args=[str(bundle.pk)]),
                {'form_name': 'document_form', 'document_name': 'guide',
                 'publication_date': '2026-09-25', 'document_file': document_file})
        self.assertTrue(Product_Document.objects.filter(bundle=bundle).exists())
        return bundle

    def keys(self, bundle):
        report = os.path.join(self.work, 'report-{}.json'.format(bundle.pk))
        findings = runner.run_validate(bundle.directory(), report)
        document_findings = [f for f in findings
                             if '/document/' in (f.get('label_path') or '')
                             or '/document/' in (f.get('message') or '')]
        return ({rule.key for rule in map(validate_rules.classify, document_findings) if rule},
                [f['type'] for f in document_findings])

    def test_external_no_document_file_is_missing(self):
        bundle = self.bundle_with('External', document_upload('guide'))
        keys, types = self.keys(bundle)
        self.assertNotIn('document-file-missing', keys, types)
        self.assertNotIn('error.label.missing_file', types)

    def test_archive_pdfa_1b_passes_verapdf(self):
        bundle = self.bundle_with('Archive', document_upload('guide'))
        keys, types = self.keys(bundle)
        self.assertNotIn('document-file-missing', keys, types)
        self.assertNotIn('document-not-pdfa', keys, types)
        self.assertFalse([t for t in types if 'pdf' in t.lower()], types)

    def test_a_pdf_that_only_claims_pdfa_1_is_caught_by_validate(self):
        """The negative control: upload trusts the claim, veraPDF checks it.

        An ordinary PDF with a PDF/A-1 claim appended passes the upload check, so
        this is what proves veraPDF really runs, and that its finding reaches the
        user as the PDF/A rule rather than as raw text.
        """
        faked = fixture_bytes('guide_plain.pdf') + b"\n% pdfaid:part='1'\n"
        bundle = self.bundle_with('Archive', upload('guide.pdf', faked))
        keys, types = self.keys(bundle)
        self.assertIn('document-not-pdfa', keys, types)

    def test_archive_text_document_is_accepted(self):
        bundle = self.bundle_with('Archive', document_upload('guide', 'guide.txt'))
        keys, types = self.keys(bundle)
        self.assertNotIn('document-file-missing', keys, types)
        self.assertFalse([t for t in types if 'standard' in t.lower()], types)

    def test_a_run_leaves_no_temporary_pdf_behind(self):
        """veraPDF copies each PDF to java.io.tmpdir and never deletes it."""
        from build.models import ValidationRun
        bundle = self.bundle_with('Archive', document_upload('guide'))
        seen = []
        real_popen = validate_runner.subprocess.Popen

        def watching_popen(command, **kwargs):
            seen.append(kwargs['env'].get('_JAVA_OPTIONS', ''))
            return real_popen(command, **kwargs)

        run = ValidationRun.objects.create(bundle=bundle, tier=ValidationRun.TIER_FULL)
        validate_runner.subprocess.Popen = watching_popen
        try:
            validate_runner.run(run.pk)
        finally:
            validate_runner.subprocess.Popen = real_popen
        run.refresh_from_db()
        self.assertEqual(run.status, ValidationRun.STATUS_DONE, run.failure_reason)
        tmpdir = validate_runner.java_tmpdir_for(run)
        self.assertIn('-Djava.io.tmpdir=' + tmpdir, seen[0])
        self.assertFalse(os.path.exists(tmpdir))
