"""Tests for the validation runner, report parser and progress arithmetic.

None of these invoke validate itself: it is a 70MB Java program that may not be
installed on the machine running the suite, and shelling out to it would make the
tests slow and environment-dependent. What is tested is everything around it -- how
the command is built, how its streamed output is interpreted, how a report is parsed,
and how a run's state is reported -- which is where the logic actually lives.
"""
import json
import os
import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, SimpleTestCase, override_settings

from build.models import Bundle, ValidationRun
from build import validate_runner
from build.validate_report import (element_path, errors, group_by_location,
                                   parse_report, summarise_types, warnings)


class ProgressPatternTests(SimpleTestCase):
    """The counters validate streams are what make the progress bar honest."""

    def test_reads_each_counter(self):
        cases = [
            ('[label.validation] 3 products completed.', 'label.validation', '3'),
            ('[content.validation] 12 products completed.', 'content.validation', '12'),
            ('[reference.integrity] 1 products completed.', 'reference.integrity', '1'),
        ]
        for line, expected_pass, expected_count in cases:
            with self.subTest(line=line):
                match = validate_runner.PROGRESS.search(line)
                self.assertIsNotNone(match)
                self.assertEqual(match.group(1), expected_pass)
                self.assertEqual(match.group(2), expected_count)

    def test_ignores_ordinary_output(self):
        for line in ['Product Level Validation Results',
                     'PASS: file:/archive/bundle.xml',
                     '  [error.label.schema] something went wrong']:
            self.assertIsNone(validate_runner.PROGRESS.search(line))

    def test_every_counter_maps_to_a_phase(self):
        for counter in ['label.validation', 'content.validation', 'reference.integrity']:
            self.assertIn(counter, validate_runner.COUNTER_PHASES)


class ProgressArithmeticTests(TestCase):
    """percent_complete has to stay indeterminate while validate is silent."""

    def setUp(self):
        user = User.objects.create_user('progress', password='pw')
        self.bundle = Bundle.objects.create(name='progress bundle', user=user, version='1O00')

    def run_at(self, tier, phase, done, total=10, status=ValidationRun.STATUS_RUNNING):
        return ValidationRun(bundle=self.bundle, tier=tier, phase=phase,
                             products_done=done, products_total=total, status=status)

    def test_loading_is_indeterminate_not_zero(self):
        """A bar sitting at zero for five seconds reads as a hang."""
        self.assertIsNone(
            self.run_at(ValidationRun.TIER_STRUCTURE, ValidationRun.PHASE_LOADING, 0)
            .percent_complete())

    def test_unknown_total_is_indeterminate(self):
        self.assertIsNone(
            self.run_at(ValidationRun.TIER_STRUCTURE, ValidationRun.PHASE_LABELS, 0, total=0)
            .percent_complete())

    def test_structure_run_splits_the_bar_across_two_passes(self):
        half_of_labels = self.run_at(
            ValidationRun.TIER_STRUCTURE, ValidationRun.PHASE_LABELS, 5).percent_complete()
        self.assertEqual(half_of_labels, 25)

    def test_full_run_splits_the_bar_across_three_passes(self):
        content_half = self.run_at(
            ValidationRun.TIER_FULL, ValidationRun.PHASE_CONTENT, 5).percent_complete()
        self.assertEqual(content_half, 50)

    def test_a_structure_run_is_never_in_the_content_phase(self):
        """validate emits a content counter even when content is skipped."""
        self.assertNotIn(ValidationRun.PHASE_CONTENT,
                         ValidationRun.PASSES[ValidationRun.TIER_STRUCTURE])
        self.assertIsNone(
            self.run_at(ValidationRun.TIER_STRUCTURE, ValidationRun.PHASE_CONTENT, 5)
            .percent_complete())

    def test_progress_never_exceeds_one_hundred(self):
        over = self.run_at(ValidationRun.TIER_STRUCTURE, ValidationRun.PHASE_REFERENCES, 999)
        self.assertLessEqual(over.percent_complete(), 100)

    def test_complete_is_one_hundred(self):
        done = self.run_at(ValidationRun.TIER_STRUCTURE, ValidationRun.PHASE_DONE, 10,
                           status=ValidationRun.STATUS_DONE)
        self.assertEqual(done.percent_complete(), 100)

    def test_every_phase_has_words_for_it(self):
        for phase in [ValidationRun.PHASE_LOADING, ValidationRun.PHASE_LABELS,
                      ValidationRun.PHASE_CONTENT, ValidationRun.PHASE_REFERENCES,
                      ValidationRun.PHASE_DONE]:
            label = self.run_at(ValidationRun.TIER_FULL, phase, 0).phase_label()
            self.assertTrue(label and label != phase, 'no wording for {}'.format(phase))


