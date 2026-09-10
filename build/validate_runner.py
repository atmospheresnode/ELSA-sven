"""Run the PDS validate tool against a bundle and record what it said.

Two halves. `run()` executes validate synchronously and updates a ValidationRun row
as it goes; it is what the management command calls. `start()` launches that command
as a detached child and returns immediately; it is what a view calls.

Detached matters. Under mod_wsgi a worker can be recycled mid-request, and a thread
doing the waiting would go with it. A child in its own session is reparented instead
of killed, so a validation started just before a deploy still finishes and still
records its result.

The progress numbers are real rather than decorative. validate streams a running
count as it works:

    [label.validation]   3 products completed.
    [content.validation] 2 products completed.

Paired with ELSA's own count of the labels it wrote, that is a true percentage. The
one honest caveat is the beginning: validate emits nothing at all for roughly five
seconds while it compiles the schematron into XSLT, so that stretch is reported as
an indeterminate loading phase instead of a bar sitting at zero.
"""
import os
import re
import subprocess
import sys

from django.conf import settings
from django.utils import timezone

from build.models import ValidationRun
from build.validate_report import parse_report

# "[label.validation] 3 products completed." - the counters validate streams. Each
# pass reports separately and advances independently.
PROGRESS = re.compile(
    r'\[(label\.validation|content\.validation|reference\.integrity)\]'
    r'\s+(\d+)\s+products completed')

COUNTER_PHASES = {
    'label.validation': ValidationRun.PHASE_LABELS,
    'content.validation': ValidationRun.PHASE_CONTENT,
    'reference.integrity': ValidationRun.PHASE_REFERENCES,
}


class ValidateUnavailable(RuntimeError):
    """validate is not installed or not configured on this host."""


def validate_executable():
    """Path to the validate launcher, or raise saying exactly what is missing.

    Worth being specific: the failure people actually hit is a Java version
    mismatch, and validate's own message for that (UnsupportedClassVersionError,
    class file version 61.0) names neither Java nor a version anybody recognises.
    """
    home = getattr(settings, 'VALIDATE_HOME', '')
    if not home:
        raise ValidateUnavailable(
            'VALIDATE_HOME is not set. Point it at an unpacked PDS validate release '
            '(the directory containing bin/validate) in secrets.py.')

    executable = os.path.join(home, 'bin', 'validate')
    if not os.path.exists(executable):
        raise ValidateUnavailable(
            'No validate launcher at {}. Check VALIDATE_HOME points at the release '
            'directory itself, not its parent.'.format(executable))

    return executable


def _environment():
    """A copy of the environment with JAVA_HOME pointed at the configured runtime."""
    environment = os.environ.copy()
    java_home = getattr(settings, 'VALIDATE_JAVA_HOME', '')
    if java_home:
        environment['JAVA_HOME'] = java_home
        environment['PATH'] = os.path.join(java_home, 'bin') + os.pathsep + environment.get('PATH', '')
    return environment


def label_count(bundle):
    """How many XML labels the bundle holds, which is what progress is measured against."""
    total = 0
    for _dirpath, _dirnames, filenames in os.walk(bundle.directory()):
        total += sum(1 for name in filenames if name.lower().endswith('.xml'))
    return total


def report_path_for(run):
    reports = os.path.join(settings.VALIDATE_WORK_DIR, 'reports')
    os.makedirs(reports, exist_ok=True)
    return os.path.join(reports, 'run-{}.json'.format(run.pk))


def build_command(run, report_path):
    """The validate invocation for this run's tier."""
    command = [
        validate_executable(),
        '-R', 'pds4.bundle',
        '-t', run.bundle.directory(),
        '-r', report_path,
        '-s', 'json',
        # Streams the counters that drive the progress bar. Without it validate
        # runs silently and there is nothing to report but elapsed time.
        '--progressN', '1',
    ]

    catalog = os.path.join(settings.VALIDATE_WORK_DIR, 'catalog.xml')
    if os.path.exists(catalog):
        # Resolves schema URLs against the local cache, so a run does not depend on
        # pds.nasa.gov being reachable. Built by `manage.py build_schema_catalog`.
        command += ['-C', catalog]

    if run.tier == ValidationRun.TIER_STRUCTURE:
        # Skips reading inside data files. This is the difference between a few
        # seconds and, on a large NetCDF bundle, potentially minutes.
        command.append('--skip-content-validation')

    return command


