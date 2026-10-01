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

from build.requirement_fixture import satisfy_requirements
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

    def test_the_working_tier_no_longer_skips_reading_data_files(self):
        """Skipping saved nothing and hid a whole class of error.

        Measured against real bundles, content validation costs 6s against 7s on a
        184MB NetCDF bundle and is within noise on both Archive bundles: an AMA data
        product only references its file, so there is nothing inside to read. What
        the skip did cost was error.label.missing_file, which only appears with
        content validation and which every ELSA document currently produces.
        """
        self.assertNotIn('--skip-content-validation',
                         self.command_for(ValidationRun.TIER_STRUCTURE))

    def test_the_submission_tier_reads_the_data(self):
        self.assertNotIn('--skip-content-validation',
                         self.command_for(ValidationRun.TIER_FULL))

    def test_a_host_can_still_turn_content_validation_off(self):
        """The escape hatch, for a bundle with large tables where it is slow."""
        with override_settings(VALIDATE_SKIP_CONTENT=True):
            self.assertIn('--skip-content-validation',
                          self.command_for(ValidationRun.TIER_STRUCTURE))

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

    def test_reloading_an_unchanged_bundle_starts_nothing(self):
        """Someone fixing things reloads repeatedly; each reload must not cost a JVM.

        This used to be a five minute cooldown, which was needed because staleness was
        measured against a timestamp that never moved: without the wait, every single
        load would have started a run. Now an unchanged bundle simply is not stale, so
        the protection holds however many times the page is opened, and it no longer
        costs the person who did change something a five minute wait to see it.
        """
        self.finished_run(ago_seconds=5)
        with override_settings(VALIDATE_AUTO_CHECK=True):
            for _ in range(5):
                self.assertFalse(validate_runner.should_auto_check(self.bundle))

    def test_a_changed_bundle_is_rechecked_without_waiting(self):
        """The complaint this fixes: fix the thing, and the panel says so."""
        self.finished_run(ago_seconds=1, stale=True)
        with override_settings(VALIDATE_AUTO_CHECK=True):
            self.assertTrue(validate_runner.should_auto_check(self.bundle))

    def test_a_host_can_still_impose_a_debounce(self):
        """Kept as a safety valve, off by default."""
        self.finished_run(ago_seconds=1, stale=True)
        with override_settings(VALIDATE_AUTO_CHECK=True,
                               VALIDATE_AUTO_CHECK_DEBOUNCE_SECONDS=300):
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

    def passing_check(self):
        """Everything the submission gate wants, so the request gets through.

        These tests are about which tier a submission runs, not about the gate, and
        a bundle that has never been checked, or that has not met ELSA's own
        requirements, cannot be submitted at all now.
        """
        satisfy_requirements(self.bundle)
        ValidationRun.objects.create(
            bundle=self.bundle, tier=ValidationRun.TIER_STRUCTURE,
            status=ValidationRun.STATUS_DONE, findings=[],
            bundle_updated_at=self.bundle.updated_at)

    def submit(self):
        return self.client.post(
            reverse('build:submit_bundle_internal', args=[self.bundle.pk]))

    def test_submitting_starts_no_second_check(self):
        """The gate has just accepted a check matching these exact files, and that
        check already reads inside data files, so another would only repeat it."""
        from unittest import mock
        self.passing_check()
        with mock.patch.object(validate_runner.subprocess, 'Popen') as popen:
            with override_settings(VALIDATE_HOME='', VALIDATE_WORK_DIR=self.archive):
                self.submit()
        self.bundle.refresh_from_db()
        self.assertIsNotNone(self.bundle.submitted_at)
        self.assertFalse(ValidationRun.objects.filter(
            bundle=self.bundle, tier=ValidationRun.TIER_FULL).exists())
        popen.assert_not_called()

    def test_a_submission_succeeds_even_if_validation_cannot_start(self):
        """Validation is a service to the submission, never a gate on it."""
        from unittest import mock
        self.passing_check()
        with mock.patch.object(validate_runner, 'start',
                               side_effect=RuntimeError('validate exploded')):
            response = self.submit()
        self.assertIn(response.status_code, (200, 302))
        self.bundle.refresh_from_db()
        self.assertIsNotNone(self.bundle.submitted_at)


