# -*- coding: utf-8 -*-
"""End to end: an External bundle needs an author and a NetCDF file, and says so.

Every step drives the real views: the citation and its author through the citation
forms, the target through the context search, the document and the collection
through the bundle page, the NetCDF file through the real upload. Each bundle is
then checked by the real NASA validator, through validate_runner.run, and submitted
through the real submission view.

What is being proved, for each requirement:

- the validator really does say nothing when it is missing, which is the whole
  reason ELSA has to ask for it itself;
- ELSA asks for it, in the list the Review & Submit window shows;
- the submission is refused, even though the validator passed the bundle;
- doing what the item says clears it, and the same bundle then submits.

An Archive bundle missing both is built as well, to prove neither is asked of it.

Slow (a JVM per check) and skipped when the validator or the NetCDF fixture is not
available on this machine.

    python3 manage.py test build.test_external_requirements_e2e --settings=test_settings
"""
from __future__ import unicode_literals

import os
import shutil
import tempfile

from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from build import preflight, validate_rules, validate_runner
from build.corpus import runner
from build.corpus.builder import BundleBuilder
from build.corpus.shapes import NETCDF_FIXTURE, have_netcdf
from build.models import Citation_Information, NetCDFFile, Target, ValidationRun

LAB_ANALOG = {
    'lid': 'urn:nasa:pds:context:target:laboratory_analog.mars',
    'name': 'Mars Laboratory Analog', 'type_of': 'Laboratory Analog', 'vid': 1.0,
    'file_ref': 'https://pds.nasa.gov/data/pds4/context-pds4/target/laboratory_analog.mars_1.0.xml',
}


class Builder(BundleBuilder):
    """The corpus builder, plus the two steps these tests need through real views."""

    def choose_target(self):
        target, _ = Target.objects.get_or_create(lid=LAB_ANALOG['lid'], defaults=LAB_ANALOG)
        self.client.post(reverse('build:context_search_target', args=[self.bundle.pk]),
                         {'target': target.pk})
        return self


@override_settings(VALIDATE_BLOCKS_SUBMISSION=True, VALIDATE_AUTO_CHECK=False,
                   VALIDATE_EMAIL_AFTER_SECONDS=100000)
