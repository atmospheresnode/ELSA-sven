# -*- coding: utf-8 -*-
"""Run the corpus: build each shape, validate it for real, judge what a user sees.

Three gates, in the order they matter:

1. Nothing ELSA caused reaches a user. Anything in the ELSA bucket is our defect,
   not theirs, and the count belongs at zero rather than merely hidden.
2. Nothing is unmapped. A finding with no rule reaches the user as raw PDS text,
   which is the thing this feature exists to avoid.
3. What the user is told to do matches what they have actually left undone.

Requires the real validate tool; skipped when VALIDATE_HOME is not configured,
because a harness that silently passes without running the validator is worse than
no harness.
"""
from __future__ import unicode_literals

import json
import os
import subprocess

from django.conf import settings

from build import validate_rules
from build.validate_report import parse_report


def validate_available():
    home = getattr(settings, 'VALIDATE_HOME', '')
    return bool(home) and os.path.exists(os.path.join(home, 'bin', 'validate'))


def run_validate(directory, report_path, catalog=None):
    """Run the real validator over a bundle directory. Returns the findings."""
    # Built by the same code the application uses, so the corpus cannot quietly
    # validate differently from the product. It did once: the runner hardcoded
    # --skip-content-validation while the application had stopped skipping, so a
    # whole class of finding was invisible to the harness meant to catch it.
    from build.validate_runner import build_command

    class _Run(object):
        tier = 'full'

        def __init__(self, directory):
            self.bundle = type('B', (), {'directory': lambda self: directory})()

    command = build_command(_Run(directory), report_path)

    if catalog and os.path.exists(catalog) and '-C' not in command:
        command += ['-C', catalog]

    environment = dict(os.environ)
    java_home = getattr(settings, 'VALIDATE_JAVA_HOME', '')
    if java_home:
        environment['JAVA_HOME'] = java_home
        environment['PATH'] = os.path.join(java_home, 'bin') + os.pathsep + \
            environment.get('PATH', '')

    subprocess.run(command, env=environment, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=600)

    if not os.path.exists(report_path):
        raise RuntimeError('validate produced no report for ' + directory)
    return parse_report(report_path)['findings']


def judge(findings):
    """What the corpus asserts on, for one bundle."""
    translated = validate_rules.translate(findings)
    return {
        'raw': len(findings),
        'user_keys': sorted({item['key'] for item in translated['user']}),
        'advisory_keys': sorted({item['key'] for item in translated['advisory']}),
        'elsa_keys': sorted({item['key'] for item in translated['elsa']}),
        'elsa_findings': sum(len(i['findings']) for i in translated['elsa']),
        # Where each ELSA-caused finding actually is, so a failure can be diagnosed
        # from the log rather than by building the bundle again to look at it.
        'elsa_where': sorted({
            '{} {}'.format(f['label'] or '(bundle)', f['element_path'] or '-')
            for item in translated['elsa'] for f in item['findings']}),
        'unmapped': len(translated['unmapped']),
        'unmapped_messages': [f['message'][:120] for f in translated['unmapped']],
    }