def run(run_id):
    """Execute validate for one ValidationRun, updating the row as it progresses.

    Runs in the foreground of whatever process calls it; `start()` is what makes
    that a detached child. Always leaves the row in a terminal state, because a row
    stuck at RUNNING is indistinguishable to the UI from a job still working.
    """
    validation_run = ValidationRun.objects.get(pk=run_id)

    validation_run.status = ValidationRun.STATUS_RUNNING
    validation_run.started_at = timezone.now()
    validation_run.bundle_updated_at = validation_run.bundle.updated_at
    validation_run.products_total = label_count(validation_run.bundle)
    validation_run.products_done = 0
    validation_run.phase = ValidationRun.PHASE_LOADING
    validation_run.save()

    report_path = report_path_for(validation_run)

    try:
        command = build_command(validation_run, report_path)
    except ValidateUnavailable as error:
        validation_run.status = ValidationRun.STATUS_FAILED
        validation_run.failure_reason = str(error)
        validation_run.finished_at = timezone.now()
        validation_run.save()
        return validation_run

    try:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=_environment(), text=True, bufsize=1)

        expected_phases = ValidationRun.PASSES.get(
            validation_run.tier, [ValidationRun.PHASE_LABELS])

        for line in process.stdout:
            match = PROGRESS.search(line)
            if not match:
                continue

            phase = COUNTER_PHASES.get(match.group(1))
            # validate emits a content counter even when --skip-content-validation
            # was passed, so a structure run would otherwise report a phase it is
            # not performing, and the bar would jump backwards when references
            # started. Trust the tier over the counter.
            if phase not in expected_phases:
                continue

            validation_run.phase = phase
            validation_run.products_done = int(match.group(2))
            validation_run.save(update_fields=['phase', 'products_done'])

        process.wait(timeout=getattr(settings, 'VALIDATE_TIMEOUT_SECONDS', 3600))

    except subprocess.TimeoutExpired:
        process.kill()
        validation_run.status = ValidationRun.STATUS_FAILED
        validation_run.failure_reason = (
            'Validation exceeded VALIDATE_TIMEOUT_SECONDS and was stopped.')
        validation_run.finished_at = timezone.now()
        validation_run.save()
        return validation_run

    except OSError as error:
        validation_run.status = ValidationRun.STATUS_FAILED
        validation_run.failure_reason = 'Could not run validate: {}'.format(error)
        validation_run.finished_at = timezone.now()
        validation_run.save()
        return validation_run

    # A non-zero exit means errors were found, which is a normal outcome and not a
    # failure of the run. Only a missing report means validate could not do its job.
    if not os.path.exists(report_path):
        validation_run.status = ValidationRun.STATUS_FAILED
        validation_run.failure_reason = (
            'validate exited with status {} without writing a report.'.format(
                process.returncode))
        validation_run.finished_at = timezone.now()
        validation_run.save()
        return validation_run

    parsed = parse_report(report_path)
    validation_run.report_path = report_path
    validation_run.findings = parsed['findings']
    validation_run.error_count = parsed['summary']['errors']
    validation_run.warning_count = parsed['summary']['warnings']
    validation_run.phase = ValidationRun.PHASE_DONE
    validation_run.products_done = validation_run.products_total
    validation_run.status = ValidationRun.STATUS_DONE
    validation_run.finished_at = timezone.now()
    validation_run.save()
    return validation_run


def active_run_for(bundle, tier=None):
    """The queued or running validation for this bundle, if there is one."""
    runs = bundle.validation_runs.filter(
        status__in=[ValidationRun.STATUS_QUEUED, ValidationRun.STATUS_RUNNING])
    if tier is not None:
        runs = runs.filter(tier=tier)
    return runs.first()


def latest_run_for(bundle, tier=None):
    runs = bundle.validation_runs.all()
    if tier is not None:
        runs = runs.filter(tier=tier)
    return runs.first()


def start(bundle, tier=ValidationRun.TIER_STRUCTURE):
    """Queue a validation and launch it detached. Returns the ValidationRun.

    A run already in flight for this bundle is returned as-is rather than joined by
    a second one. Two JVMs validating the same directory would compete for the same
    report path and cost twice the memory to produce one answer, and a user clicking
    a button twice is not asking for that.
    """
    existing = active_run_for(bundle, tier)
    if existing is not None:
        return existing

    validation_run = ValidationRun.objects.create(
        bundle=bundle, tier=tier, status=ValidationRun.STATUS_QUEUED,
        bundle_updated_at=bundle.updated_at)

    command = [sys.executable, os.path.join(settings.BASE_DIR, 'manage.py'),
               'run_validation', str(validation_run.pk)]

    try:
        subprocess.Popen(
            command, cwd=settings.BASE_DIR, env=_environment(),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            # Its own session, so a recycled wsgi worker does not take the run with
            # it. The child records its own result, so nothing is waiting on it.
            start_new_session=True)
    except OSError as error:
        validation_run.status = ValidationRun.STATUS_FAILED
        validation_run.failure_reason = 'Could not start validation: {}'.format(error)
        validation_run.finished_at = timezone.now()
        validation_run.save()

    return validation_run
