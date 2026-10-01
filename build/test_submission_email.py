# -*- coding: utf-8 -*-
"""What the node is told when a bundle is submitted.

The notification used to say only that a bundle had arrived, so whoever opened it
still had to find it in ELSA and check it before knowing whether there was anything
to do. Reducing exactly that back-and-forth is why the validator runs at all, so
the answer belongs in the message.
"""

from __future__ import unicode_literals

import shutil
import tempfile

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from build.models import Bundle, ValidationRun
from build.requirement_fixture import satisfy_requirements
from build.views import validation_summary_for_email

CITATION_FINDING = {
    'severity': 'ERROR', 'type': 'error.label.schematron',
    'message': 'In Product_Bundle both Citation_Information and its description are required.',
    'label': 'b.xml', 'label_path': '/b.xml', 'line': 1,
    'element_path': 'Product_Bundle/Identification_Area',
}

# A warning nobody has written a rule for. Deliberately not a context name
# mismatch: those are counted against ELSA rather than shown, since context names
# come from the registry through ELSA and a submitter cannot act on them.
ADVISORY_FINDING = {
    'severity': 'WARNING', 'type': 'warning.label.something_unusual',
    'message': 'A warning nobody has written a rule for yet.',
    'label': 'b.xml', 'label_path': '/b.xml', 'line': 3, 'element_path': '',
}

ELSA_FINDING = {
    'severity': 'ERROR', 'type': 'error.label.schema',
    'message': "cvc-minLength-valid: Value '' with length = '0' is not facet-valid.",
    'label': 'b.xml', 'label_path': '/b.xml', 'line': 9,
    'element_path': 'Product_Bundle/Context_Area/Time_Coordinates/start_date_time',
}


class SubmissionEmailSummaryTests(TestCase):

    def setUp(self):
        self.archive = tempfile.mkdtemp(prefix='elsa-email-')
        self.addCleanup(shutil.rmtree, self.archive, True)
        patcher = override_settings(ARCHIVE_DIR=self.archive)
        patcher.enable()
        self.addCleanup(patcher.disable)

        from django.contrib.auth.models import User
        self.user = User.objects.create_user('emailer', password='pw',
                                             email='e@example.com')
        self.bundle = Bundle.objects.create(
            name='email bundle', user=self.user, version='1O00',
            bundle_type='External')
        # A bundle that reaches the notification has passed the gate, so it has met
        # ELSA's requirements by definition.
        satisfy_requirements(self.bundle)

    def run_with(self, findings, status=ValidationRun.STATUS_DONE, **kwargs):
        defaults = dict(bundle=self.bundle, tier=ValidationRun.TIER_STRUCTURE,
                        status=status, findings=findings, products_total=3,
                        products_done=3, bundle_updated_at=self.bundle.updated_at,
                        content_fingerprint=self.bundle.content_fingerprint())
        defaults.update(kwargs)
        run = ValidationRun.objects.create(**defaults)
        ValidationRun.objects.filter(pk=run.pk).update(finished_at=timezone.now())
        run.refresh_from_db()
        return run

    # -- the summary -------------------------------------------------------------

    def test_a_clean_bundle_says_so(self):
        self.run_with([])
        summary = validation_summary_for_email(self.bundle)
        self.assertIn('3 label(s) checked', summary)
        self.assertIn('Nothing reported', summary)

    def test_advisory_items_are_listed_because_a_reviewer_sees_them(self):
        self.run_with([ADVISORY_FINDING])
        summary = validation_summary_for_email(self.bundle)
        self.assertIn('Worth a look during review', summary)
        self.assertIn('pds noted something, but it does not block you', summary.lower())

    def test_elsa_findings_are_counted_not_listed(self):
        """Our backlog. A number is enough to know whether to mention it."""
        self.run_with([ELSA_FINDING, ELSA_FINDING])
        summary = validation_summary_for_email(self.bundle)
        self.assertIn('caused by ELSA', summary)
        self.assertNotIn('cvc-minLength-valid', summary)

    def test_a_bundle_with_nothing_outstanding_says_that_too(self):
        self.run_with([ELSA_FINDING])
        self.assertIn('Nothing outstanding for the submitter',
                      validation_summary_for_email(self.bundle))

    def test_outstanding_user_items_are_named(self):
        """The gate should have stopped these, so seeing one is worth knowing."""
        self.run_with([CITATION_FINDING])
        summary = validation_summary_for_email(self.bundle)
        self.assertIn('Still outstanding for the submitter', summary)
        self.assertIn('Citation Information', summary)

    def test_a_failed_check_explains_itself_and_does_not_alarm(self):
        self.run_with([], status=ValidationRun.STATUS_FAILED,
                      failure_reason='validate is not installed on this host')
        summary = validation_summary_for_email(self.bundle)
        self.assertIn('could not run', summary)
        self.assertIn('not installed', summary)
        self.assertIn('accepted anyway', summary)

    def test_a_bundle_never_checked_says_so(self):
        self.assertIn('not run', validation_summary_for_email(self.bundle))

    def test_stale_results_are_flagged(self):
        self.run_with([])
        self.bundle.save()
        self.assertIn('out of date', validation_summary_for_email(self.bundle))

    def test_no_raw_pds_jargon_reaches_the_node_summary(self):
        self.run_with([CITATION_FINDING, ELSA_FINDING, ADVISORY_FINDING])
        summary = validation_summary_for_email(self.bundle)
        for jargon in ('cvc-', 'facet-valid', 'element_path'):
            self.assertNotIn(jargon, summary)

    # -- the email itself --------------------------------------------------------

    @override_settings(VALIDATE_BLOCKS_SUBMISSION=False)
    def test_the_notification_carries_the_summary(self):
        self.run_with([])
        self.client.login(username='emailer', password='pw')
        self.client.post(
            reverse('build:submit_bundle_internal', args=[self.bundle.pk]))
        self.assertEqual(len(mail.outbox), 1, 'no notification was sent')
        self.assertIn('PDS validation', mail.outbox[0].body)
        self.assertIn('Nothing reported', mail.outbox[0].body)

    @override_settings(VALIDATE_BLOCKS_SUBMISSION=False)
    def test_the_rest_of_the_notification_is_unchanged(self):
        self.run_with([])
        self.client.login(username='emailer', password='pw')
        self.client.post(
            reverse('build:submit_bundle_internal', args=[self.bundle.pk]))
        body = mail.outbox[0].body
        for expected in ('Bundle: email bundle', 'Archive Path:', 'Download URL:'):
            self.assertIn(expected, body)

    def test_repeated_items_name_the_thing_they_are_about(self):
        """Three documents with bad file names are three identical lines otherwise."""
        def bad_name(label):
            return {'severity': 'ERROR',
                    'type': 'error.file.name_has_invalid_characters',
                    'message': 'File name uses invalid character', 'label': '',
                    'label_path': '/archive/u/b/document/' + label,
                    'line': None, 'element_path': ''}
        self.run_with([bad_name('one.xml'), bad_name('two.xml')])
        summary = validation_summary_for_email(self.bundle)
        self.assertIn('one.xml', summary)
        self.assertIn('two.xml', summary)
