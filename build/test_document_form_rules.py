# -*- coding: utf-8 -*-
"""Document titles and author names are checked while the user is still on the form.

Both rules are PDS4's, both were reaching real bundles, and both were only caught
by the validator: by then the document had been written, labelled, listed in an
inventory, and shown back to its owner as an error to go and undo.

A real bundle had two documents named "11" with file names "111" and "aa". File
names are no longer typed: they come from the uploaded file, and test_document_upload
covers them.
"""

from __future__ import unicode_literals

import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from build.corpus.builder import document_upload
from build.forms import AnnexProductDocumentForm, ProductDocumentForm
from build.models import Bundle, Investigation, Product_Document


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
            {'document_name': name, 'document_id': 'd1', 'comment': ''},
            {'document_file': document_upload('new_guide')},
            bundle=self.bundle, **kwargs)

    def add(self, name):
        return self.client.post(
            reverse('build:annex_collection_document', args=[str(self.bundle.pk)]),
            {'form_name': 'document_form', 'document_name': name, 'document_id': name,
             'document_file': document_upload(name), 'comment': '', 'source': 'bundle'})

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
            {'document_name': 'guide', 'document_id': 'd1', 'comment': ''},
            {'document_file': document_upload('guide')}, bundle=other)
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
            {'document_name': 'guide', 'document_id': 'd1', 'comment': ''},
            {'document_file': document_upload('guide')})
        self.assertTrue(form.is_valid(), form.errors)


class AccentedNameTests(TestCase):
    """PDS4 holds author and editor names in ASCII_* types, all Basic Latin only.

    Found by putting the real scientist's name into the corpus: "Raul
    Morales-Juberias" is refused by PDS however correctly it is spelled. That is a
    limitation of the archive format, not anything the user did wrong, so the form
    says so where they can see it and offers the spelling that works rather than
    leaving them to discover it from a validation report later.
    """

    def setUp(self):
        from build.models import Citation_Information
        self.archive = tempfile.mkdtemp(prefix='elsa-accent-')
        self.addCleanup(shutil.rmtree, self.archive, True)
        patcher = override_settings(ARCHIVE_DIR=self.archive)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.user = User.objects.create_user('accent', password='pw')
        self.client.login(username='accent', password='pw')
        Investigation.objects.create(
            name='Atmospheric Modeling Annex', type_of='Individual Investigation',
            lid='urn:nasa:pds:context:investigation:individual.atmospheric_modeling_annex',
            file_ref='')
        self.client.post(reverse('build:build'), {
            'name': 'accent bundle', 'bundle_type': 'External',
            'version': '1O00', 'bundleID': ''})
        self.bundle = Bundle.objects.get(name='accent bundle')
        self.client.post(
            reverse('build:citation_information', args=[str(self.bundle.pk)]),
            {'number_of_authors_people': 1, 'number_of_authors_organization': 0,
             'number_of_editors_people': 0, 'number_of_editors_organization': 0,
             'publication_year': '2026', 'description': 'A description',
             'keyword': 'k'})
        self.citation = Citation_Information.objects.get(bundle=self.bundle)

    def form(self, given, family):
        from build.forms import EditCitationInformationForm
        return EditCitationInformationForm(
            {'author_person_0_given_name': given,
             'author_person_0_family_name': family,
             'author_person_0_orcid': '', 'author_person_0_affiliation': ''},
            pk_cit=self.citation.pk)

    def test_a_plain_name_is_accepted(self):
        self.assertTrue(self.form('Ada', 'Lovelace').is_valid())

    def test_an_apostrophe_is_still_fine(self):
        self.assertTrue(self.form("Sean", "O'Brien").is_valid())

    def test_an_accented_given_name_is_refused(self):
        form = self.form('Raúl', 'Smith')
        self.assertFalse(form.is_valid())
        self.assertIn('author_person_0_given_name', form.errors)

    def test_an_accented_family_name_is_refused(self):
        form = self.form('John', 'Morales-Juberías')
        self.assertFalse(form.is_valid())
        self.assertIn('author_person_0_family_name', form.errors)

    def test_the_message_offers_the_spelling_that_works(self):
        """Without this the user is told no and left to guess."""
        form = self.form('Raúl', 'Morales-Juberías')
        form.is_valid()
        self.assertIn('Raul', form.errors['author_person_0_given_name'][0])
        self.assertIn('Morales-Juberias',
                      form.errors['author_person_0_family_name'][0])

    def test_the_message_does_not_blame_the_user(self):
        form = self.form('Raúl', 'Smith')
        form.is_valid()
        message = form.errors['author_person_0_given_name'][0]
        self.assertIn('limit of the archive format', message)

    def test_a_name_with_no_latin_equivalent_is_still_explained(self):
        form = self.form('山田', 'Smith')
        form.is_valid()
        self.assertIn('outside that set',
                      form.errors['author_person_0_given_name'][0])
