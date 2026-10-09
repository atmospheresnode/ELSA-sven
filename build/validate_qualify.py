"""Would moving to a new validate release change anything a user sees?

Answered the way it was answered by hand for 4.2.0 -> 5.0.0: run the release in use
and the candidate over the same real bundles and compare, at three levels.

1. Raw findings, keyed on (label, type, severity, line, message). Anything here is a
   real change in what PDS says.
2. The translation: which plain-language items, which ELSA-caused ones, which are
   unmapped. A new message type lands here as "unmapped", the thing the rule table
   exists to prevent.
3. The gate. A bundle that could be submitted and now cannot, or the reverse, is the
   difference that matters most and is reported first.

The exit code is recorded but never counted as a difference, because ELSA does not
read it (5.0.0 changed it for empty runs and nothing else).

Both sides are invoked through validate_runner.build_command and _environment, the
same code a real check uses, so the comparison cannot quietly test something the
application never runs. Bundles are only read; reports go to a scratch directory.
"""
import collections
import concurrent.futures
import glob
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from django.conf import settings

from build import validate_release, validate_rules, validate_runner
from build.validate_report import parse_report

# The suites that exercise validation, run against both releases when the host has
# the test suite (developer checkouts; it is not deployed). Browser tests are left
# out: they are slow and test the page, which a validate release cannot change.
TEST_MODULE_PATTERNS = ('test_valid*.py', 'test_corpus_e2e.py', 'test_document_upload.py',
                        'test_preflight*.py', 'test_review_and_submit.py')


def bundle_dirs(roots=None, limit=None):
    """Every <root>/<user>/<bundle> directory holding at least one XML label."""
    roots = roots or getattr(settings, 'VALIDATE_QUALIFY_ROOTS', None) or [settings.ARCHIVE_DIR]
    found = []
    for root in roots:
        for directory in sorted(glob.glob(os.path.join(root, '*', '*'))):
            if os.path.isdir(directory) and _has_label(directory):
                found.append(directory)
    return found[:limit] if limit else found


def _has_label(directory):
    for _dirpath, _dirnames, filenames in os.walk(directory):
        if any(name.lower().endswith('.xml') for name in filenames):
            return True
    return False


class _Run(object):
    """Enough of a ValidationRun for build_command: a tier and a directory."""
    tier = 'full'

    def __init__(self, directory):
        self.bundle = type('B', (), {'directory': lambda self: directory})()


def run_one(home, java_home, directory, scratch):
    """Validate one bundle with one release. Returns what the comparison needs."""
    os.makedirs(scratch, exist_ok=True)
    report = os.path.join(scratch, 'report.json')
    tmpdir = os.path.join(scratch, 'tmp')
    os.makedirs(tmpdir, exist_ok=True)
    command = validate_runner.build_command(
        _Run(directory), report, executable=os.path.join(home, 'bin', 'validate'))
    environment = validate_runner._environment(tmpdir, java_home=java_home)
    timeout = getattr(settings, 'VALIDATE_TIMEOUT_SECONDS', 3600)

    started = time.monotonic()
    process = subprocess.Popen(command, env=environment, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, errors='replace',
                               start_new_session=True)
    try:
        output, _ = process.communicate(timeout=timeout)
        exit_code = process.returncode
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        output, _ = process.communicate()
        exit_code = 'timeout'
    result = {'exit': exit_code, 'seconds': round(time.monotonic() - started, 1)}

    if not os.path.exists(report):
        result['failed'] = 'no report. It said: ' + validate_runner.last_words(
            (output or '').splitlines())
        return result
    try:
        parsed = parse_report(report)
    except (ValueError, OSError) as error:
        result['failed'] = 'unreadable report: {}'.format(error)
        return result

    findings = parsed['findings']
    translated = validate_rules.translate(findings)
    result.update({
        'version': parsed['summary']['tool_version'],
        'raw': sorted(json.dumps([os.path.relpath(f['label_path'], directory)
                                  if f['label_path'] else '', f['type'], f['severity'],
                                  f['line'], f['message']]) for f in findings),
        'user': sorted('{} {}'.format(i['key'], i['subject']).strip() for i in translated['user']),
        'advisory': sorted('{} {}'.format(i['key'], i['subject']).strip()
                           for i in translated['advisory']),
        'elsa': sorted('{} {}'.format(i['key'], i['subject']).strip() for i in translated['elsa']),
        'unmapped': sorted('{} | {}'.format(f['type'], f['message'][:160])
                           for f in translated['unmapped']),
        'can_submit': validate_rules.summarise(findings)['can_submit'],
    })
    return result


