"""The three tiers, and when a check starts by itself.

The cheap tier runs while someone works and skips reading inside data files. The
full tier runs once, at submission. Which one runs where is worth testing, because
getting it backwards means either a slow bundle page or an unchecked submission.

The auto-check rules matter more than they look. Each run is a JVM, so the
question is not only "are the results fresh" but "how many processes does a busy
afternoon start".
"""
import os
import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from build.models import Bundle, Investigation, ValidationRun
from build import validate_runner


class TierSelectionTests(TestCase):

    def setUp(self):
        self.workdir = tempfile.mkdtemp(prefix='elsa-tiers-')
        self.addCleanup(shutil.rmtree, self.workdir, True)
        user = User.objects.create_user('tiers', password='pw')
        self.bundle = Bundle.objects.create(
            name='tier bundle', user=user, version='1O00', bundle_type='External')

        home = os.path.join(self.workdir, 'validate-home')
        os.makedirs(os.path.join(home, 'bin'))
        open(os.path.join(home, 'bin', 'validate'), 'w').close()
        self.home = home

    def command_for(self, tier):
        run = ValidationRun.objects.create(bundle=self.bundle, tier=tier)
        with override_settings(VALIDATE_HOME=self.home, VALIDATE_WORK_DIR=self.workdir):
            return validate_runner.build_command(run, os.path.join(self.workdir, 'r.json'))

    def test_the_working_tier_skips_reading_data_files(self):
        """This is what makes it cheap enough to run while someone works."""
        self.assertIn('--skip-content-validation',
                      self.command_for(ValidationRun.TIER_STRUCTURE))

    def test_the_submission_tier_reads_the_data(self):
        self.assertNotIn('--skip-content-validation',
                         self.command_for(ValidationRun.TIER_FULL))

    def test_both_tiers_check_references_between_products(self):
        for tier in (ValidationRun.TIER_STRUCTURE, ValidationRun.TIER_FULL):
            self.assertIn('pds4.bundle', self.command_for(tier))


class AutoCheckTests(TestCase):
    """When the page is allowed to start a check without being asked."""

    def setUp(self):
        user = User.objects.create_user('auto', password='pw')
        self.bundle = Bundle.objects.create(
            name='auto bundle', user=user, version='1O00')

    def finished_run(self, ago_seconds=0, status=ValidationRun.STATUS_DONE,
                     stale=False):
        run = ValidationRun.objects.create(
            bundle=self.bundle, tier=ValidationRun.TIER_STRUCTURE, status=status,
            bundle_updated_at=self.bundle.updated_at)
        when = timezone.now() - timezone.timedelta(seconds=ago_seconds)
        ValidationRun.objects.filter(pk=run.pk).update(
            finished_at=when, requested_at=when)
        if stale:
            self.bundle.save()          # auto_now moves updated_at past the snapshot
        run.refresh_from_db()
        return run

    def test_a_bundle_never_checked_is_checked(self):
        with override_settings(VALIDATE_AUTO_CHECK=True):
            self.assertTrue(validate_runner.should_auto_check(self.bundle))

    def test_fresh_results_are_left_alone(self):
        self.finished_run(ago_seconds=10)
        with override_settings(VALIDATE_AUTO_CHECK=True):
            self.assertFalse(validate_runner.should_auto_check(self.bundle))

    def test_results_that_no_longer_describe_the_bundle_are_refreshed(self):
        self.finished_run(ago_seconds=9999, stale=True)
        with override_settings(VALIDATE_AUTO_CHECK=True,
                               VALIDATE_AUTO_CHECK_COOLDOWN_SECONDS=300):
            self.assertTrue(validate_runner.should_auto_check(self.bundle))

    def test_the_cooldown_stops_a_reload_starting_another_run(self):
        """Someone fixing things reloads repeatedly; each reload must not cost a JVM."""
        self.finished_run(ago_seconds=5, stale=True)
        with override_settings(VALIDATE_AUTO_CHECK=True,
                               VALIDATE_AUTO_CHECK_COOLDOWN_SECONDS=300):
            self.assertFalse(validate_runner.should_auto_check(self.bundle))

    def test_a_run_in_flight_is_never_duplicated(self):
        ValidationRun.objects.create(
            bundle=self.bundle, status=ValidationRun.STATUS_RUNNING)
        with override_settings(VALIDATE_AUTO_CHECK=True):
            self.assertFalse(validate_runner.should_auto_check(self.bundle))

    def test_a_failed_run_is_not_retried_automatically(self):
        """Whatever stopped it will stop the next one. Silent retries are worse."""
        self.finished_run(ago_seconds=9999, status=ValidationRun.STATUS_FAILED,
                          stale=True)
        with override_settings(VALIDATE_AUTO_CHECK=True,
                               VALIDATE_AUTO_CHECK_COOLDOWN_SECONDS=1):
            self.assertFalse(validate_runner.should_auto_check(self.bundle))

    def test_the_whole_thing_can_be_turned_off(self):
        with override_settings(VALIDATE_AUTO_CHECK=False):
            self.assertFalse(validate_runner.should_auto_check(self.bundle))


