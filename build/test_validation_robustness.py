"""Failure-mode tests for the validation runner.

Written by going back through the Phase 1 code looking for ways it breaks rather
than ways it works. Each test here corresponds to something that can actually
happen on a server: a hung tool, a crashed child, a truncated report, a user
clicking twice, a process killed by a deploy.

The bar is that a ValidationRun always reaches a terminal state and never wedges a
bundle. A row stuck at RUNNING is worse than a failed one, because the
deduplication that stops two JVMs starting will also refuse every future run for
that bundle.
"""
import json
import os
import shutil
import stat
import tempfile
import time

from django.contrib.auth.models import User
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from build.models import Bundle, ValidationRun
from build import validate_runner
from build.validate_report import parse_report


def fake_validate(workdir, script):
    """Install a stand-in validate executable that behaves however we need."""
    home = os.path.join(workdir, 'validate-home')
    os.makedirs(os.path.join(home, 'bin'), exist_ok=True)
    path = os.path.join(home, 'bin', 'validate')
    with open(path, 'w') as handle:
        handle.write('#!/bin/sh\n' + script)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return home


class RunnerRobustnessTests(TestCase):

    def setUp(self):
        self.workdir = tempfile.mkdtemp(prefix='elsa-robust-')
        self.addCleanup(shutil.rmtree, self.workdir, True)
        self.archive = os.path.join(self.workdir, 'archive')
        os.makedirs(self.archive)

        user = User.objects.create_user('robust', password='pw')
        self.bundle = Bundle.objects.create(
            name='robust bundle', user=user, version='1O00', bundle_type='External')
        os.makedirs(self.bundle.directory(), exist_ok=True)
        with open(os.path.join(self.bundle.directory(), 'bundle_robust.xml'), 'w') as label:
            label.write('<Product_Bundle xmlns="http://pds.nasa.gov/pds4/pds/v1"/>')

    def settings_for(self, home, timeout=3600):
        return override_settings(
            VALIDATE_HOME=home, VALIDATE_JAVA_HOME='',
            VALIDATE_WORK_DIR=self.workdir, ARCHIVE_DIR=self.archive,
            VALIDATE_TIMEOUT_SECONDS=timeout)

    # -- a tool that never returns -----------------------------------------------

    def test_a_hung_tool_is_stopped_by_the_timeout(self):
        """The timeout has to cover the tool going quiet, not just running long.

        validate holds its stdout open for as long as it lives, so reading that pipe
        to EOF is itself the blocking operation. A timeout applied only after the
        read finishes never fires on the case it exists for.
        """
        home = fake_validate(self.workdir, 'sleep 30\n')
        run = ValidationRun.objects.create(bundle=self.bundle)

        started = time.time()
        with self.settings_for(home, timeout=2):
            validate_runner.run(run.pk)
        elapsed = time.time() - started

        run.refresh_from_db()
        self.assertLess(elapsed, 15, 'the run was not stopped by its timeout')
        self.assertEqual(run.status, ValidationRun.STATUS_FAILED)
        self.assertIn('longer than', run.failure_reason.lower())
        self.assertIn('stopped', run.failure_reason.lower())

    # -- a tool that dies badly ---------------------------------------------------

    def test_a_crashing_tool_leaves_a_failed_run_not_a_running_one(self):
        home = fake_validate(self.workdir, 'echo boom >&2\nexit 3\n')
        run = ValidationRun.objects.create(bundle=self.bundle)
        with self.settings_for(home):
            validate_runner.run(run.pk)
        run.refresh_from_db()
        self.assertEqual(run.status, ValidationRun.STATUS_FAILED)
        self.assertIsNotNone(run.finished_at)

    def test_an_unwritable_report_directory_does_not_wedge_the_run(self):
        home = fake_validate(self.workdir, 'exit 0\n')
        run = ValidationRun.objects.create(bundle=self.bundle)
        reports = os.path.join(self.workdir, 'reports')
        os.makedirs(reports, exist_ok=True)
        os.chmod(reports, 0o500)
        self.addCleanup(os.chmod, reports, 0o700)
        with self.settings_for(home):
            validate_runner.run(run.pk)
        run.refresh_from_db()
        self.assertIn(run.status,
                      (ValidationRun.STATUS_FAILED, ValidationRun.STATUS_DONE))
        self.assertNotEqual(run.status, ValidationRun.STATUS_RUNNING)

    # -- a report that cannot be read ---------------------------------------------

    def test_a_truncated_report_is_reported_as_a_failure(self):
        """A half-written report is what a killed tool leaves behind."""
        home = fake_validate(
            self.workdir,
            'mkdir -p "%s/reports"\n'
            'printf \'{"summary": {"totalErr\' > "%s/reports/run-REPLACE.json"\n'
            % (self.workdir, self.workdir))
        run = ValidationRun.objects.create(bundle=self.bundle)
        script = os.path.join(home, 'bin', 'validate')
        with open(script) as handle:
            body = handle.read().replace('REPLACE', str(run.pk))
        with open(script, 'w') as handle:
            handle.write(body)

        with self.settings_for(home):
            validate_runner.run(run.pk)
        run.refresh_from_db()
        self.assertEqual(run.status, ValidationRun.STATUS_FAILED)
        self.assertNotEqual(run.status, ValidationRun.STATUS_RUNNING)

    # -- runs abandoned by a restart ----------------------------------------------

    def test_an_abandoned_run_does_not_wedge_the_bundle_forever(self):
        """The worst failure mode: one crashed run blocking all future validation.

        A child killed by a deploy leaves its row at RUNNING with nothing to finish
        it. Deduplication then hands that same dead row back to every later request,
        so the bundle can never be validated again.
        """
        abandoned = ValidationRun.objects.create(
            bundle=self.bundle, tier=ValidationRun.TIER_STRUCTURE,
            status=ValidationRun.STATUS_RUNNING)
        ValidationRun.objects.filter(pk=abandoned.pk).update(
            started_at=timezone.now() - timezone.timedelta(hours=6),
            requested_at=timezone.now() - timezone.timedelta(hours=6))

        active = validate_runner.active_run_for(
            self.bundle, ValidationRun.TIER_STRUCTURE)
        self.assertIsNone(
            active, 'a six-hour-old run is not still in flight; it was abandoned')

    def test_a_recent_run_is_still_treated_as_in_flight(self):
        """The counterpart: reaping must not cancel work that is genuinely running."""
        fresh = ValidationRun.objects.create(
            bundle=self.bundle, status=ValidationRun.STATUS_RUNNING)
        self.assertIsNotNone(
            validate_runner.active_run_for(self.bundle, fresh.tier))

    def test_an_abandoned_run_is_marked_failed_rather_than_left_running(self):
        """It should say what happened, not just quietly stop counting."""
        abandoned = ValidationRun.objects.create(
            bundle=self.bundle, status=ValidationRun.STATUS_RUNNING)
        ValidationRun.objects.filter(pk=abandoned.pk).update(
            started_at=timezone.now() - timezone.timedelta(hours=6),
            requested_at=timezone.now() - timezone.timedelta(hours=6))

        validate_runner.active_run_for(self.bundle, abandoned.tier)
        abandoned.refresh_from_db()
        self.assertEqual(abandoned.status, ValidationRun.STATUS_FAILED)
        self.assertTrue(abandoned.failure_reason)

    # -- the user clicking twice ---------------------------------------------------

    def test_two_simultaneous_starts_create_one_run(self):
        """Both requests read 'no active run' before either has written one."""
        home = fake_validate(self.workdir, 'exit 0\n')
        with self.settings_for(home):
            first = validate_runner.start(self.bundle, ValidationRun.TIER_STRUCTURE)
            second = validate_runner.start(self.bundle, ValidationRun.TIER_STRUCTURE)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            ValidationRun.objects.filter(
                bundle=self.bundle, tier=ValidationRun.TIER_STRUCTURE).count(), 1)

    # -- progress bookkeeping ------------------------------------------------------

    def test_progress_is_not_written_once_per_line(self):
        """A large bundle streams a counter per product per pass.

        Saving on every one turns a validation into hundreds of writes for numbers a
        poller reads every couple of seconds.
        """
        home = fake_validate(
            self.workdir,
            'i=1\nwhile [ $i -le 60 ]; do '
            'echo "[label.validation] $i products completed."; i=$((i+1)); done\n'
            'exit 0\n')
        run = ValidationRun.objects.create(bundle=self.bundle)

        saves = {'count': 0}
        original = ValidationRun.save

        def counting_save(self, *args, **kwargs):
            saves['count'] += 1
            return original(self, *args, **kwargs)

        ValidationRun.save = counting_save
        self.addCleanup(setattr, ValidationRun, 'save', original)

        with self.settings_for(home):
            validate_runner.run(run.pk)

        self.assertLess(
            saves['count'], 30,
            'wrote {} times for 60 progress lines'.format(saves['count']))

    # -- a bundle with nothing in it -----------------------------------------------

    def test_a_bundle_with_no_labels_still_completes(self):
        empty_user = User.objects.create_user('empty', password='pw')
        empty = Bundle.objects.create(
            name='empty bundle', user=empty_user, version='1O00')
        home = fake_validate(self.workdir, 'exit 0\n')
        run = ValidationRun.objects.create(bundle=empty)
        with self.settings_for(home):
            validate_runner.run(run.pk)
        run.refresh_from_db()
        self.assertNotEqual(run.status, ValidationRun.STATUS_RUNNING)
        self.assertIsNotNone(run.finished_at)