def compare(before, after):
    """What changed for one bundle, as readable lines. Empty when nothing did."""
    if 'failed' in before or 'failed' in after:
        if 'failed' in before and 'failed' in after:
            return []
        return ['the check {} with the new release ({})'.format(
            'fails' if 'failed' in after else 'now succeeds',
            after.get('failed') or before.get('failed'))]

    changes = []
    if before['can_submit'] != after['can_submit']:
        changes.append('GATE: could {}be submitted, now {}'.format(
            '' if before['can_submit'] else 'not ',
            'can' if after['can_submit'] else 'cannot'))
    for field, name in (('user', 'shown to the user'), ('unmapped', 'unmapped'),
                        ('elsa', 'hidden as ELSA-caused'), ('advisory', 'advisory')):
        gone = sorted(set(before[field]) - set(after[field]))
        new = sorted(set(after[field]) - set(before[field]))
        for item in gone:
            changes.append('{}: no longer {}'.format(item, name))
        for item in new:
            changes.append('{}: now {}'.format(item, name))
    gone = collections.Counter(before['raw']) - collections.Counter(after['raw'])
    new = collections.Counter(after['raw']) - collections.Counter(before['raw'])
    if gone or new:
        changes.append('raw findings: {} gone, {} new'.format(
            sum(gone.values()), sum(new.values())))
        for item in list(gone)[:3]:
            changes.append('  - ' + _raw_line(item))
        for item in list(new)[:3]:
            changes.append('  + ' + _raw_line(item))
    return changes


def _raw_line(encoded):
    label, kind, severity, line, message = json.loads(encoded)
    return '{} {} line {} {}: {}'.format(label, severity, line, kind, message[:140])


def qualify(candidate_home, baseline_home=None, roots=None, limit=None, workers=None,
            progress=None):
    """Compare the candidate release with the host's own over every bundle.

    Returns a dict; `render()` turns it into the report people read.
    """
    baseline_home = os.path.realpath(baseline_home or settings.VALIDATE_HOME)
    candidate_home = os.path.realpath(candidate_home)
    sides = {
        'baseline': (baseline_home, validate_release.java_home_for(baseline_home)),
        'candidate': (candidate_home, validate_release.java_home_for(candidate_home)),
    }
    directories = bundle_dirs(roots, limit)
    workers = workers or getattr(settings, 'VALIDATE_QUALIFY_WORKERS', 0) or \
        getattr(settings, 'VALIDATE_MAX_CONCURRENT', 2) or 2

    scratch = tempfile.mkdtemp(prefix='elsa-qualify-')
    results = {}
    started = time.monotonic()
    try:
        jobs = [(n, side, directory) for n, directory in enumerate(directories)
                for side in sides]

        def work(job):
            n, side, directory = job
            home, java_home = sides[side]
            return job, run_one(home, java_home, directory,
                                os.path.join(scratch, '{}-{}'.format(n, side)))

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            for done, (job, result) in enumerate(pool.map(work, jobs), 1):
                results[job] = result
                shutil.rmtree(os.path.join(scratch, '{}-{}'.format(job[0], job[1])),
                              ignore_errors=True)
                if progress:
                    progress(done, len(jobs))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    bundles = []
    for n, directory in enumerate(directories):
        before, after = results[(n, 'baseline', directory)], results[(n, 'candidate', directory)]
        bundles.append({'dir': directory, 'changes': compare(before, after),
                        'exit_changed': before['exit'] != after['exit'],
                        'seconds': (before['seconds'], after['seconds'])})

    changed = [b for b in bundles if b['changes']]
    return {
        'baseline': validate_release.release_version(baseline_home),
        'candidate': validate_release.release_version(candidate_home),
        'baseline_java': sides['baseline'][1],
        'candidate_java': sides['candidate'][1],
        'bundles': len(bundles),
        'identical': len(bundles) - len(changed),
        'changed': changed,
        'gate_flips': sum(1 for b in changed if any(c.startswith('GATE') for c in b['changes'])),
        'exit_code_changes': sum(1 for b in bundles if b['exit_changed']),
        'seconds': {
            'baseline': round(sum(b['seconds'][0] for b in bundles), 1),
            'candidate': round(sum(b['seconds'][1] for b in bundles), 1),
        },
        'wall_seconds': round(time.monotonic() - started),
        'roots': roots or getattr(settings, 'VALIDATE_QUALIFY_ROOTS', None) or [settings.ARCHIVE_DIR],
    }


