# -*- coding: utf-8 -*-
"""Document file names and titles are checked while the user is still on the form.

Both rules are PDS4's, both were reaching real bundles, and both were only caught
by the validator: by then the document had been written, labelled, listed in an
inventory, and shown back to its owner as an error to go and undo.

A real bundle had two documents named "11" with file names "111" and "aa".
"""

from __future__ import unicode_literals

import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from build.forms import AnnexProductDocumentForm, ProductDocumentForm
from build.models import Bundle, Investigation, Product_Document


class FileNameRuleTests(TestCase):
    """The pattern is PDS4's own, not an approximation of it."""

    def field(self, value):
        form = AnnexProductDocumentForm({
            'document_name': 'a doc', 'document_id': 'doc1',
            'file_name': value, 'comment': '', 'document_std_id': 'PDF/A'})
        form.is_valid()
        return form.errors.get('file_name')

    def test_a_normal_name_is_accepted(self):
        for value in ('User_Guide.pdf', 'readme.txt', 'a1.b2', 'v1.2.3.tar'):
            self.assertIsNone(self.field(value), value)

    def test_a_name_with_no_extension_is_refused(self):
        for value in ('111', 'aa', 'no_extension'):
            self.assertIsNotNone(self.field(value), value)

    def test_the_message_names_the_value_and_shows_a_good_one(self):
        errors = self.field('111')
        self.assertIn('111', errors[0])
        self.assertIn('.pdf', errors[0])

    def test_a_space_is_refused(self):
        self.assertIsNotNone(self.field('User Guide.pdf'))

    def test_a_name_that_does_not_start_alphanumeric_is_refused(self):
        for value in ('.hidden', '-starts.pdf', '_leading.pdf'):
            self.assertIsNotNone(self.field(value), value)

    def test_the_archive_form_has_the_same_rule(self):
        form = ProductDocumentForm({'document_name': 'a doc',
                                    'publication_date': '2026-01-15',
                                    'file_name': '111'})
        form.is_valid()
        self.assertIn('file_name', form.errors)


class DuplicateDocumentNameTests(TestCase):
    """The name becomes the identifier the collection inventory lists."""

    def setUp(self):
        self.archive = tempfile.mkdtemp(prefix='elsa-docname-')
        self.addCleanup(shutil.rmtree, self.archive, True)
        patcher = override_settings(ARCHIVE_DIR=self.archive)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.user = User.objects.create_user('docname', password='pw')
        self.client.login(username='docname', password='pw')
        Investigation.objects.create(
            name='Atmospheric Modeling Annex', type_of='Individual Investigation',
            lid='urn:nasa:pds:context:investigation:individual.atmospheric_modeling_annex',
            file_ref='')
        self.client.post(reverse('build:build'), {
            'name': 'docname bundle', 'bundle_type': 'External',
            'version': '1O00', 'bundleID': ''})
        self.bundle = Bundle.objects.get(name='docname bundle')

    def form(self, name, **kwargs):
        return AnnexProductDocumentForm(
            {'document_name': name, 'document_id': 'd1',
             'file_name': 'guide.pdf', 'comment': '', 'document_std_id': 'PDF/A'},
            bundle=self.bundle, **kwargs)

    def add(self, name):
        return self.client.post(
            reverse('build:annex_collection_document', args=[str(self.bundle.pk)]),
            {'form_name': 'document_form', 'document_name': name, 'document_id': name,
             'file_name': name + '.pdf', 'comment': '', 'document_std_id': 'PDF/A',
             'source': 'bundle'})

    def test_a_first_document_is_fine(self):
        self.assertTrue(self.form('User Guide').is_valid())

    def test_a_second_document_with_the_same_name_is_refused(self):
        self.add('guide')
        self.assertEqual(Product_Document.objects.filter(bundle=self.bundle).count(), 1)
        form = self.form('guide')
        self.assertFalse(form.is_valid())
        self.assertIn('already has a document', form.errors['document_name'][0])

    def test_the_duplicate_never_reaches_the_database(self):
        """The symptom was an inventory listing the same identifier twice."""
        self.add('guide')
        self.add('guide')
        self.assertEqual(Product_Document.objects.filter(bundle=self.bundle).count(), 1)

    def test_a_different_name_is_still_allowed(self):
        self.add('guide')
        self.add('variables')
        self.assertEqual(Product_Document.objects.filter(bundle=self.bundle).count(), 2)

    def test_another_bundle_may_use_the_same_name(self):
        """Identifiers are scoped to the bundle, so this is not a clash."""
        self.add('guide')
        self.client.post(reverse('build:build'), {
            'name': 'other bundle', 'bundle_type': 'External',
            'version': '1O00', 'bundleID': ''})
        other = Bundle.objects.get(name='other bundle')
        form = AnnexProductDocumentForm(
            {'document_name': 'guide', 'document_id': 'd1',
             'file_name': 'guide.pdf', 'comment': '', 'document_std_id': 'PDF/A'},
            bundle=other)
        self.assertTrue(form.is_valid(), form.errors)

    def test_editing_a_document_without_renaming_it_is_not_a_duplicate(self):
        """It would otherwise clash with itself, and no edit could ever be saved."""
        self.add('guide')
        existing = Product_Document.objects.get(bundle=self.bundle)
        form = self.form('guide', editing=existing)
        self.assertTrue(form.is_valid(), form.errors)

    def test_renaming_onto_another_document_is_still_refused(self):
        self.add('guide')
        self.add('variables')
        variables = Product_Document.objects.get(
            bundle=self.bundle, document_name='variables')
        form = self.form('guide', editing=variables)
        self.assertFalse(form.is_valid())

    def test_without_a_bundle_the_check_is_skipped_rather_than_crashing(self):
        """Callers that never passed a bundle keep working."""
        self.add('guide')
        form = AnnexProductDocumentForm(
            {'document_name': 'guide', 'document_id': 'd1',
             'file_name': 'guide.pdf', 'comment': '', 'document_std_id': 'PDF/A'})
        self.assertTrue(form.is_valid(), form.errors)
