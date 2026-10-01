# -*- coding: utf-8 -*-
"""ELSA's own requirements, and the panel that has to report them.

The defect these guard: an External bundle with no target was told it had passed.
PDS4 marks Target_Identification minOccurs="0" in Context_Area, so a bundle without
one produces a valid label and the PDS validator correctly reports nothing. ELSA
requires a target anyway, and the Review & Submit button had always refused a bundle
without one. The two verdicts sat on the same screen disagreeing: a red "Targets"
badge in the progress bar and a green "passed" in the validation panel.

The same is true of a modification history, which PDS4 also makes optional. Only
Citation Information reached the validator at all, and only because ELSA ships the
citation skeleton in every label, so an unfilled one shows up as blank elements.

Also guarded here: the submit gate. Before this, the three requirements were enforced
only by disabling the button, so a POST that skipped the page enforced nothing.
"""
from __future__ import unicode_literals

import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from build import preflight, validate_rules, validate_runner
from build.models import (Bundle, Citation_Information, Investigation,
                          Modification_History, NetCDFFile, Target, ValidationRun)


def with_data(bundle):
    """The External-only requirement unrelated to what these tests are about."""
    NetCDFFile.objects.create(bundle=bundle, title='out.nc', file='out.nc', processed=True)
    return bundle


CITATION_FINDING = {
    'severity': 'ERROR', 'type': 'error.label.schematron',
    'message': 'In Product_Bundle both Citation_Information and its description are required.',
    'label': 'bundle_x.xml', 'label_path': '/bundle_x.xml', 'line': 1,
    'element_path': 'Product_Bundle/Identification_Area',
}


class RequirementsTests(TestCase):
    """The checks themselves, against the database."""

    def setUp(self):
        self.user = User.objects.create_user('req', password='pw')
        self.bundle = Bundle.objects.create(
            name='req bundle', user=self.user, version='1O00',
            bundle_type='External')

    def keys(self):
        return [item['key'] for item in preflight.requirements(self.bundle)]

    def satisfy_citation(self):
        Citation_Information.objects.create(
            bundle=self.bundle, description='A bundle', publication_year='2026',
            number_of_authors_people=1)

    def satisfy_modification_history(self):
        Modification_History.objects.create(
            bundle=self.bundle, description='Initial delivery',
            modification_date='2026-09-18', version_id='1.0')

    def satisfy_target(self):
        target = Target.objects.create(
            name='Mars', type_of='Planet',
            lid='urn:nasa:pds:context:target:planet.mars', file_ref='')
        self.bundle.targets.add(target)

    def test_a_new_external_bundle_is_missing_everything_that_applies_yet(self):
        # No author item: until there is a citation, adding one is the thing to do.
        self.assertEqual(self.keys(), ['citation-missing',
                                       'modification-history-missing',
                                       'target-missing', 'netcdf-missing'])

    def test_a_missing_target_is_reported_on_its_own(self):
        self.satisfy_citation()
        self.satisfy_modification_history()
        with_data(self.bundle)
        self.assertEqual(self.keys(), ['target-missing'])

    def test_a_missing_modification_history_is_reported_on_its_own(self):
        self.satisfy_citation()
        self.satisfy_target()
        with_data(self.bundle)
        self.assertEqual(self.keys(), ['modification-history-missing'])

    def test_a_complete_bundle_has_nothing_outstanding(self):
        self.satisfy_citation()
        self.satisfy_modification_history()
        self.satisfy_target()
        with_data(self.bundle)
        self.assertEqual(self.keys(), [])
        self.assertTrue(preflight.met(self.bundle))

    def test_every_requirement_names_a_card_the_panel_can_send_someone_to(self):
        for item in preflight.requirements(self.bundle):
            self.assertTrue(item['card'], item['key'])
            self.assertTrue(item['anchor'] or item['scroll_to'],
                            '{} has nowhere to go'.format(item['key']))

    def test_the_target_item_says_why_the_validator_is_silent(self):
        # Without this a user compares the panel against the Validation output tab,
        # finds nothing matching, and stops trusting the panel.
        item = [i for i in preflight.requirements(self.bundle)
                if i['key'] == 'target-missing'][0]
        self.assertEqual(item['source'], 'elsa')
        self.assertIn('PDS4', item['detail'])

    def test_status_matches_what_the_submit_button_reads(self):
        self.satisfy_citation()
        self.assertEqual(preflight.status(self.bundle), {
            'Citation_Information': True,
            'Modification_History': False,
            'Targets': False,
        })