def test_modules():
    labels = []
    for pattern in TEST_MODULE_PATTERNS:
        for path in sorted(glob.glob(os.path.join(settings.BASE_DIR, 'build', pattern))):
            label = 'build.' + os.path.basename(path)[:-3]
            if label not in labels:
                labels.append(label)
    return labels


def run_tests(home, java_home):
    """Run the validation suites with this release. None when this host has no suite.

    Returns {'ran', 'ok', 'failed': [test ids], 'summary'}.
    """
    if not os.path.exists(os.path.join(settings.BASE_DIR, 'test_settings.py')):
        return None
    modules = test_modules()
    if not modules:
        return None

    scratch = tempfile.mkdtemp(prefix='elsa-qualify-tests-')
    try:
        with open(os.path.join(scratch, 'elsa_qualify_settings.py'), 'w') as handle:
            handle.write('from test_settings import *  # noqa\n'
                         'VALIDATE_HOME = {!r}\nVALIDATE_JAVA_HOME = {!r}\n'.format(home, java_home))
        environment = dict(os.environ)
        environment['PYTHONPATH'] = os.pathsep.join(
            [scratch, str(settings.BASE_DIR), environment.get('PYTHONPATH', '')]).rstrip(os.pathsep)
        environment.pop('DJANGO_SETTINGS_MODULE', None)
        done = subprocess.run(
            [sys.executable, os.path.join(settings.BASE_DIR, 'manage.py'), 'test'] + modules
            + ['--settings=elsa_qualify_settings', '--noinput'],
            cwd=settings.BASE_DIR, env=environment, capture_output=True, text=True,
            errors='replace', timeout=4 * 3600)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    output = done.stdout + done.stderr
    ran = next((line for line in output.splitlines() if line.startswith('Ran ')), '')
    verdict = next((line for line in reversed(output.splitlines())
                    if line.startswith('OK') or line.startswith('FAILED')), '')
    failed = sorted({line.split(' ', 1)[1].strip() for line in output.splitlines()
                     if line.startswith('FAIL: ') or line.startswith('ERROR: ')})
    return {'ran': ran, 'ok': verdict.startswith('OK') and done.returncode == 0,
            'failed': failed, 'summary': verdict or 'no result line (exit {})'.format(done.returncode)}


def render(result, tests=None):
    """The report people read, as plain text. No em dashes, per house style."""
    lines = [
        'validate {} compared with {} (the release in use)'.format(
            result['candidate'], result['baseline']),
        '',
        'Java: {} runs on {}; {} runs on {}.'.format(
            result['candidate'], result['candidate_java'], result['baseline'], result['baseline_java']),
        'Bundles compared: {} (from {}).'.format(result['bundles'], ', '.join(result['roots'])),
        'Identical in every respect: {}.'.format(result['identical']),
        'Changed: {}, of which the submission gate flips on {}.'.format(
            len(result['changed']), result['gate_flips']),
        'Exit code changed (ELSA ignores it): {}.'.format(result['exit_code_changes']),
        'Validation time, all bundles: {}s with {}, {}s with {}.'.format(
            result['seconds']['baseline'], result['baseline'],
            result['seconds']['candidate'], result['candidate']),
    ]
    if tests is not None:
        lines.append('')
        lines.append('Validation test suites:')
        for side in ('baseline', 'candidate'):
            outcome = tests.get(side)
            if outcome is None:
                continue
            lines.append('  {} {}: {} {}'.format(side, result[side], outcome['ran'], outcome['summary']))
        new = tests.get('new_failures') or []
        lines.append('  Failing only with {}: {}'.format(
            result['candidate'], ', '.join(new) if new else 'none'))
    if result['changed']:
        lines += ['', 'What changes, bundle by bundle:']
        ordered = sorted(result['changed'], key=lambda b: not any(
            c.startswith('GATE') for c in b['changes']))
        for bundle in ordered:
            lines.append('')
            lines.append(bundle['dir'])
            lines += ['    ' + change for change in bundle['changes']]
    return '\n'.join(lines) + '\n'


def is_clean(result, tests=None):
    """True when nothing a user sees would change and no test newly fails."""
    return not result['changed'] and not (tests and tests.get('new_failures'))