class ReportParsingRobustnessTests(TestCase):
    """parse_report reads a file another program wrote; it has to survive that."""

    def setUp(self):
        self.workdir = tempfile.mkdtemp(prefix='elsa-report-robust-')
        self.addCleanup(shutil.rmtree, self.workdir, True)

    def write(self, content):
        path = os.path.join(self.workdir, 'report.json')
        with open(path, 'w') as handle:
            handle.write(content)
        return path

    def test_malformed_json_raises_something_the_caller_can_catch(self):
        with self.assertRaises(ValueError):
            parse_report(self.write('{"summary": {"totalErr'))

    def test_a_report_with_no_results_is_not_an_error(self):
        parsed = parse_report(self.write(json.dumps({'summary': {'totalErrors': 0}})))
        self.assertEqual(parsed['findings'], [])
        self.assertEqual(parsed['summary']['errors'], 0)

    def test_a_message_with_no_severity_is_kept(self):
        parsed = parse_report(self.write(json.dumps({
            'summary': {},
            'productLevelValidationResults': [
                {'label': 'file:/nowhere.xml', 'messages': [{'message': 'no severity'}]}],
        })))
        self.assertEqual(len(parsed['findings']), 1)


class ViewRobustnessTests(TestCase):
    """The HTTP surface, given input a browser or a crawler can actually send."""

    def setUp(self):
        self.owner = User.objects.create_user('vowner', password='pw')
        self.bundle = Bundle.objects.create(
            name='view robust', user=self.owner, version='1O00', bundle_type='External')
        self.client.login(username='vowner', password='pw')

    def test_a_bundle_that_does_not_exist_is_not_a_server_error(self):
        from django.urls import reverse
        response = self.client.get(
            reverse('build:validation_status', args=[999999]))
        self.assertNotEqual(response.status_code, 500)

    def test_starting_on_a_bundle_that_does_not_exist_is_not_a_server_error(self):
        from django.urls import reverse
        response = self.client.post(
            reverse('build:start_validation', args=[999999]))
        self.assertNotEqual(response.status_code, 500)

    def test_a_report_that_does_not_exist_is_not_a_server_error(self):
        from django.urls import reverse
        staff = User.objects.create_user('vstaff', password='pw', is_staff=True)
        self.client.force_login(staff)
        response = self.client.get(reverse('build:validation_report', args=[999999]))
        self.assertNotEqual(response.status_code, 500)

    def test_an_unknown_tier_falls_back_rather_than_failing(self):
        """A tier arrives from a form field, so it can be anything."""
        from django.urls import reverse
        workdir = tempfile.mkdtemp(prefix='elsa-tier-')
        self.addCleanup(shutil.rmtree, workdir, True)
        home = fake_validate(workdir, 'exit 0\n')
        with override_settings(VALIDATE_HOME=home, VALIDATE_WORK_DIR=workdir,
                               ARCHIVE_DIR=workdir):
            response = self.client.post(
                reverse('build:start_validation', args=[self.bundle.pk]),
                {'tier': 'not-a-tier'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            ValidationRun.objects.get(bundle=self.bundle).tier,
            ValidationRun.TIER_STRUCTURE)

    def test_status_of_a_failed_run_carries_its_reason(self):
        from django.urls import reverse
        ValidationRun.objects.create(
            bundle=self.bundle, status=ValidationRun.STATUS_FAILED,
            failure_reason='validate is not installed')
        payload = json.loads(self.client.get(
            reverse('build:validation_status', args=[self.bundle.pk])).content)
        self.assertTrue(payload['finished'])
        self.assertIn('not installed', payload['failure_reason'])

    def test_percent_is_absent_rather_than_zero_while_loading(self):
        """A bar at zero for five seconds reads as a hang; null means indeterminate."""
        from django.urls import reverse
        ValidationRun.objects.create(
            bundle=self.bundle, status=ValidationRun.STATUS_RUNNING,
            phase=ValidationRun.PHASE_LOADING, products_total=10)
        payload = json.loads(self.client.get(
            reverse('build:validation_status', args=[self.bundle.pk])).content)
        self.assertIsNone(payload['percent'])


class StaleRunVisibilityTests(TestCase):
    """Reaping has to reach the polling endpoint, not just the start path.

    active_run_for reaps, but the status view reads through latest_run_for. If only
    the start path reaps, a page polling a bundle whose run died shows "Running"
    forever and never recovers on its own.
    """

    def setUp(self):
        user = User.objects.create_user('stalevis', password='pw')
        self.bundle = Bundle.objects.create(
            name='stale visible', user=user, version='1O00')
        self.client.login(username='stalevis', password='pw')

    def abandoned(self):
        run = ValidationRun.objects.create(
            bundle=self.bundle, status=ValidationRun.STATUS_RUNNING)
        ValidationRun.objects.filter(pk=run.pk).update(
            requested_at=timezone.now() - timezone.timedelta(hours=6),
            started_at=timezone.now() - timezone.timedelta(hours=6))
        return run

    def test_the_status_endpoint_does_not_report_a_dead_run_as_running(self):
        from django.urls import reverse
        self.abandoned()
        payload = json.loads(self.client.get(
            reverse('build:validation_status', args=[self.bundle.pk])).content)
        self.assertTrue(
            payload['finished'],
            'a page polling this would show Running forever')
        self.assertEqual(payload['status'], ValidationRun.STATUS_FAILED)

    def test_latest_run_for_reaps_too(self):
        run = self.abandoned()
        validate_runner.latest_run_for(self.bundle)
        run.refresh_from_db()
        self.assertEqual(run.status, ValidationRun.STATUS_FAILED)


class ConcurrentStartTests(TransactionTestCase):
    """Two browser tabs, or a double-click, hitting start at the same moment.

    TransactionTestCase rather than TestCase: threads need real committed rows, not
    a transaction the test rolls back.
    """

    def setUp(self):
        self.workdir = tempfile.mkdtemp(prefix='elsa-race-')
        self.addCleanup(shutil.rmtree, self.workdir, True)
        user = User.objects.create_user('racer', password='pw')
        self.bundle = Bundle.objects.create(
            name='race bundle', user=user, version='1O00')

    def test_simultaneous_starts_do_not_launch_two_validations(self):
        import threading
        home = fake_validate(self.workdir, 'exit 0\n')
        barrier = threading.Barrier(4)
        created = []

        def go():
            barrier.wait()
            with override_settings(VALIDATE_HOME=home, VALIDATE_WORK_DIR=self.workdir,
                                   ARCHIVE_DIR=self.workdir):
                created.append(validate_runner.start(
                    self.bundle, ValidationRun.TIER_STRUCTURE).pk)
            from django.db import connection
            connection.close()

        threads = [threading.Thread(target=go) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        total = ValidationRun.objects.filter(bundle=self.bundle).count()
        self.assertEqual(
            total, 1,
            'four simultaneous requests created {} runs; each is a JVM'.format(total))
        self.assertEqual(len(set(created)), 1)