class CommandBuildingTests(TestCase):
    def setUp(self):
        user = User.objects.create_user('cmd', password='pw')
        self.bundle = Bundle.objects.create(name='cmd bundle', user=user, version='1O00')
        self.workdir = tempfile.mkdtemp(prefix='elsa-validate-cmd-')
        self.addCleanup(shutil.rmtree, self.workdir, True)
        self.home = os.path.join(self.workdir, 'validate-home')
        os.makedirs(os.path.join(self.home, 'bin'))
        open(os.path.join(self.home, 'bin', 'validate'), 'w').close()

    def build(self, tier):
        run = ValidationRun.objects.create(bundle=self.bundle, tier=tier)
        with override_settings(VALIDATE_HOME=self.home, VALIDATE_WORK_DIR=self.workdir):
            return validate_runner.build_command(run, os.path.join(self.workdir, 'r.json'))

    def test_content_validation_is_not_skipped_by_default(self):
        """It is free on every bundle shape ELSA produces, and catches missing files."""
        self.assertNotIn('--skip-content-validation',
                         self.build(ValidationRun.TIER_STRUCTURE))

    def test_a_host_can_turn_content_validation_off(self):
        from django.test import override_settings
        with override_settings(VALIDATE_SKIP_CONTENT=True):
            self.assertIn('--skip-content-validation',
                          self.build(ValidationRun.TIER_STRUCTURE))

    def test_full_run_does_not_skip_content_validation(self):
        self.assertNotIn('--skip-content-validation', self.build(ValidationRun.TIER_FULL))

    def test_progress_is_always_requested(self):
        """Without it validate runs silently and there is nothing to show."""
        self.assertIn('--progressN', self.build(ValidationRun.TIER_STRUCTURE))

    def test_bundle_rule_and_json_output(self):
        command = self.build(ValidationRun.TIER_STRUCTURE)
        self.assertIn('pds4.bundle', command)
        self.assertIn('json', command)

    def test_catalog_is_used_when_one_has_been_built(self):
        with open(os.path.join(self.workdir, 'catalog.xml'), 'w') as catalog:
            catalog.write('<catalog/>')
        self.assertIn('-C', self.build(ValidationRun.TIER_STRUCTURE))

    def test_missing_validate_home_says_what_to_set(self):
        run = ValidationRun.objects.create(bundle=self.bundle)
        with override_settings(VALIDATE_HOME=''):
            with self.assertRaises(validate_runner.ValidateUnavailable) as caught:
                validate_runner.build_command(run, '/tmp/r.json')
        self.assertIn('VALIDATE_HOME', str(caught.exception))


class DeduplicationTests(TestCase):
    """A second request must join the run in flight rather than start another JVM."""

    def setUp(self):
        user = User.objects.create_user('dedupe', password='pw')
        self.bundle = Bundle.objects.create(name='dedupe bundle', user=user, version='1O00')

    def test_active_run_is_found(self):
        running = ValidationRun.objects.create(
            bundle=self.bundle, tier=ValidationRun.TIER_STRUCTURE,
            status=ValidationRun.STATUS_RUNNING)
        self.assertEqual(
            validate_runner.active_run_for(self.bundle, ValidationRun.TIER_STRUCTURE).pk,
            running.pk)

    def test_finished_runs_are_not_active(self):
        ValidationRun.objects.create(
            bundle=self.bundle, tier=ValidationRun.TIER_STRUCTURE,
            status=ValidationRun.STATUS_DONE)
        self.assertIsNone(
            validate_runner.active_run_for(self.bundle, ValidationRun.TIER_STRUCTURE))

    def test_start_returns_the_run_already_in_flight(self):
        existing = ValidationRun.objects.create(
            bundle=self.bundle, tier=ValidationRun.TIER_STRUCTURE,
            status=ValidationRun.STATUS_RUNNING)
        self.assertEqual(
            validate_runner.start(self.bundle, ValidationRun.TIER_STRUCTURE).pk,
            existing.pk)
        self.assertEqual(ValidationRun.objects.count(), 1)

    def test_tiers_do_not_block_each_other(self):
        ValidationRun.objects.create(
            bundle=self.bundle, tier=ValidationRun.TIER_STRUCTURE,
            status=ValidationRun.STATUS_RUNNING)
        self.assertIsNone(validate_runner.active_run_for(self.bundle, ValidationRun.TIER_FULL))


class StalenessTests(TestCase):
    def setUp(self):
        user = User.objects.create_user('stale', password='pw')
        self.bundle = Bundle.objects.create(name='stale bundle', user=user, version='1O00')

    def test_a_run_taken_after_the_last_edit_is_current(self):
        run = ValidationRun.objects.create(
            bundle=self.bundle, bundle_updated_at=self.bundle.updated_at)
        self.assertFalse(run.is_stale())

    def test_editing_the_bundle_makes_the_run_stale(self):
        run = ValidationRun.objects.create(
            bundle=self.bundle, bundle_updated_at=self.bundle.updated_at)
        self.bundle.save()          # auto_now bumps updated_at
        run.refresh_from_db()
        run.bundle.refresh_from_db()
        self.assertTrue(run.is_stale())

    def test_a_run_with_no_snapshot_is_not_called_stale(self):
        """Better to say nothing than to cry stale on every legacy row."""
        self.assertFalse(
            ValidationRun.objects.create(bundle=self.bundle, bundle_updated_at=None).is_stale())


