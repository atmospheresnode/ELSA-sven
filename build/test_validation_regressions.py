"""Regressions found by reviewing the validation foundation against the real tool.

Each of these was a live defect, reproduced before it was fixed:

- the report parser dropped a finding when two collections held labels with the same
  file name, and lost every element path when the bundle path needed percent-encoding;
- a warning could match a user rule and block submission, and the advisory rule for
  warnings matched on a type suffix no validate type has;
- a missing file in any bundle whose path merely contained "document" was hidden as
  ELSA's gap;
- a run refused for capacity opened the gate on a bundle with current findings, and a
  run whose child died on startup held its bundle and a capacity slot for an hour;
- the progress reader stopped reading on a database error or an undecodable byte, and
  validate then blocked on a full pipe until the timeout.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, SimpleTestCase, override_settings
from django.utils import timezone

from build.models import Bundle, ValidationRun
from build import validate_runner, validate_rules, validate_report
from build.requirement_fixture import satisfy_requirements


LABEL = """<?xml version="1.0" encoding="UTF-8"?>
<Product_External xmlns="http://pds.nasa.gov/pds4/pds/v1">
  <Identification_Area>
    <logical_identifier>urn:x</logical_identifier>
  </Identification_Area>
</Product_External>
"""


class ParserProbes(SimpleTestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def report(self, results):
        path = os.path.join(self.tmp, 'r.json')
        with open(path, 'w') as f:
            json.dump({'summary': {}, 'productLevelValidationResults': results}, f)
        return path

    def label(self, *parts):
        path = os.path.join(self.tmp, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.write(LABEL)
        return path

    def test_same_basename_in_two_collections_keeps_both_findings(self):
        a = self.label('alpha', 'x.xml')
        b = self.label('beta', 'x.xml')
        msg = {'severity': 'ERROR', 'type': 'error.label.schema', 'line': 4, 'message': 'm'}
        parsed = validate_report.parse_report(self.report([
            {'label': 'file:' + a, 'messages': [msg]},
            {'label': 'file:' + b, 'messages': [msg]},
        ]))
        self.assertEqual(len(parsed['findings']), 2)

    def test_percent_encoded_path_still_resolves_element(self):
        a = self.label('sp ace', 'x.xml')
        msg = {'severity': 'ERROR', 'type': 'error.label.schema', 'line': 4, 'message': 'm'}
        parsed = validate_report.parse_report(self.report([
            {'label': 'file:' + a.replace(' ', '%20'), 'messages': [msg]}]))
        self.assertEqual(parsed['findings'][0]['element_path'],
                         'Product_External/Identification_Area/logical_identifier')


class RuleProbes(SimpleTestCase):

    def test_a_warning_never_blocks(self):
        finding = {'severity': 'WARNING', 'type': 'warning.label.schematron',
                   'message': "The value '' of element 'given_name' is not valid",
                   'label': 'b.xml', 'label_path': '/a/b.xml', 'line': 3,
                   'element_path': 'Product_Bundle/Identification_Area/Citation_Information/List_Author/Person/given_name'}
        self.assertTrue(validate_rules.summarise([finding])['can_submit'],
                        validate_rules.classify(finding).key)

    def test_advisory_warning_rule_matches_a_real_warning_type(self):
        finding = {'severity': 'WARNING', 'type': 'warning.integrity.unreferenced_member',
                   'message': 'x', 'label': 'c.xml', 'label_path': '/a/c.xml',
                   'line': None, 'element_path': ''}
        self.assertEqual(validate_rules.classify(finding).key, 'advisory-warning')

    def test_missing_netcdf_in_a_bundle_named_documents_is_the_users(self):
        finding = {'severity': 'ERROR', 'type': 'error.label.missing_file',
                   'message': 'URI reference does not exist: file:/ar/u/documents_x_bundle/data/f.nc',
                   'label': 'f.xml', 'label_path': '/ar/u/documents_x_bundle/data/f.xml',
                   'line': None, 'element_path': ''}
        self.assertEqual(validate_rules.classify(finding).key, 'missing-file')


@override_settings(VALIDATE_BLOCKS_SUBMISSION=True, VALIDATE_MAX_CONCURRENT=1)
class GateProbes(TestCase):

    def setUp(self):
        self.archive = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.archive, True)
        p = override_settings(ARCHIVE_DIR=self.archive, VALIDATE_WORK_DIR=self.archive)
        p.enable()
        self.addCleanup(p.disable)
        self.owner = User.objects.create_user('probe', password='pw', email='p@x.org')
        self.bundle = Bundle.objects.create(name='probe', user=self.owner,
                                            version='1O00', bundle_type='External')
        satisfy_requirements(self.bundle)
        self.other = Bundle.objects.create(name='other', user=self.owner,
                                           version='1O00', bundle_type='External')

    def test_capacity_refusal_does_not_open_the_gate_on_known_findings(self):
        blocking = {'severity': 'ERROR', 'type': 'error.label.schematron',
                    'message': 'In Product_Bundle both Citation_Information and its description are required.',
                    'label': 'b.xml', 'label_path': '/b.xml', 'line': 1,
                    'element_path': 'Product_Bundle/Identification_Area'}
        ValidationRun.objects.create(
            bundle=self.bundle, tier=ValidationRun.TIER_STRUCTURE,
            status=ValidationRun.STATUS_DONE, findings=[blocking],
            content_fingerprint=self.bundle.content_fingerprint(),
            finished_at=timezone.now())
        self.assertIsNotNone(validate_runner.submission_block(self.bundle, self.owner))
        # Someone else's run fills the only slot; pressing Check is refused.
        ValidationRun.objects.create(bundle=self.other, tier=ValidationRun.TIER_STRUCTURE,
                                     status=ValidationRun.STATUS_RUNNING)
        refused = validate_runner.start(self.bundle)
        self.assertEqual(refused.status, ValidationRun.STATUS_FAILED)
        self.assertIsNotNone(validate_runner.submission_block(self.bundle, self.owner),
                             'gate opened by a capacity refusal')

    def test_a_queued_run_whose_child_died_does_not_hold_capacity_for_an_hour(self):
        dead = ValidationRun.objects.create(bundle=self.other,
                                            tier=ValidationRun.TIER_STRUCTURE,
                                            status=ValidationRun.STATUS_QUEUED)
        ValidationRun.objects.filter(pk=dead.pk).update(
            requested_at=timezone.now() - timezone.timedelta(minutes=10))
        self.assertFalse(validate_runner.at_capacity())


class ReaderProbes(SimpleTestCase):

    def spawn(self, code):
        return subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)

    def test_reader_keeps_draining_after_a_db_error(self):
        # 400KB of progress lines: far more than a pipe buffer holds.
        proc = self.spawn(
            "import sys\nfor i in range(8000): print('[label.validation] %d products completed.' % i)")
        run = mock.Mock(tier=ValidationRun.TIER_STRUCTURE, phase='loading')
        run.save.side_effect = Exception('MySQL server has gone away')
        t = threading.Thread(target=validate_runner._track_progress, args=(proc, run), daemon=True)
        t.start()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            self.fail('validate blocked on a full pipe after the progress reader stopped')

    def test_reader_survives_non_utf8_output(self):
        proc = subprocess.Popen(
            [sys.executable, '-c',
             "import sys\nsys.stdout.buffer.write(b'bad \\xff\\xfe name\\n')\n"
             "for i in range(8000): print('[label.validation] %d products completed.' % i)"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace', bufsize=1)
        run = mock.Mock(tier=ValidationRun.TIER_STRUCTURE, phase='loading')
        t = threading.Thread(target=validate_runner._track_progress, args=(proc, run), daemon=True)
        t.start()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            self.fail('non-UTF-8 output stopped the reader and wedged validate')