class SummaryTests(TestCase):
    """Requirements have to count as blocking, or the panel says passed."""

    def setUp(self):
        self.user = User.objects.create_user('sum', password='pw')
        self.bundle = Bundle.objects.create(
            name='sum bundle', user=self.user, version='1O00',
            bundle_type='External')
        Citation_Information.objects.create(
            bundle=self.bundle, description='A bundle', publication_year='2026',
            number_of_authors_people=1)
        Modification_History.objects.create(
            bundle=self.bundle, description='Initial delivery',
            modification_date='2026-09-18', version_id='1.0')
        with_data(self.bundle)

    def test_no_findings_and_a_missing_target_is_not_a_pass(self):
        outstanding = preflight.requirements(self.bundle)
        summary = validate_rules.summarise([], outstanding)
        self.assertEqual(summary['blocking'], 1)
        self.assertFalse(summary['can_submit'])

    def test_no_findings_and_nothing_outstanding_is_a_pass(self):
        summary = validate_rules.summarise([], [])
        self.assertEqual(summary['blocking'], 0)
        self.assertTrue(summary['can_submit'])

    def test_a_requirement_is_not_listed_twice_when_pds_reports_it_too(self):
        # An unfilled citation is both absent from the database and blank in the
        # label, so it arrives from both sources under the same key.
        bare = Bundle.objects.create(name='bare', user=self.user, version='1O00',
                                     bundle_type='External')
        outstanding = preflight.requirements(bare)
        merged = validate_rules.summarise([CITATION_FINDING], outstanding)
        keys = [i['key'] for i in validate_rules.merge_user(
            validate_rules.translate([CITATION_FINDING])['user'], outstanding)]
        self.assertEqual(keys.count('citation-missing'), 1)
        # citation, modification history, target, NetCDF: four, not five.
        self.assertEqual(merged['blocking'], 4)

    def test_requirements_are_grouped_under_the_card_that_fixes_them(self):
        outstanding = preflight.requirements(self.bundle)
        grouped = dict(validate_rules.cards([], outstanding))
        self.assertIn('Context Products', grouped)
        self.assertEqual([i['key'] for i in grouped['Context Products']],
                         ['target-missing'])


class SubmitGateTests(TestCase):
    """The gate, not just the disabled button."""

    def setUp(self):
        self.archive = tempfile.mkdtemp(prefix='elsa-gate-')
        self.addCleanup(shutil.rmtree, self.archive, True)
        patcher = override_settings(ARCHIVE_DIR=self.archive)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.user = User.objects.create_user('gate', password='pw')
        self.bundle = Bundle.objects.create(
            name='gate bundle', user=self.user, version='1O00',
            bundle_type='External')

    def test_a_missing_requirement_blocks_submission(self):
        blocked = validate_runner.submission_block(self.bundle, self.user)
        self.assertIsNotNone(blocked)
        self.assertEqual(blocked[0], validate_runner.BLOCK_REQUIREMENTS)
        self.assertIn('target', blocked[1].lower())

    def test_it_blocks_even_when_validation_does_not(self):
        # VALIDATE_BLOCKS_SUBMISSION turns off the validation gate. It was never
        # meant to waive ELSA's own requirements.
        with override_settings(VALIDATE_BLOCKS_SUBMISSION=False):
            blocked = validate_runner.submission_block(self.bundle, self.user)
        self.assertIsNotNone(blocked)
        self.assertEqual(blocked[0], validate_runner.BLOCK_REQUIREMENTS)

    def test_it_blocks_staff_too(self):
        staff = User.objects.create_user('staffer', password='pw', is_staff=True)
        blocked = validate_runner.submission_block(self.bundle, staff)
        self.assertIsNotNone(blocked)
        self.assertEqual(blocked[0], validate_runner.BLOCK_REQUIREMENTS)

    def test_the_message_names_everything_that_is_missing(self):
        blocked = validate_runner.submission_block(self.bundle, self.user)
        self.assertIn('Citation Information', blocked[1])
        self.assertIn('Modification History', blocked[1])
        self.assertIn('target', blocked[1].lower())

    def test_posting_the_submit_form_anyway_does_not_submit(self):
        self.client.login(username='gate', password='pw')
        response = self.client.post(
            reverse('build:submit_bundle_internal', args=[self.bundle.pk]))
        self.assertIn(response.status_code, (200, 302))
        self.bundle.refresh_from_db()
        self.assertIsNone(self.bundle.submitted_at)