class ReportParsingTests(SimpleTestCase):
    """Parsing is a pure function over a report file, so it needs no database."""

    def setUp(self):
        self.workdir = tempfile.mkdtemp(prefix='elsa-report-')
        self.addCleanup(shutil.rmtree, self.workdir, True)

    def write_label(self):
        path = os.path.join(self.workdir, 'bundle_demo.xml')
        with open(path, 'w', encoding='utf-8') as label:
            label.write(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<Product_Bundle xmlns="http://pds.nasa.gov/pds4/pds/v1">\n'
                '  <Identification_Area>\n'
                '    <title></title>\n'
                '  </Identification_Area>\n'
                '</Product_Bundle>\n')
        return path

    def write_report(self, messages, label_path):
        path = os.path.join(self.workdir, 'report.json')
        with open(path, 'w', encoding='utf-8') as report:
            json.dump({
                'summary': {'totalErrors': len(messages), 'totalWarnings': 0,
                            'totalProducts': 1, 'messageTypes': []},
                'productLevelValidationResults': [
                    {'status': 'FAIL', 'label': 'file:' + label_path, 'messages': messages}],
            }, report)
        return path

    def test_line_numbers_resolve_to_element_paths(self):
        """The hinge of the whole design: a path names something ELSA has a form for."""
        label = self.write_label()
        report = self.write_report(
            [{'severity': 'ERROR', 'type': 'error.label.schema', 'line': 4,
              'message': "Value '' is not valid."}], label)
        finding = parse_report(report)['findings'][0]
        self.assertEqual(finding['element_path'],
                         'Product_Bundle/Identification_Area/title')

    def test_a_finding_with_no_line_is_kept_without_a_path(self):
        label = self.write_label()
        report = self.write_report(
            [{'severity': 'ERROR', 'type': 'error.validation.internal_error',
              'message': 'no line here'}], label)
        findings = parse_report(report)['findings']
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['element_path'], '')

    def test_an_unreadable_label_still_yields_findings(self):
        """A label can be unparseable precisely because it is what is broken."""
        missing = os.path.join(self.workdir, 'not_here.xml')
        report = self.write_report(
            [{'severity': 'ERROR', 'type': 'error.label.schema', 'line': 2,
              'message': 'broken'}], missing)
        self.assertEqual(len(parse_report(report)['findings']), 1)

    def test_identical_findings_are_deduplicated(self):
        label = self.write_label()
        duplicate = {'severity': 'ERROR', 'type': 'error.label.schema', 'line': 4,
                     'message': 'same thing twice'}
        report = self.write_report([duplicate, dict(duplicate)], label)
        self.assertEqual(len(parse_report(report)['findings']), 1)

    def test_summary_is_carried_through(self):
        label = self.write_label()
        report = self.write_report(
            [{'severity': 'ERROR', 'type': 'error.label.schema', 'line': 4, 'message': 'x'}],
            label)
        summary = parse_report(report)['summary']
        self.assertEqual(summary['errors'], 1)
        self.assertEqual(summary['products'], 1)

    def test_errors_and_warnings_are_separable(self):
        label = self.write_label()
        report = self.write_report(
            [{'severity': 'ERROR', 'type': 'error.label.schema', 'line': 4, 'message': 'e'},
             {'severity': 'WARNING', 'type': 'warning.label.schematron', 'line': 3, 'message': 'w'}],
            label)
        findings = parse_report(report)['findings']
        self.assertEqual(len(errors(findings)), 1)
        self.assertEqual(len(warnings(findings)), 1)

    def test_grouping_keeps_unplaced_findings(self):
        grouped = group_by_location([
            {'element_path': 'A/B', 'type': 't'},
            {'element_path': '', 'type': 't'},
        ])
        self.assertIn('', grouped)
        self.assertIn('A/B', grouped)

    def test_type_counts(self):
        counts = summarise_types([
            {'type': 'error.label.schema'}, {'type': 'error.label.schema'},
            {'type': 'error.label.schematron'}])
        self.assertEqual(counts['error.label.schema'], 2)
        self.assertEqual(counts['error.label.schematron'], 1)


class ElementPathTests(SimpleTestCase):
    def test_namespace_is_stripped(self):
        from lxml import etree
        root = etree.fromstring(
            b'<Product_Bundle xmlns="http://pds.nasa.gov/pds4/pds/v1">'
            b'<Identification_Area><title/></Identification_Area></Product_Bundle>')
        title = root.find(
            '{http://pds.nasa.gov/pds4/pds/v1}Identification_Area/'
            '{http://pds.nasa.gov/pds4/pds/v1}title')
        self.assertEqual(element_path(title),
                         'Product_Bundle/Identification_Area/title')