class AutoCheckOnThePageTests(TestCase):
    """The flag reaches the page, and the view still starts nothing itself."""

    def setUp(self):
        self.archive = tempfile.mkdtemp(prefix='elsa-autopage-')
        self.addCleanup(shutil.rmtree, self.archive, True)
        self.media = tempfile.mkdtemp(prefix='elsa-autopage-media-')
        self.addCleanup(shutil.rmtree, self.media, True)
        patcher = override_settings(ARCHIVE_DIR=self.archive, MEDIA_ROOT=self.media)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.user = User.objects.create_user('autopage', password='pw')
        self.client.login(username='autopage', password='pw')
        Investigation.objects.create(
            name='Atmospheric Modeling Annex', type_of='Individual Investigation',
            lid='urn:nasa:pds:context:investigation:individual.atmospheric_modeling_annex',
            file_ref='')
        self.client.post(reverse('build:build'), {
            'name': 'auto page bundle', 'bundle_type': 'External',
            'version': '1O00', 'bundleID': ''})
        self.bundle = Bundle.objects.get(name='auto page bundle')

    def page(self):
        return self.client.get(reverse('build:bundle', args=[self.bundle.pk]))

    def test_the_page_is_told_to_check(self):
        with override_settings(VALIDATE_AUTO_CHECK=True):
            self.assertContains(self.page(), 'data-auto-check="1"')

    def test_the_page_is_not_told_to_check_when_it_is_off(self):
        with override_settings(VALIDATE_AUTO_CHECK=False):
            self.assertNotContains(self.page(), 'data-auto-check="1"')

    def test_rendering_the_page_still_starts_nothing(self):
        """A crawler, a link preview or a HEAD request must not spawn a JVM.

        The flag only tells the browser it would be worth starting one. Nothing
        server-side acts on it.
        """
        with override_settings(VALIDATE_AUTO_CHECK=True):
            self.page()
            self.page()
            self.page()
        self.assertEqual(ValidationRun.objects.filter(bundle=self.bundle).count(), 0)


class SubmissionTierTests(TestCase):
    """Submitting runs the expensive check once, and never blocks on it."""

    def setUp(self):
        self.archive = tempfile.mkdtemp(prefix='elsa-submit-')
        self.addCleanup(shutil.rmtree, self.archive, True)
        self.user = User.objects.create_user(
            'submitter', password='pw', email='s@example.com')
        self.client.login(username='submitter', password='pw')
        patcher = override_settings(ARCHIVE_DIR=self.archive)
        patcher.enable()
        self.addCleanup(patcher.disable)
        self.bundle = Bundle.objects.create(
            name='submit bundle', user=self.user, version='1O00', bundle_type='External')

    def submit(self):
        return self.client.post(
            reverse('build:submit_bundle_internal', args=[self.bundle.pk]))

    def test_submitting_starts_a_full_check(self):
        from unittest import mock
        with mock.patch.object(validate_runner.subprocess, 'Popen'):
            with override_settings(VALIDATE_HOME='', VALIDATE_WORK_DIR=self.archive):
                self.submit()
        run = ValidationRun.objects.filter(bundle=self.bundle).first()
        self.assertIsNotNone(run)
        self.assertEqual(run.tier, ValidationRun.TIER_FULL)

    def test_a_submission_succeeds_even_if_validation_cannot_start(self):
        """Validation is a service to the submission, never a gate on it."""
        from unittest import mock
        with mock.patch.object(validate_runner, 'start',
                               side_effect=RuntimeError('validate exploded')):
            response = self.submit()
        self.assertIn(response.status_code, (200, 302))
        self.bundle.refresh_from_db()
        self.assertIsNotNone(self.bundle.submitted_at)