class PanelTests(TestCase):
    """What the person actually reads."""

    def setUp(self):
        self.archive = tempfile.mkdtemp(prefix='elsa-reqpanel-')
        self.addCleanup(shutil.rmtree, self.archive, True)
        self.media = tempfile.mkdtemp(prefix='elsa-reqpanel-media-')
        self.addCleanup(shutil.rmtree, self.media, True)
        patcher = override_settings(ARCHIVE_DIR=self.archive, MEDIA_ROOT=self.media)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.user = User.objects.create_user('reqpanel', password='pw')
        self.client.login(username='reqpanel', password='pw')
        Investigation.objects.create(
            name='Atmospheric Modeling Annex', type_of='Individual Investigation',
            lid='urn:nasa:pds:context:investigation:individual.atmospheric_modeling_annex',
            file_ref='')
        response = self.client.post(reverse('build:build'), {
            'name': 'req panel bundle', 'bundle_type': 'External',
            'version': '1O00', 'bundleID': ''})
        self.assertIn(response.status_code, (200, 302))
        self.bundle = Bundle.objects.get(name='req panel bundle')

        Citation_Information.objects.create(
            bundle=self.bundle, description='A bundle', publication_year='2026',
            number_of_authors_people=1)
        Modification_History.objects.create(
            bundle=self.bundle, description='Initial delivery',
            modification_date='2026-09-18', version_id='1.0')
        with_data(self.bundle)

    def clean_run(self):
        """A completed check that found nothing, which is what PDS really reports
        for a bundle whose only problem is that it has no target."""
        return ValidationRun.objects.create(
            bundle=self.bundle, status=ValidationRun.STATUS_DONE,
            products_total=2, products_done=2, phase=ValidationRun.PHASE_DONE,
            findings=[], error_count=0,
            bundle_updated_at=self.bundle.updated_at)

    def page(self):
        response = self.client.get(reverse('build:bundle', args=[self.bundle.pk]))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_a_clean_run_on_a_bundle_with_no_target_does_not_say_passed(self):
        self.clean_run()
        body = self.page()
        self.assertNotIn('passed.', body)
        self.assertNotIn('Nothing is blocking this bundle', body)

    def test_it_says_what_to_do_instead(self):
        self.clean_run()
        body = self.page()
        self.assertIn('Choose at least one target', body)
        self.assertIn('Context Products', body)

    def test_it_says_the_validator_is_silent_on_purpose(self):
        self.clean_run()
        self.assertIn('PDS4 allows it to be left out', self.page())

    def test_the_requirement_shows_before_any_check_has_run(self):
        # No ValidationRun at all. The requirement costs a query, not a JVM.
        body = self.page()
        self.assertIn('Choose at least one target', body)

    def test_an_unchecked_bundle_is_still_told_it_is_unchecked(self):
        with override_settings(VALIDATE_AUTO_CHECK=False):
            body = self.page()
        self.assertIn('has not been checked yet', body)
        self.assertNotIn('Run it again', body)

    def test_a_bundle_with_everything_passes_again(self):
        target = Target.objects.create(
            name='Mars', type_of='Planet',
            lid='urn:nasa:pds:context:target:planet.mars', file_ref='')
        self.bundle.targets.add(target)
        self.clean_run()
        body = self.page()
        self.assertIn('passed.', body)
        self.assertNotIn('Choose at least one target', body)

    def test_the_progress_bar_and_the_panel_agree_about_targets(self):
        # The bundle page builds its own richer status_dict for the progress bar.
        # These are the three values it shares with the panel; if they ever drift,
        # the page contradicts itself again.
        self.clean_run()
        response = self.client.get(reverse('build:bundle', args=[self.bundle.pk]))
        status = response.context['status_dict']
        for key, value in preflight.status(self.bundle).items():
            self.assertEqual(status[key], value, key)


