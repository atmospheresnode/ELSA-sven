"""The whole cycle through HTTP: start, poll, finish, read the staff report.

Drives the endpoints a browser drives, against a stand-in validate that streams
the same counters the real tool does, so the polling contract is tested rather
than assumed.

The one thing deliberately not exercised here is the detach. start() launches
`manage.py run_validation` as a separate process, which loads its own settings and
connects to its own database - in the suite that is a different database from the
in-memory one holding the test's rows, so a detached child could never complete
the run and the test would poll until it timed out. Popen is therefore replaced
with an in-process call to the same work the child would do.

The detach itself is verified against the real database instead: nine runs across
three bundles, in the session notes rather than here, because it needs a real
install of the tool.
"""
import json
import os
import shutil
import stat
import tempfile
import time

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from build.models import Bundle, ValidationRun
from build import validate_runner


REPORT = {
    "summary": {"totalErrors": 2, "totalWarnings": 1, "totalProducts": 2,
                "messageTypes": [{"messageType": "error.label.schema", "total": 2}]},
    "productLevelValidationResults": [{
        "status": "FAIL", "label": "file:LABEL_PATH",
        "messages": [
            {"severity": "ERROR", "type": "error.label.schema", "line": 4,
             "message": "cvc-minLength-valid: Value '' is not valid."},
            {"severity": "ERROR", "type": "error.label.schematron", "line": 3,
             "message": "Citation_Information and its description are required."},
            {"severity": "WARNING", "type": "warning.label.context_ref_mismatch",
             "line": 3, "message": "Context reference name mismatch."},
        ]}],
}


