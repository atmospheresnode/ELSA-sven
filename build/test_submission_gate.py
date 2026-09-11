"""The submission gate.

The rule under test is not "the button is disabled" but "the server refuses". A
disabled button is a courtesy to someone reading the page; it stops nobody who
reloads, scripts the form, or has the page open from before the results changed.

The holes in the gate are tested as carefully as the gate, because each is
deliberate and each would be a bad surprise if it closed: staff are never blocked,
a validation that could not run never blocks, and the whole thing can be switched
off.
"""
import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from build.models import Bundle, ValidationRun
from build import validate_runner


CITATION_FINDING = {
    'severity': 'ERROR', 'type': 'error.label.schematron',
    'message': 'In Product_Bundle both Citation_Information and its description are required.',
    'label': 'b.xml', 'label_path': '/b.xml', 'line': 1,
    'element_path': 'Product_Bundle/Identification_Area',
}

ELSA_FINDING = {
    'severity': 'ERROR', 'type': 'error.label.schema',
    'message': "cvc-minLength-valid: Value '' with length = '0' is not facet-valid.",
    'label': 'b.xml', 'label_path': '/b.xml', 'line': 9,
    'element_path': 'Product_Bundle/Context_Area/Time_Coordinates/start_date_time',
}


@override_settings(VALIDATE_BLOCKS_SUBMISSION=True)
class SubmissionGateTests(TestCase):

    def setUp(self):
        self.archive = tempfile.mkdtemp(prefix='elsa-gate-')
        self.addCleanup(shutil.rmtree, self.archive, True)
        patcher = override_settings(ARCHIVE_DIR=self.archive)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.owner = User.objects.create_user(
            'gateowner', password='pw', email='owner@example.com')
        self.bundle = Bundle.objects.create(
            name='gate bundle', user=self.owner, version='1O00', bundle_type='External')
        self.client.login(username='gateowner', password='pw')

    def checked_with(self, findings, status=ValidationRun.STATUS_DONE, stale=False):
        run = ValidationRun.objects.create(
            bundle=self.bundle, tier=ValidationRun.TIER_STRUCTURE, status=status,
            findings=findings, bundle_updated_at=self.bundle.updated_at,
            products_total=2, products_done=2)
        if stale:
            self.bundle.save()          # auto_now moves updated_at past the snapshot
        run.refresh_from_db()
        return run

    def submit(self):
        return self.client.post(
            reverse('build:submit_bundle_internal', args=[self.bundle.pk]))

    def submitted(self):
        self.bundle.refresh_from_db()
        return self.bundle.submitted_at is not None

    # -- the gate itself ---------------------------------------------------------

    def test_an_unchecked_bundle_cannot_be_submitted(self):
        self.submit()
        self.assertFalse(self.submitted())

    def test_a_bundle_with_findings_cannot_be_submitted(self):
        self.checked_with([CITATION_FINDING])
        self.submit()
        self.assertFalse(self.submitted())

    def test_a_clean_bundle_can_be_submitted(self):
        self.checked_with([])
        self.submit()
        self.assertTrue(self.submitted())

    def test_a_stale_result_does_not_count_as_a_pass(self):
        """The bundle changed after the check, so the result describes something else."""
        self.checked_with([], stale=True)
        self.submit()
        self.assertFalse(self.submitted())

    def test_elsa_findings_do_not_block_the_user(self):
        """Blocking someone over a defect they cannot reach would be indefensible."""
        self.checked_with([ELSA_FINDING])
        self.submit()
        self.assertTrue(self.submitted())

    def test_a_run_still_in_progress_blocks(self):
        self.checked_with([], status=ValidationRun.STATUS_RUNNING)
        self.submit()
        self.assertFalse(self.submitted())

    # -- the deliberate holes ----------------------------------------------------

    def test_a_validation_that_could_not_run_does_not_block(self):
        """If validate is missing or broken, blocking means the node receives nothing.

        Being unable to check is not evidence of a problem, and it is not something
        the person submitting can fix.
        """
        self.checked_with([], status=ValidationRun.STATUS_FAILED)
        self.submit()
        self.assertTrue(self.submitted())

    def test_staff_are_never_blocked(self):
        """The people who would open the gate for a bad rule are the ones operating it."""
        staff = User.objects.create_user(
            'gatestaff', password='pw', email='staff@example.com', is_staff=True)
        self.bundle.user = staff
        self.bundle.save()
        self.client.force_login(staff)
        self.checked_with([CITATION_FINDING])
        self.submit()
        self.assertTrue(self.submitted())

    def test_the_gate_can_be_turned_off(self):
        self.checked_with([CITATION_FINDING])
        with override_settings(VALIDATE_BLOCKS_SUBMISSION=False):
            self.submit()
        self.assertTrue(self.submitted())

    # -- what the person is told -------------------------------------------------

    def test_a_refusal_says_why(self):
        self.checked_with([CITATION_FINDING])
        response = self.client.post(
            reverse('build:submit_bundle_internal', args=[self.bundle.pk]), follow=True)
        text = ' '.join(str(m) for m in response.context['messages'])
        self.assertIn('not submitted', text)
        self.assertIn('need', text)

    def test_an_unchecked_bundle_is_told_to_run_the_check(self):
        response = self.client.post(
            reverse('build:submit_bundle_internal', args=[self.bundle.pk]), follow=True)
        text = ' '.join(str(m) for m in response.context['messages'])
        self.assertIn('validation check', text.lower())

    def test_the_reason_is_the_same_one_the_page_shows(self):
        """The page and the refusal must not disagree about why."""
        self.checked_with([CITATION_FINDING])
        blocked = validate_runner.submission_block(self.bundle, self.owner)
        self.assertIsNotNone(blocked)
        reason, message = blocked
        self.assertEqual(reason, validate_runner.BLOCK_FINDINGS)

        response = self.client.post(
            reverse('build:submit_bundle_internal', args=[self.bundle.pk]), follow=True)
        text = ' '.join(str(m) for m in response.context['messages'])
        self.assertIn(message, text)

    # -- resubmission ------------------------------------------------------------

    def test_a_previously_submitted_bundle_is_blocked_if_it_regresses(self):
        """Resubmission is a submission, and gets the same check."""
        self.checked_with([])
        self.submit()
        self.assertTrue(self.submitted())
        first = self.bundle.submitted_at

        ValidationRun.objects.all().delete()
        self.checked_with([CITATION_FINDING])
        self.submit()
        self.bundle.refresh_from_db()
        self.assertEqual(self.bundle.submitted_at, first, 'resubmission slipped past')