class ExternalOnlyRequirementsTests(TestCase):
    """At least one author, and at least one processed NetCDF file.

    Both are gaps PDS4 leaves open and the real validator was shown to say nothing
    about: a citation may omit List_Author entirely, and a bundle may carry no data.
    External only for now; an Archive bundle cannot pass validation through ELSA yet,
    and a gate must never block on something ELSA itself cannot produce.
    """

    def setUp(self):
        self.user = User.objects.create_user('ext', password='pw')

    def bundle(self, bundle_type='External', authors=1, organizations=0, data=True,
               citation=True):
        bundle = Bundle.objects.create(name='{} {}'.format(bundle_type, Bundle.objects.count()),
                                       user=self.user, version='1O00',
                                       bundle_type=bundle_type)
        if citation:
            Citation_Information.objects.create(
                bundle=bundle, description='A bundle', publication_year='2026',
                number_of_authors_people=authors,
                number_of_authors_organization=organizations)
        Modification_History.objects.create(
            bundle=bundle, description='Initial delivery',
            modification_date='2026-09-18', version_id='1.0')
        target, _ = Target.objects.get_or_create(
            lid='urn:nasa:pds:context:target:planet.mars',
            defaults={'name': 'Mars', 'type_of': 'Planet', 'file_ref': ''})
        bundle.targets.add(target)
        if data:
            with_data(bundle)
        return bundle

    def keys(self, bundle):
        return [item['key'] for item in preflight.requirements(bundle)]

    # -- authors -------------------------------------------------------------------

    def test_a_citation_with_no_authors_is_not_enough(self):
        self.assertEqual(self.keys(self.bundle(authors=0)), ['author-missing'])

    def test_one_person_is_enough(self):
        self.assertEqual(self.keys(self.bundle(authors=1)), [])

    def test_one_organization_is_enough(self):
        self.assertEqual(self.keys(self.bundle(authors=0, organizations=1)), [])

    def test_no_citation_asks_for_the_citation_not_its_authors(self):
        self.assertEqual(self.keys(self.bundle(citation=False)), ['citation-missing'])

    def test_the_author_item_is_fixed_in_citation_information(self):
        item = preflight.requirements(self.bundle(authors=0))[0]
        self.assertEqual(item['anchor'], 'citation_information_modal')
        self.assertEqual(item['source'], 'elsa')
        self.assertIn('PDS4', item['detail'])

    # -- NetCDF --------------------------------------------------------------------

    def test_no_netcdf_is_not_enough(self):
        self.assertEqual(self.keys(self.bundle(data=False)), ['netcdf-missing'])

    def test_a_file_elsa_could_not_process_does_not_count(self):
        bundle = self.bundle(data=False)
        NetCDFFile.objects.create(bundle=bundle, title='bad.nc', file='bad.nc',
                                  processed=False, processing_error='not NetCDF')
        self.assertEqual(self.keys(bundle), ['netcdf-missing'])

    def test_one_processed_file_is_enough(self):
        bundle = self.bundle(data=False)
        NetCDFFile.objects.create(bundle=bundle, title='bad.nc', file='bad.nc', processed=False)
        with_data(bundle)
        self.assertEqual(self.keys(bundle), [])

    def test_another_bundles_file_does_not_count(self):
        with_data(self.bundle(data=False))
        self.assertEqual(self.keys(self.bundle(data=False)), ['netcdf-missing'])

    def test_the_netcdf_item_takes_you_to_the_collections_card(self):
        item = preflight.requirements(self.bundle(data=False))[0]
        self.assertEqual(item['scroll_to'], 'collections_card')
        self.assertEqual(item['card'], 'NetCDF Files')
        self.assertIn('PDS4', item['detail'])

    # -- Archive is untouched -----------------------------------------------------

    def test_archive_is_not_asked_for_either(self):
        bundle = self.bundle(bundle_type='Archive', authors=0, data=False)
        self.assertEqual(self.keys(bundle), [])

    # -- everything that says "ready" agrees ---------------------------------------

    def test_the_bundle_hub_does_not_call_it_ready(self):
        self.assertEqual(self.bundle(data=False).get_status(), 'in_progress')
        self.assertEqual(self.bundle(authors=0).get_status(), 'in_progress')
        self.assertEqual(self.bundle().get_status(), 'ready')
        self.assertEqual(self.bundle(bundle_type='Archive', authors=0,
                                     data=False).get_status(), 'ready')

    def test_the_gate_refuses_and_says_why(self):
        blocked = validate_runner.submission_block(self.bundle(data=False), self.user)
        self.assertEqual(blocked[0], validate_runner.BLOCK_REQUIREMENTS)
        self.assertIn('Upload at least one NetCDF file', blocked[1])

        blocked = validate_runner.submission_block(self.bundle(authors=0), self.user)
        self.assertIn('Add at least one author to the citation', blocked[1])

    def test_staff_are_held_to_them_too(self):
        staff = User.objects.create_user('ext_staff', password='pw', is_staff=True)
        blocked = validate_runner.submission_block(self.bundle(data=False), staff)
        self.assertEqual(blocked[0], validate_runner.BLOCK_REQUIREMENTS)

    def test_a_posted_submission_is_refused(self):
        bundle = self.bundle(authors=0)
        self.client.login(username='ext', password='pw')
        self.client.post(reverse('build:submit_bundle_internal', args=[bundle.pk]))
        bundle.refresh_from_db()
        self.assertIsNone(bundle.submitted_at)


