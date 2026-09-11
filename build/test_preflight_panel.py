"""The pre-flight panel as it renders on the bundle page.

Renders the real page through the real view in each state the panel can be in, and
asserts on what a person would see. The thing being guarded is that a data provider
is never shown a finding they cannot act on, and never shown a raw PDS error code.
"""
import os
import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from build.models import Bundle, Investigation, ValidationRun


def finding(message, type_='error.label.schema', path='', label='bundle_x.xml'):
    return {'severity': 'ERROR', 'type': type_, 'message': message, 'label': label,
            'label_path': '/' + label, 'line': 1, 'element_path': path}


CITATION = finding(
    'In Product_Bundle both Citation_Information and its description are required.',
    type_='error.label.schematron', path='Product_Bundle/Identification_Area')

EMPTY_COLLECTION = finding(
    "cvc-minInclusive-valid: Value '0' is not facet-valid with respect to minInclusive '1'.",
    path='Product_Collection/File_Area_Inventory/Inventory/records')

ELSA_DEFECT = finding(
    "cvc-minLength-valid: Value '' with length = '0' is not facet-valid.",
    path='Product_Bundle/Context_Area/Time_Coordinates/start_date_time')

ADVISORY = {'severity': 'WARNING', 'type': 'warning.label.context_ref_mismatch',
            'message': 'Context reference name mismatch.', 'label': 'bundle_x.xml',
            'label_path': '/bundle_x.xml', 'line': 3, 'element_path': ''}


