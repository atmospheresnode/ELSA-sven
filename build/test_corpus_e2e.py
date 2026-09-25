# -*- coding: utf-8 -*-
"""End-to-end: build each corpus shape, validate it for real, judge what a user sees.

Slow and honest. Every bundle here is built by driving the real views, written to a
real directory, and handed to the real NASA validator. Skipped, loudly, when the
validator is not configured; a harness that passes without running it would be
worse than none.

Built once for the whole class rather than once per assertion. Each bundle costs a
JVM start and five to seven seconds, so rebuilding them per test turned eight
shapes into twenty-four validator runs for no added confidence.

    python3 manage.py test build.test_corpus_e2e --settings=test_settings
"""
from __future__ import unicode_literals

import os
import shutil
import tempfile
import unittest

from django.test import TransactionTestCase, override_settings

from build.corpus import runner
from build.corpus.builder import BundleBuilder
from build.corpus.shapes import SHAPES, have_netcdf

VERDICTS = {}

# Defects ELSA is known to produce that are product gaps rather than bugs in label
# generation. Named so the gate stays strict about everything else instead of being
# switched off, and so the list stays short and visible.
#
#   document-file-missing -- ELSA records a document and its file name but offers no
#   way to upload the file itself. Neither document form carries a FileField and no
#   template offers a file input, so every bundle containing a document names a file
#   that is not there. Closing it is a feature, not a fix.
KNOWN_GAPS = {'document-file-missing'}


def build_corpus(archive, media, reports):
    """Build and validate every shape once. Returns {shape name: verdict}."""
    verdicts = {}
    with override_settings(ARCHIVE_DIR=archive, MEDIA_ROOT=media):
        for shape in SHAPES:
            if shape.get('needs_netcdf') and not have_netcdf():
                continue
            builder = BundleBuilder()
            shape['build'](builder)
            directory = builder.directory()
            report = os.path.join(
                reports, shape['name'].replace(' ', '_') + '.json')
            findings = runner.run_validate(directory, report)
            verdicts[shape['name']] = runner.judge(findings)
    return verdicts


def describe(verdicts):
    """A readable report, printed once, so a failure is diagnosable from the log."""
    lines = ['', 'CORPUS', '=' * 70]
    for shape in SHAPES:
        if shape['name'] not in verdicts:
            lines.append('skip {:34} (no NetCDF fixture)'.format(shape['name']))
            continue
        verdict = verdicts[shape['name']]
        unexpected_elsa = set(verdict['elsa_keys']) - KNOWN_GAPS
        ok = (not unexpected_elsa and not verdict['unmapped']
              and set(verdict['user_keys']) == shape['expect_user'])
        lines.append('{} {:34} {:3} raw'.format(
            'pass' if ok else 'FAIL', shape['name'], verdict['raw']))
        lines.append('       user     : {}'.format(verdict['user_keys'] or '-'))
        if set(verdict['user_keys']) != shape['expect_user']:
            lines.append('       expected : {}'.format(
                sorted(shape['expect_user']) or '-'))
        if verdict['elsa_keys']:
            known = sorted(set(verdict['elsa_keys']) & KNOWN_GAPS)
            unexpected = sorted(set(verdict['elsa_keys']) - KNOWN_GAPS)
            if known:
                lines.append('       gap      : {} (known, see KNOWN_GAPS)'.format(known))
            if unexpected:
                lines.append('       ELSA     : {} ({} findings)'.format(
                    unexpected, verdict['elsa_findings']))
                for where in verdict['elsa_where']:
                    lines.append('                  {}'.format(where))
        for message in verdict['unmapped_messages']:
            lines.append('       UNMAPPED : {}'.format(message))
    return '\n'.join(lines)


@unittest.skipUnless(runner.validate_available(),
                     'VALIDATE_HOME is not configured; see docs/pds_validation_setup.md')
class CorpusEndToEndTests(TransactionTestCase):

    @classmethod
    def setUpClass(cls):
        super(CorpusEndToEndTests, cls).setUpClass()
        cls.archive = tempfile.mkdtemp(prefix='elsa-corpus-')
        cls.media = tempfile.mkdtemp(prefix='elsa-corpus-media-')
        cls.reports = tempfile.mkdtemp(prefix='elsa-corpus-reports-')

    @classmethod
    def tearDownClass(cls):
        for directory in (cls.archive, cls.media, cls.reports):
            shutil.rmtree(directory, ignore_errors=True)
        super(CorpusEndToEndTests, cls).tearDownClass()

    def setUp(self):
        # TransactionTestCase truncates tables between tests, so the corpus is built
        # inside the first test that needs it and reused by the rest of the class.
        if not VERDICTS:
            VERDICTS.update(build_corpus(self.archive, self.media, self.reports))
            print(describe(VERDICTS))

    # -- the three gates ---------------------------------------------------------

    KNOWN_GAPS = KNOWN_GAPS

    def test_nothing_elsa_caused_reaches_a_bundle(self):
        """Gate 1. Anything here is our defect, not the user's."""
        offenders = []
        for name, verdict in sorted(VERDICTS.items()):
            unexpected = set(verdict['elsa_keys']) - self.KNOWN_GAPS
            if unexpected:
                offenders.append('{}: {}'.format(name, sorted(unexpected)))
        self.assertEqual(offenders, [],
                         'ELSA is still writing invalid labels:\n  '
                         + '\n  '.join(offenders))

    def test_the_known_gaps_are_still_the_only_ones(self):
        """A gap that has been closed should stop being excused."""
        seen = {key for v in VERDICTS.values() for key in v['elsa_keys']}
        stale = self.KNOWN_GAPS - seen
        self.assertEqual(
            stale, set(),
            'these are listed as known gaps but no longer occur; remove them from '
            'KNOWN_GAPS so the gate goes back to being strict about them: '
            '{}'.format(sorted(stale)))

    def test_no_finding_reaches_a_user_unmapped(self):
        """Gate 2. An unmapped finding is raw PDS text in front of a scientist."""
        offenders = ['{}: {}'.format(name, v['unmapped_messages'])
                     for name, v in sorted(VERDICTS.items()) if v['unmapped']]
        self.assertEqual(offenders, [],
                         'findings with no rule:\n  ' + '\n  '.join(offenders))

    def test_a_user_is_told_exactly_what_they_left_undone(self):
        """Gate 3. Not more, and not less."""
        mismatches = []
        for shape in SHAPES:
            if shape['name'] not in VERDICTS:
                continue
            verdict = VERDICTS[shape['name']]
            actual = set(verdict['user_keys'])
            if actual != shape['expect_user']:
                mismatches.append('{}: expected {} got {}'.format(
                    shape['name'], sorted(shape['expect_user']) or '{}',
                    sorted(actual) or '{}'))
        self.assertEqual(mismatches, [],
                         'what the user is told does not match:\n  '
                         + '\n  '.join(mismatches))
