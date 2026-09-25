"""The Review & Submit flow: one list, a check before submitting, and a quiet background.

The Review & Submit window used to judge readiness twice, once from its own checklist
(did Modification History, Citation Information and Targets exist) and once from the
validation verdict above it. It now shows the validation panel's own list, beside an
inventory of what is being sent, and checks the bundle itself when it opens on one that
has changed.

A refusal because the server was busy used to be stored as a failed run. It was then
read as the bundle's latest result: the badge stuck at "Could not run", the list lost
the findings of the check before it, and nothing retried. It is no longer stored.
"""
import shutil
import tempfile
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from build import validate_runner
from build.models import Bundle, Investigation, ValidationRun
from build.requirement_fixture import satisfy_requirements


CITATION = {
    'severity': 'ERROR', 'type': 'error.label.schematron',
    'message': 'In Product_Bundle both Citation_Information and its description are required.',
    'label': 'b.xml', 'label_path': '/b.xml', 'line': 1,
    'element_path': 'Product_Bundle/Identification_Area',
}


@override_settings(VALIDATE_BLOCKS_SUBMISSION=True, VALIDATE_AUTO_CHECK=True)
class ReviewAndSubmitTests(TestCase):

    def setUp(self):
        self.archive = tempfile.mkdtemp(prefix='elsa-review-')
        self.addCleanup(shutil.rmtree, self.archive, True)
        self.media = tempfile.mkdtemp(prefix='elsa-review-media-')
        self.addCleanup(shutil.rmtree, self.media, True)
        patcher = override_settings(ARCHIVE_DIR=self.archive, MEDIA_ROOT=self.media,
                                    VALIDATE_WORK_DIR=self.archive)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.user = User.objects.create_user('reviewer', password='pw')
        self.client.login(username='reviewer', password='pw')
        Investigation.objects.create(
            name='Atmospheric Modeling Annex', type_of='Individual Investigation',
            lid='urn:nasa:pds:context:investigation:individual.atmospheric_modeling_annex',
            file_ref='')
        self.client.post(reverse('build:build'), {
            'name': 'review bundle', 'bundle_type': 'External',
            'version': '1O00', 'bundleID': ''})
        self.bundle = Bundle.objects.get(name='review bundle')
        satisfy_requirements(self.bundle)

    def checked(self, findings, **kwargs):
        defaults = dict(bundle=self.bundle, tier=ValidationRun.TIER_STRUCTURE,
                        status=ValidationRun.STATUS_DONE, findings=findings,
                        products_total=2, products_done=2, finished_at=timezone.now(),
                        content_fingerprint=self.bundle.content_fingerprint())
        defaults.update(kwargs)
        return ValidationRun.objects.create(**defaults)

    def page(self):
        response = self.client.get(reverse('build:bundle', args=[self.bundle.pk]))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def fragments(self):
        return self.client.get(
            reverse('build:validation_panel', args=[self.bundle.pk])).content.decode()

    def review_window(self, body):
        return body.split('id="reviewBundleModal"')[1].split('<!-- Form Modals -->')[0]

    def changed(self):
        """Change a file under the bundle, which is what makes a result stale."""
        import os
        os.makedirs(self.bundle.directory(), exist_ok=True)
        with open(os.path.join(self.bundle.directory(), 'edited.xml'), 'w') as handle:
            handle.write('<x/>')

    # -- which bundles need a check ---------------------------------------------

    def test_a_bundle_never_checked_needs_one(self):
        self.assertTrue(validate_runner.needs_check(self.bundle))

    def test_a_bundle_unchanged_since_its_check_does_not(self):
        self.checked([])
        self.assertFalse(validate_runner.needs_check(self.bundle))

    def test_a_bundle_changed_since_its_check_does(self):
        self.checked([])
        self.changed()
        self.assertTrue(validate_runner.needs_check(self.bundle))

    def test_nothing_is_started_while_a_check_is_in_flight(self):
        self.checked([], status=ValidationRun.STATUS_RUNNING, finished_at=None)
        self.changed()
        self.assertFalse(validate_runner.needs_check(self.bundle))

    def test_a_failed_check_waits_to_be_asked(self):
        self.checked([], status=ValidationRun.STATUS_FAILED, failure_reason='no validate')
        self.changed()
        self.assertFalse(validate_runner.needs_check(self.bundle))

    def test_the_status_endpoint_says_whether_a_check_is_needed(self):
        url = reverse('build:validation_status', args=[self.bundle.pk])
        self.assertTrue(self.client.get(url).json()['needs_check'])
        self.checked([])
        self.assertFalse(self.client.get(url).json()['needs_check'])

    def test_the_status_count_includes_elsas_requirements(self):
        """The badge is redrawn from this number when a check finishes. Counting PDS's
        findings alone turned it to "Passed" on a bundle with no target."""
        self.bundle.targets.clear()
        self.checked([])
        state = self.client.get(
            reverse('build:validation_status', args=[self.bundle.pk])).json()
        self.assertEqual(state['blocking'], 1)

        badge = self.page().split('id="preflight_row_badge"')[1].split('</span>')[0]
        self.assertIn('1 to fix', badge)

    def test_the_count_after_a_real_start_includes_them_too(self):
        self.bundle.targets.clear()
        with mock.patch.object(validate_runner.subprocess, 'Popen'):
            response = self.client.post(
                reverse('build:start_validation', args=[self.bundle.pk]))
        self.assertEqual(response.json()['blocking'], 1)

    # -- a busy server is not a result ------------------------------------------

    def test_a_busy_refusal_is_not_stored(self):
        other = Bundle.objects.create(name='other', user=self.user, version='1O00')
        ValidationRun.objects.create(bundle=other, status=ValidationRun.STATUS_RUNNING)
        before = ValidationRun.objects.count()
        with override_settings(VALIDATE_MAX_CONCURRENT=1):
            response = self.client.post(
                reverse('build:start_validation', args=[self.bundle.pk]))
        self.assertTrue(response.json()['busy'])
        self.assertEqual(ValidationRun.objects.count(), before)

    def test_a_busy_refusal_leaves_the_last_result_standing(self):
        self.checked([CITATION])
        other = Bundle.objects.create(name='other', user=self.user, version='1O00')
        ValidationRun.objects.create(bundle=other, status=ValidationRun.STATUS_RUNNING)
        with override_settings(VALIDATE_MAX_CONCURRENT=1):
            validate_runner.start(self.bundle)
        body = self.page()
        badge = body.split('id="preflight_row_badge"')[1].split('</span>')[0]
        self.assertIn('1 to fix', badge)
        self.assertNotIn('Could not run', badge)

    # -- the list follows the check the verdict rests on ------------------------

    def test_a_failed_recheck_does_not_empty_the_list_the_gate_is_reading(self):
        self.checked([CITATION])
        self.checked([], status=ValidationRun.STATUS_FAILED, failure_reason='timed out')
        self.assertIsNotNone(validate_runner.submission_block(self.bundle, self.user))
        self.assertIn('Add Citation Information', self.review_window(self.page()))

    # -- one list, in the Review & Submit window ---------------------------------

    def test_the_review_window_shows_the_validation_list(self):
        self.checked([CITATION])
        window = self.review_window(self.page())
        self.assertIn('Before you can submit', window)
        self.assertIn('Add Citation Information, including a description', window)
        # Fixed from inside another window, so it has to close this one first.
        self.assertIn("switchToModal('reviewBundleModal', 'citation_information_modal')", window)

    def test_the_review_window_no_longer_keeps_a_checklist_of_its_own(self):
        self.checked([])
        window = self.review_window(self.page())
        # This bundle has no documents or data files, so the only ticks the window
        # could show are the old checklist's: one each for Modification History,
        # Citation Information and Targets, all met here.
        self.assertEqual(window.count('bi-check-circle-fill text-success'), 0)
        self.assertNotIn('bi-x-circle-fill text-danger', window)
        self.assertIn('What you are sending', window)

    def test_the_inventory_names_the_targets(self):
        self.checked([])
        self.assertIn('Mars', self.review_window(self.page()))

    def test_requirements_appear_before_any_check(self):
        """ELSA's own requirements need no JVM, so they are listed straight away."""
        self.bundle.targets.clear()
        window = self.review_window(self.page())
        self.assertIn('Choose at least one target', window)

    def test_a_clean_bundle_says_nothing_needs_changing(self):
        self.checked([])
        window = self.review_window(self.page())
        self.assertIn('Nothing needs changing', window)
        self.assertIn('ready to submit', window)

    # -- everything that shows the verdict refreshes together --------------------

    def test_the_fragments_carry_the_list_and_both_submit_buttons(self):
        self.checked([])
        fragments = self.fragments()
        for piece in ('id="validation_verdict"', 'id="review_submit_action"',
                      'id="review_findings"', 'id="submit_confirm_action"'):
            self.assertIn(piece, fragments)

    def test_the_confirmation_window_follows_a_fresh_check(self):
        """It was rendered once, at page load: cleared in Review & Submit, then refused here."""
        stale = self.checked([])
        self.changed()
        confirm = self.fragments().split('id="submit_confirm_action"')[1]
        self.assertIn('Not ready to submit', confirm.split('/submit_confirm_action')[0])

        self.checked([])                        # a fresh check of the changed files
        confirm = self.fragments().split('id="submit_confirm_action"')[1]
        confirm = confirm.split('/submit_confirm_action')[0]
        self.assertNotIn('Not ready to submit', confirm)
        self.assertIn(reverse('build:submit_bundle_internal', args=[self.bundle.pk]), confirm)
        self.assertTrue(stale.is_stale())

    def test_the_verdict_counts_what_the_list_shows(self):
        """The gate names only ELSA's own requirements when any are missing; the band
        used to quote it, and read "One thing is still needed" above a longer list."""
        self.bundle.targets.clear()
        self.checked([CITATION])
        verdict = self.page().split('id="verdict_result"')[1].split('/validation_verdict')[0]
        self.assertIn('2 things to change before this bundle can be submitted', verdict)
        self.assertNotIn('One thing is still needed', verdict)