class ValidationHttpCycleTests(TestCase):

    def setUp(self):
        self.workdir = tempfile.mkdtemp(prefix='elsa-e2e-')
        self.addCleanup(shutil.rmtree, self.workdir, True)
        self.archive = os.path.join(self.workdir, 'archive')
        os.makedirs(self.archive)

        self.owner = User.objects.create_user('e2e', password='pw')
        self.staff = User.objects.create_user('e2estaff', password='pw', is_staff=True)
        self.bundle = Bundle.objects.create(
            name='e2e bundle', user=self.owner, version='1O00', bundle_type='External')
        os.makedirs(self.bundle.directory(), exist_ok=True)

        self.label = os.path.join(self.bundle.directory(), 'bundle_e2e.xml')
        with open(self.label, 'w') as handle:
            handle.write(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<Product_Bundle xmlns="http://pds.nasa.gov/pds4/pds/v1">\n'
                '  <Identification_Area>\n'
                '    <title></title>\n'
                '  </Identification_Area>\n'
                '</Product_Bundle>\n')

        self.home = self.install_fake_validate()
        self.client.login(username='e2e', password='pw')

    def install_fake_validate(self):
        """A stand-in that streams real counters, then writes a real report shape."""
        report = dict(REPORT)
        report['productLevelValidationResults'][0]['label'] = 'file:' + self.label
        payload = json.dumps(report).replace("'", "'\\''")

        home = os.path.join(self.workdir, 'validate-home')
        os.makedirs(os.path.join(home, 'bin'))
        path = os.path.join(home, 'bin', 'validate')
        with open(path, 'w') as handle:
            handle.write(
                '#!/bin/sh\n'
                '# parse -r <report path> the way the real tool is invoked\n'
                'while [ $# -gt 0 ]; do\n'
                '  if [ "$1" = "-r" ]; then REPORT="$2"; fi\n'
                '  shift\n'
                'done\n'
                'echo "[label.validation] 1 products completed."\n'
                'echo "[label.validation] 2 products completed."\n'
                'echo "[reference.integrity] 1 products completed."\n'
                "printf '%s' '" + payload + "' > \"$REPORT\"\n"
                'exit 1\n')
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        return home

    def settings(self):
        return override_settings(
            VALIDATE_HOME=self.home, VALIDATE_JAVA_HOME='',
            VALIDATE_WORK_DIR=self.workdir, ARCHIVE_DIR=self.archive,
            VALIDATE_TIMEOUT_SECONDS=60)

    def run_inline(self):
        """Do the work the detached child would do, in this process.

        Only the `manage.py run_validation` launch is intercepted. run() itself
        starts validate through the same Popen, so that call has to pass through to
        the real one or nothing would actually be validated.
        """
        real_popen = validate_runner.subprocess.Popen

        def fake_popen(command, *args, **kwargs):
            if not (isinstance(command, (list, tuple)) and 'run_validation' in command):
                return real_popen(command, *args, **kwargs)

            validate_runner.run(int(command[-1]))

            class Launched:
                """Enough of a Popen for start(), which never inspects it."""
                pid = -1
                returncode = 0

                def poll(self):
                    return 0

                def wait(self, timeout=None):
                    return 0
            return Launched()
        return fake_popen

    def poll_until_finished(self, limit=30):
        """Poll the status endpoint the way the page will."""
        url = reverse('build:validation_status', args=[self.bundle.pk])
        deadline = time.time() + limit
        seen = []
        while time.time() < deadline:
            payload = json.loads(self.client.get(url).content)
            seen.append(payload)
            if payload.get('finished'):
                return payload, seen
            time.sleep(0.2)
        self.fail('the run never reported finished')

    def start_validation(self):
        from unittest import mock
        with mock.patch.object(validate_runner.subprocess, 'Popen',
                               side_effect=self.run_inline()):
            return self.client.post(
                reverse('build:start_validation', args=[self.bundle.pk]))

    def test_the_whole_cycle_through_http(self):
        with self.settings():
            start = self.start_validation()
            self.assertEqual(start.status_code, 200)
            queued = json.loads(start.content)
            self.assertIn(queued['status'],
                          (ValidationRun.STATUS_QUEUED, ValidationRun.STATUS_RUNNING))

            final, seen = self.poll_until_finished()

        self.assertEqual(final['status'], ValidationRun.STATUS_DONE)
        self.assertEqual(final['errors'], 2)
        self.assertEqual(final['warnings'], 1)
        self.assertEqual(final['percent'], 100)
        self.assertFalse(final['stale'])
        self.assertEqual(final['failure_reason'], '')

        # Every payload the poller saw must be coherent: percent is null or 0-100,
        # and never goes backwards once it is a number.
        numbers = [p['percent'] for p in seen if p.get('percent') is not None]
        self.assertEqual(numbers, sorted(numbers), 'progress went backwards')
        for value in numbers:
            self.assertGreaterEqual(value, 0)
            self.assertLessEqual(value, 100)

    def test_a_non_zero_exit_is_a_completed_run_not_a_failed_one(self):
        """Finding errors is what the tool is for; it exits 1 to say so."""
        with self.settings():
            self.start_validation()
            final, _ = self.poll_until_finished()
        self.assertEqual(final['status'], ValidationRun.STATUS_DONE)

    def test_findings_resolve_to_places_in_the_label(self):
        with self.settings():
            self.start_validation()
            self.poll_until_finished()

        run = ValidationRun.objects.get(bundle=self.bundle)
        paths = {f['element_path'] for f in run.findings}
        self.assertIn('Product_Bundle/Identification_Area/title', paths)

    def test_editing_the_bundle_marks_the_result_stale(self):
        with self.settings():
            self.start_validation()
            self.poll_until_finished()

            self.bundle.save()          # any edit bumps updated_at
            payload = json.loads(self.client.get(
                reverse('build:validation_status', args=[self.bundle.pk])).content)
        self.assertTrue(payload['stale'], 'the panel would show results that no longer apply')

    def test_staff_can_read_the_finished_report(self):
        with self.settings():
            self.start_validation()
            self.poll_until_finished()

        run = ValidationRun.objects.get(bundle=self.bundle)
        self.client.force_login(self.staff)
        response = self.client.get(reverse('build:validation_report', args=[run.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Identification_Area')
        self.assertContains(response, 'error.label.schematron')

    def test_the_owner_cannot_read_the_staff_report(self):
        with self.settings():
            self.start_validation()
            self.poll_until_finished()
        run = ValidationRun.objects.get(bundle=self.bundle)
        self.assertEqual(
            self.client.get(reverse('build:validation_report', args=[run.pk])).status_code,
            302)

    def test_a_second_cycle_gives_the_same_answer(self):
        answers = []
        with self.settings():
            for _ in range(2):
                ValidationRun.objects.filter(bundle=self.bundle).delete()
                self.start_validation()
                final, _ = self.poll_until_finished()
                answers.append((final['status'], final['errors'], final['warnings']))
        self.assertEqual(answers[0], answers[1])
