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

    def field(self, value, document_std_id=''):
        """Errors for a file name, with no declared format by default.

        No format on purpose: with one, ELSA completes the extension itself rather
        than refusing, so these cases have to be posed as a name ELSA cannot finish.
        """
        form = AnnexProductDocumentForm({
            'document_name': 'a doc', 'document_id': 'doc1',
            'file_name': value, 'comment': '',
            'document_std_id': document_std_id})
        form.is_valid()
        return form.errors.get('file_name')

    def test_a_normal_name_is_accepted(self):
        for value in ('User_Guide.pdf', 'readme.txt', 'a1.b2', 'v1.2.3.tar'):
            self.assertIsNone(self.field(value), value)

    def test_a_name_with_no_extension_is_refused_when_nothing_implies_one(self):
        """With a declared format ELSA supplies it; without one it cannot guess."""
        for value in ('111', 'aa', 'no_extension'):
            self.assertIsNotNone(self.field(value), value)

    def test_the_same_name_is_accepted_once_a_format_says_what_it_is(self):
        for value in ('111', 'aa', 'no_extension'):
            self.assertIsNone(self.field(value, 'PDF/A'), value)

    def test_the_message_names_the_value_and_shows_a_good_one(self):
        errors = self.field('111')
        self.assertIsNotNone(errors)
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

    def test_the_archive_form_also_completes_the_extension(self):
        form = ProductDocumentForm({'document_name': 'a doc',
                                    'publication_date': '2026-01-15',
                                    'file_name': 'User_Guide',
                                    'document_std_id': 'PDF/A'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['file_name'], 'User_Guide.pdf')


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


class FormatImpliesTheExtensionTests(TestCase):
    """The form already asks for the file format, so the extension is derivable.

    Telling someone to go back and add ".pdf" to a name when they have already said
    the file is a PDF is asking them to repeat themselves. PDS requires the
    extension; ELSA can supply it.
    """

    def field(self, file_name, document_std_id='PDF/A'):
        form = AnnexProductDocumentForm({
            'document_name': 'a doc', 'document_id': 'doc1',
            'file_name': file_name, 'comment': '',
            'document_std_id': document_std_id})
        form.is_valid()
        return form

    def test_a_bare_name_gets_the_extension_its_format_implies(self):
        form = self.field('User_Guide', 'PDF/A')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['file_name'], 'User_Guide.pdf')

    def test_ascii_implies_txt(self):
        form = self.field('readme', 'ASCII')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['file_name'], 'readme.txt')

    def test_a_name_that_already_has_one_is_left_alone(self):
        form = self.field('guide.pdf', 'PDF/A')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['file_name'], 'guide.pdf')

    def test_a_name_with_its_own_different_extension_is_respected(self):
        """The user knows what the file is; the format field is a declaration."""
        form = self.field('data.csv', 'ASCII')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['file_name'], 'data.csv')

    def test_the_names_that_used_to_fail_now_succeed(self):
        """111, aa and awd are the real ones from a real bundle."""
        for bare in ('111', 'aa', 'awd'):
            form = self.field(bare, 'PDF/A')
            self.assertTrue(form.is_valid(), '{}: {}'.format(bare, form.errors))
            self.assertEqual(form.cleaned_data['file_name'], bare + '.pdf')

    def test_a_genuinely_bad_name_is_still_refused(self):
        """Completing the extension must not become a way to smuggle anything in."""
        form = self.field('my guide', 'PDF/A')
        self.assertFalse(form.is_valid())
        self.assertIn('file_name', form.errors)

    def test_no_format_means_the_old_message_still_applies(self):
        form = self.field('User_Guide', '')
        self.assertFalse(form.is_valid())
        self.assertIn('extension', form.errors['file_name'][0])