class PreflightPanelTests(TestCase):

    def setUp(self):
        self.archive = tempfile.mkdtemp(prefix='elsa-panel-')
        self.addCleanup(shutil.rmtree, self.archive, True)
        patcher = override_settings(ARCHIVE_DIR=self.archive)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.media = tempfile.mkdtemp(prefix='elsa-panel-media-')
        self.addCleanup(shutil.rmtree, self.media, True)
        media_patcher = override_settings(MEDIA_ROOT=self.media)
        media_patcher.enable()
        self.addCleanup(media_patcher.disable)

        self.user = User.objects.create_user('panel', password='pw')
        self.client.login(username='panel', password='pw')
        Investigation.objects.create(
            name='Atmospheric Modeling Annex', type_of='Individual Investigation',
            lid='urn:nasa:pds:context:investigation:individual.atmospheric_modeling_annex',
            file_ref='')

        # Built through the real view, because the bundle page redirects away from a
        # Bundle row that has no Product_Bundle behind it.
        response = self.client.post(reverse('build:build'), {
            'name': 'panel bundle', 'bundle_type': 'External',
            'version': '1O00', 'bundleID': ''})
        self.assertIn(response.status_code, (200, 302))
        self.bundle = Bundle.objects.get(name='panel bundle')

    def page(self):
        response = self.client.get(reverse('build:bundle', args=[self.bundle.pk]))
        self.assertEqual(response.status_code, 200)
        return response

    def run_with(self, findings, **kwargs):
        defaults = dict(bundle=self.bundle, status=ValidationRun.STATUS_DONE,
                        products_total=2, products_done=2,
                        phase=ValidationRun.PHASE_DONE, findings=findings,
                        error_count=len(findings), bundle_updated_at=self.bundle.updated_at)
        defaults.update(kwargs)
        return ValidationRun.objects.create(**defaults)

    # -- the states the panel can be in -----------------------------------------

    def test_a_bundle_never_checked_invites_a_check(self):
        page = self.page()
        self.assertContains(page, 'Pre-flight Check')
        self.assertContains(page, 'has not been checked yet')
        self.assertContains(page, 'Run pre-flight check')

    def test_findings_are_shown_in_plain_language(self):
        self.run_with([CITATION])
        page = self.page()
        self.assertContains(page, 'Add Citation Information')
        self.assertContains(page, 'Citation Information')

    def test_a_raw_pds_error_code_never_reaches_the_page(self):
        """The whole point of the translation layer."""
        self.run_with([CITATION, EMPTY_COLLECTION])
        body = self.page().content.decode()
        for jargon in ('cvc-minInclusive-valid', 'cvc-minLength-valid', 'facet-valid',
                       'Product_Collection/File_Area_Inventory'):
            self.assertNotIn(jargon, body, 'raw PDS output leaked to the user')

    def test_an_elsa_defect_is_not_shown_to_the_user(self):
        """A finding they cannot act on is noise, and teaches them to ignore the panel."""
        self.run_with([ELSA_DEFECT])
        body = self.page().content.decode()
        self.assertNotIn('Time_Coordinates', body)
        self.assertNotIn('An empty container that ELSA wrote', body)

    def test_an_elsa_defect_does_not_count_against_the_user(self):
        self.run_with([ELSA_DEFECT])
        page = self.page()
        self.assertEqual(page.context['validation_summary']['blocking'], 0)
        self.assertTrue(page.context['validation_summary']['can_submit'])

    def test_a_clean_bundle_says_so(self):
        self.run_with([ADVISORY])
        page = self.page()
        self.assertContains(page, 'Nothing is blocking this bundle')

    def test_what_passed_is_counted_too(self):
        """A panel that only ever reports failure is one people avoid opening."""
        self.run_with([CITATION], products_total=12)
        self.assertContains(self.page(), 'Labels checked')

    def test_advisory_items_are_shown_separately(self):
        self.run_with([ADVISORY])
        self.assertContains(self.page(), 'Worth reviewing')

    def test_stale_results_are_marked(self):
        run = self.run_with([CITATION])
        self.bundle.save()                      # auto_now bumps updated_at
        run.refresh_from_db()
        self.assertContains(self.page(), 'out of date')

    def test_a_failed_run_explains_itself(self):
        self.run_with([], status=ValidationRun.STATUS_FAILED,
                      failure_reason='validate is not installed on this host')
        self.assertContains(self.page(), 'not installed')

    # -- the Fix buttons ---------------------------------------------------------

    def test_a_fix_button_points_at_the_modal_that_fixes_it(self):
        self.run_with([CITATION])
        self.assertContains(self.page(), 'data-bs-target="#citation_information_modal"')

    def test_items_are_grouped_by_card_not_by_file(self):
        self.run_with([CITATION, EMPTY_COLLECTION])
        page = self.page()
        names = [card for card, _items in page.context['validation_cards']]
        self.assertIn('Citation Information', names)
        self.assertIn('Documents', names)

    def test_one_problem_on_four_labels_reads_as_one_item(self):
        findings = [finding(
            'In Product_Collection both Citation_Information and its description are required.',
            type_='error.label.schematron', path='Product_Collection/Identification_Area',
            label='collection_{}.xml'.format(n)) for n in range(4)]
        self.run_with(findings)
        page = self.page()
        self.assertEqual(page.context['validation_summary']['blocking'], 1)
        self.assertContains(page, 'Affects 4 labels')

    # -- the controls ------------------------------------------------------------

    def test_the_panel_offers_to_run_a_check(self):
        page = self.page()
        self.assertContains(page, 'preflight_run')
        self.assertContains(page, reverse('build:start_validation', args=[self.bundle.pk]))
        self.assertContains(page, reverse('build:validation_status', args=[self.bundle.pk]))

    def test_raw_detail_is_available_but_not_in_the_way(self):
        self.run_with([CITATION])
        page = self.page()
        self.assertContains(page, 'Technical detail')
        self.assertContains(page, '<details')

    def test_the_full_report_link_is_staff_only(self):
        run = self.run_with([CITATION])
        self.assertNotContains(self.page(), 'Full report')

        staff = User.objects.create_user('panelstaff', password='pw', is_staff=True)
        self.bundle.user = staff
        self.bundle.save()
        self.client.force_login(staff)
        self.assertContains(self.page(), 'Full report')

    def test_nothing_is_started_just_by_opening_the_page(self):
        """Opening a bundle must not spawn a JVM."""
        self.page()
        self.assertEqual(ValidationRun.objects.filter(bundle=self.bundle).count(), 0)