@override_settings(VALIDATE_BLOCKS_SUBMISSION=True, VALIDATE_AUTO_CHECK=False)
class ExternalRequirementsOnThePageTests(ReviewAndSubmitTests.__bases__[0]):
    """The two External-only requirements, as a person meets them on the page."""

    setUp = ReviewAndSubmitTests.setUp
    checked = ReviewAndSubmitTests.checked
    page = ReviewAndSubmitTests.page
    review_window = ReviewAndSubmitTests.review_window

    def remove_data(self):
        self.bundle.netcdf_files.all().delete()

    def remove_authors(self):
        self.bundle.citation_information_set.update(
            number_of_authors_people=0, number_of_authors_organization=0)

    def review_button(self, body):
        return body.split('id="externalSubmitBtn"')[1].split('>')[0]

    def test_the_review_window_asks_for_a_netcdf_file_and_says_where(self):
        self.remove_data()
        self.checked([])
        window = self.review_window(self.page())
        self.assertIn('Upload at least one NetCDF file', window)
        section = window.split('Upload at least one NetCDF file')[1].split('Why does this matter')[0]
        self.assertIn('data-target="collections_card"', section)
        self.assertIn('data-bs-dismiss="modal"', section)

    def test_the_review_window_asks_for_an_author_and_opens_the_citation(self):
        self.remove_authors()
        self.checked([])
        window = self.review_window(self.page())
        self.assertIn('Add at least one author to the citation', window)
        self.assertIn("switchToModal('reviewBundleModal', 'citation_information_modal')", window)

    def test_submit_stays_disabled_and_the_band_counts_it(self):
        self.remove_data()
        self.checked([])
        body = self.page()
        verdict = body.split('id="verdict_result"')[1].split('/validation_verdict')[0]
        self.assertIn('1 thing to change before this bundle can be submitted', verdict)
        action = body.split('id="review_submit_action"')[1].split('/review_submit_action')[0]
        self.assertIn('disabled', action)

    def test_the_review_button_is_green_only_when_nothing_is_required(self):
        self.checked([])
        self.assertIn('btn-success', self.review_button(self.page()))
        self.remove_authors()
        self.assertNotIn('btn-success', self.review_button(self.page()))

    def test_the_bundle_hub_says_in_progress(self):
        self.remove_data()
        hub = self.client.get('/accounts/bundles/').content.decode()
        # The status badge sits just after the card's LID line.
        card = hub.split('urn:nasa:pds-ama:review_bundle</p>')[1][:900]
        self.assertIn('In Progress', card)
        self.assertNotIn('Ready', card)