class CitationFormAuthorTests(TestCase):
    """The author requirement, enforced where an author can actually be added.

    The number of authors is fixed when a citation is created; the edit form fills
    in names but cannot change the count. So an External citation is refused without
    one, instead of being accepted and then impossible to fix short of deleting it.
    """

    def setUp(self):
        self.user = User.objects.create_user('citeform', password='pw')

    def form(self, bundle_type, people, organizations, bundle=True):
        from build.forms import CitationInformationForm
        kwargs = {}
        if bundle:
            kwargs['bundle'] = Bundle.objects.create(
                name='cite {}'.format(Bundle.objects.count()), user=self.user,
                version='1O00', bundle_type=bundle_type)
        return CitationInformationForm({
            'number_of_authors_people': people,
            'number_of_authors_organization': organizations,
            'number_of_editors_people': 0, 'number_of_editors_organization': 0,
            'publication_year': '2026', 'description': 'A bundle', 'keyword': ''}, **kwargs)

    def test_external_with_no_author_is_refused_and_says_why(self):
        form = self.form('External', 0, 0)
        self.assertFalse(form.is_valid())
        self.assertIn('Add at least one author', ' '.join(form.non_field_errors()))

    def test_external_with_a_person_or_an_organization_is_accepted(self):
        self.assertTrue(self.form('External', 1, 0).is_valid())
        self.assertTrue(self.form('External', 0, 1).is_valid())

    def test_archive_is_unchanged(self):
        self.assertTrue(self.form('Archive', 0, 0).is_valid())

    def test_a_caller_that_does_not_say_which_bundle_is_unchanged(self):
        self.assertTrue(self.form('External', 0, 0, bundle=False).is_valid())