class ResourceLimitTests(TestCase):
    """Limits that exist because this runs on a machine shared with other services.

    Per-bundle deduplication stops one bundle starting two JVMs. It says nothing
    about many people opening many bundles, which is the case that would hurt
    everything else on the box rather than just ELSA.
    """

    def setUp(self):
        self.workdir = tempfile.mkdtemp(prefix='elsa-limits-')
        self.addCleanup(shutil.rmtree, self.workdir, True)
        self.user = User.objects.create_user('limits', password='pw')
        self.bundle = Bundle.objects.create(
            name='limit bundle', user=self.user, version='1O00')
        home = os.path.join(self.workdir, 'validate-home')
        os.makedirs(os.path.join(home, 'bin'))
        open(os.path.join(home, 'bin', 'validate'), 'w').close()
        self.home = home

    def busy_with(self, count):
        for index in range(count):
            other = Bundle.objects.create(
                name='busy {}'.format(index), user=self.user, version='1O00')
            ValidationRun.objects.create(
                bundle=other, status=ValidationRun.STATUS_RUNNING)

    def test_the_heap_is_capped(self):
        """Through _JAVA_OPTIONS, because bin/validate ignores JAVA_OPTS and hard-codes
        -Xms2048m -Xmx4096m; _JAVA_OPTIONS is the only thing the JVM applies after it."""
        with override_settings(VALIDATE_JAVA_MAX_HEAP='2g'):
            opts = validate_runner._environment().get('_JAVA_OPTIONS', '')
        self.assertIn('-Xmx2g', opts)
        # Without a lower floor, any ceiling under the launcher's 2GB stops the JVM.
        self.assertIn('-Xms64m', opts)

    def test_an_existing_java_options_is_kept(self):
        os.environ['_JAVA_OPTIONS'] = '-Dfoo=bar'
        self.addCleanup(os.environ.pop, '_JAVA_OPTIONS', None)
        with override_settings(VALIDATE_JAVA_MAX_HEAP='1g'):
            opts = validate_runner._environment().get('_JAVA_OPTIONS', '')
        self.assertIn('-Dfoo=bar', opts)
        self.assertIn('-Xmx1g', opts)

    def test_the_cap_is_not_applied_twice(self):
        """The child inherits the web process's environment and builds its own."""
        with override_settings(VALIDATE_JAVA_MAX_HEAP='2g'):
            os.environ['_JAVA_OPTIONS'] = validate_runner._environment()['_JAVA_OPTIONS']
            self.addCleanup(os.environ.pop, '_JAVA_OPTIONS', None)
            opts = validate_runner._environment()['_JAVA_OPTIONS']
        self.assertEqual(opts.count('-Xmx'), 1)

    def test_capacity_is_reached_at_the_limit(self):
        self.busy_with(2)
        with override_settings(VALIDATE_MAX_CONCURRENT=2):
            self.assertTrue(validate_runner.at_capacity())

    def test_below_the_limit_is_not_capacity(self):
        self.busy_with(1)
        with override_settings(VALIDATE_MAX_CONCURRENT=2):
            self.assertFalse(validate_runner.at_capacity())

    def test_a_request_at_capacity_is_refused_not_queued(self):
        """Queuing would need a worker to drain it, and there is none."""
        from unittest import mock
        self.busy_with(2)
        launched = []
        with override_settings(VALIDATE_MAX_CONCURRENT=2, VALIDATE_HOME=self.home,
                               VALIDATE_WORK_DIR=self.workdir):
            with mock.patch.object(validate_runner.subprocess, 'Popen',
                                   side_effect=lambda *a, **k: launched.append(a)):
                run = validate_runner.start(self.bundle)
        self.assertEqual(run.status, ValidationRun.STATUS_FAILED)
        self.assertIn('as many validations', run.failure_reason)
        self.assertEqual(launched, [], 'a refused request still started a JVM')

    def test_the_cap_can_be_lifted(self):
        self.busy_with(5)
        with override_settings(VALIDATE_MAX_CONCURRENT=0):
            self.assertFalse(validate_runner.at_capacity())

    def test_abandoned_runs_do_not_count_towards_capacity(self):
        """Otherwise a couple of crashes would wedge validation for everyone."""
        self.busy_with(2)
        ValidationRun.objects.all().update(
            requested_at=timezone.now() - timezone.timedelta(hours=6))
        with override_settings(VALIDATE_MAX_CONCURRENT=2, VALIDATE_TIMEOUT_SECONDS=60):
            self.assertFalse(validate_runner.at_capacity())