class ExternalRequirementsEndToEndTests(TransactionTestCase):

    def setUp(self):
        if not runner.validate_available():
            self.skipTest('the PDS validate tool is not configured on this machine')
        if not have_netcdf():
            self.skipTest('the NetCDF fixture is not on this machine')
        self.work = tempfile.mkdtemp(prefix='elsa-ext-req-')
        self.addCleanup(shutil.rmtree, self.work, True)
        patcher = override_settings(ARCHIVE_DIR=os.path.join(self.work, 'archive'),
                                    MEDIA_ROOT=os.path.join(self.work, 'media'),
                                    VALIDATE_WORK_DIR=os.path.join(self.work, 'validation'))
        patcher.enable()
        self.addCleanup(patcher.disable)

    # -- helpers -------------------------------------------------------------------

    def external(self, name, authors=1, netcdf=True):
        b = Builder(username='ext_e2e')
        b.create(name, 'External').add_citation(authors=authors)
        if authors:
            b.fill_authors()
        b.add_modification_history().choose_target().add_document('readme')
        if netcdf:
            b.add_collection('sims').upload_netcdf(NETCDF_FIXTURE, 'sims')
        return b

    def check(self, bundle):
        """A real validation run, in-process, recorded the way the page records one."""
        run = ValidationRun.objects.create(bundle=bundle, tier=ValidationRun.TIER_STRUCTURE)
        run = validate_runner.run(run.pk)
        self.assertEqual(run.status, ValidationRun.STATUS_DONE, run.failure_reason)
        return run

    def user_items(self, run):
        return [item['key'] for item in validate_rules.translate(run.findings or [])['user']]

    def submit(self, b):
        b.client.post(reverse('build:submit_bundle_internal', args=[b.bundle.pk]))
        b.bundle.refresh_from_db()
        return b.bundle.submitted_at is not None

    def review_window(self, b):
        body = b.client.get(reverse('build:bundle', args=[b.bundle.pk])).content.decode()
        return body.split('id="reviewBundleModal"')[1].split('<!-- Form Modals -->')[0]

    def requirement_keys(self, b):
        return [item['key'] for item in preflight.requirements(b.bundle)]

    # -- a complete bundle goes through -------------------------------------------

    def test_a_complete_external_bundle_is_checked_and_submitted(self):
        b = self.external('ext req complete')
        self.assertTrue(NetCDFFile.objects.filter(bundle=b.bundle, processed=True).exists())
        self.assertEqual(self.requirement_keys(b), [])
        run = self.check(b.bundle)
        self.assertEqual(self.user_items(run), [], run.findings)
        self.assertIsNone(validate_runner.submission_block(b.bundle, b.user))
        self.assertTrue(self.submit(b))

    # -- no NetCDF: validate is silent, ELSA is not, and uploading one clears it ----

    def test_no_netcdf_passes_validate_but_not_elsa_until_one_is_uploaded(self):
        b = self.external('ext req no data', netcdf=False)
        run = self.check(b.bundle)
        self.assertEqual(self.user_items(run), [],
                         'the validator reported something; the premise no longer holds')

        self.assertEqual(self.requirement_keys(b), ['netcdf-missing'])
        self.assertIn('Upload at least one NetCDF file', self.review_window(b))
        self.assertFalse(self.submit(b))
        self.assertEqual(b.bundle.get_status(), 'in_progress')

        # Doing what the item says: add a collection, upload a file into it.
        b.add_collection('sims').upload_netcdf(NETCDF_FIXTURE, 'sims')
        self.assertEqual(self.requirement_keys(b), [])
        self.assertNotIn('Upload at least one NetCDF file', self.review_window(b))
        # The files changed, so the old check no longer counts; a new one does.
        self.assertIsNotNone(validate_runner.submission_block(b.bundle, b.user))
        self.check(b.bundle)
        self.assertEqual(b.bundle.get_status(), 'ready')
        self.assertTrue(self.submit(b))

    def test_a_file_whose_label_failed_does_not_count(self):
        b = self.external('ext req failed file')
        NetCDFFile.objects.filter(bundle=b.bundle).update(
            processed=False, processing_error='regeneration failed')
        self.assertEqual(self.requirement_keys(b), ['netcdf-missing'])
        self.check(b.bundle)
        self.assertFalse(self.submit(b))

    # -- authors -------------------------------------------------------------------

    def test_the_citation_form_refuses_an_external_citation_with_no_author(self):
        """The only moment an author can be added is when the citation is created, so
        that is where it is enforced, and the refusal is shown rather than lost."""
        b = Builder(username='ext_e2e')
        b.create('ext req form refuses', 'External')
        response = b.client.post(reverse('build:bundle', args=[b.bundle.pk]), {
            'number_of_authors_people': 0, 'number_of_authors_organization': 0,
            'number_of_editors_people': 0, 'number_of_editors_organization': 0,
            'publication_year': '2026', 'description': 'A test bundle', 'keyword': 'winds'})
        self.assertFalse(Citation_Information.objects.filter(bundle=b.bundle).exists())
        body = response.content.decode()
        self.assertIn('Add at least one author, a person or an organization', body)
        self.assertTrue(response.context['reopen_citation_modal'])
        self.assertIn("document.getElementById('citation_information_modal');", body)

        # The same numbers with one author go through.
        b.add_citation(authors=1)
        self.assertTrue(Citation_Information.objects.filter(bundle=b.bundle).exists())

    def test_an_unrelated_post_does_not_reopen_the_citation_window(self):
        b = Builder(username='ext_e2e')
        b.create('ext req unrelated post', 'External')
        response = b.client.post(reverse('build:bundle', args=[b.bundle.pk]),
                                 {'collection_name': 'sims', 'collection_type': 'External'},
                                 follow=True)
        self.assertFalse((response.context or {}).get('reopen_citation_modal', False))
        self.assertNotIn("document.getElementById('citation_information_modal');",
                         response.content.decode())

    def legacy_authorless(self, name):
        """A citation with no author, as one created before the form refused them.

        Made through the real view, so the labels are written exactly as they were,
        with only the new refusal switched off.
        """
        from unittest import mock
        from django import forms as django_forms
        from build.forms import CitationInformationForm
        with mock.patch.object(CitationInformationForm, 'clean',
                               lambda form: django_forms.ModelForm.clean(form)):
            return self.external(name, authors=0)

    def test_an_authorless_citation_passes_validate_but_not_elsa(self):
        b = self.legacy_authorless('ext req no author')
        self.assertEqual(Citation_Information.objects.get(bundle=b.bundle)
                         .number_of_authors_people, 0)
        run = self.check(b.bundle)
        self.assertEqual(self.user_items(run), [],
                         'the validator reported something; the premise no longer holds')

        self.assertEqual(self.requirement_keys(b), ['author-missing'])
        window = self.review_window(b)
        self.assertIn('Add at least one author to the citation', window)
        self.assertIn('delete this citation, and create it again', window)
        self.assertFalse(self.submit(b))
        self.assertEqual(b.bundle.get_status(), 'in_progress')

    def test_the_advice_on_an_authorless_citation_can_be_followed(self):
        """Delete the citation, create it again with an author, and it submits."""
        b = self.legacy_authorless('ext req follow advice')
        citation = Citation_Information.objects.get(bundle=b.bundle)
        b.client.post(reverse('build:citation_information_delete',
                              args=[b.bundle.pk, citation.pk]))
        self.assertFalse(Citation_Information.objects.filter(bundle=b.bundle).exists())

        b.add_citation(authors=1).fill_authors()
        self.assertEqual(self.requirement_keys(b), [])
        run = self.check(b.bundle)
        self.assertEqual(self.user_items(run), [], run.findings)
        self.assertEqual(b.bundle.get_status(), 'ready')
        self.assertTrue(self.submit(b))

    def test_an_author_declared_but_left_blank_is_caught_by_validate_instead(self):
        """The one author case ELSA does not ask about, because validate already does."""
        b = Builder(username='ext_e2e')
        b.create('ext req blank author', 'External').add_citation(authors=1)   # never named
        b.add_modification_history().choose_target().add_document('readme')
        b.add_collection('sims').upload_netcdf(NETCDF_FIXTURE, 'sims')
        self.assertNotIn('author-missing', self.requirement_keys(b))
        run = self.check(b.bundle)
        self.assertIn('citation-author-blank', self.user_items(run))
        self.assertFalse(self.submit(b))

    # -- Archive is not asked for either -------------------------------------------

    def test_an_archive_bundle_is_not_asked_for_either(self):
        b = Builder(username='ext_e2e')
        b.create('arc req untouched', 'Archive').add_citation(authors=0)
        b.add_modification_history()
        keys = self.requirement_keys(b)
        self.assertNotIn('author-missing', keys)
        self.assertNotIn('netcdf-missing', keys)
        self.assertNotIn('Upload at least one NetCDF file',
                         b.client.get(reverse('build:bundle', args=[b.bundle.pk])).content.decode())
