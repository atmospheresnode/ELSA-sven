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
import threading
import time

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from build.models import Bundle, ValidationRun
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


# How often progress may be written back while a run is in flight. validate streams
# one line per product per pass, so a bundle with a few hundred labels would
# otherwise mean a database write per line for a number nobody reads more than once
# every couple of seconds.
PROGRESS_WRITE_INTERVAL_SECONDS = 1.0


def _track_progress(process, validation_run):
    """Consume validate's output, recording how far it has got.

    Runs on its own thread. Every exit path from here is swallowed deliberately:
    this is bookkeeping for a progress bar, and a failure to update it must never
    be the reason a validation does not finish.
    """
    expected_phases = ValidationRun.PASSES.get(
        validation_run.tier, [ValidationRun.PHASE_LABELS])
    last_write = 0.0
    pending = False

    try:
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

            changed_phase = phase != validation_run.phase
            validation_run.phase = phase
            validation_run.products_done = int(match.group(2))
            pending = True

            # A phase change is always worth recording immediately; it is what the
            # indicator shows in words, and there are only ever a few of them.
            now = time.monotonic()
            if changed_phase or now - last_write >= PROGRESS_WRITE_INTERVAL_SECONDS:
                validation_run.save(update_fields=['phase', 'products_done'])
                last_write = now
                pending = False

        if pending:
            validation_run.save(update_fields=['phase', 'products_done'])
    except Exception as error:                       # noqa: BLE001 - see docstring
        print('validate progress tracking stopped: {}'.format(error))


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
    """A copy of the environment with JAVA_HOME pointed at the configured runtime.

    Also carries the settings module across. The child is a fresh `manage.py`
    invocation, so without this it loads whatever manage.py defaults to rather than
    the settings the parent is running under, and would look for its ValidationRun
    row in a different database than the one holding it.
    """
    environment = os.environ.copy()

    settings_module = os.environ.get('DJANGO_SETTINGS_MODULE') or settings.SETTINGS_MODULE
    if settings_module:
        environment['DJANGO_SETTINGS_MODULE'] = settings_module

    java_home = getattr(settings, 'VALIDATE_JAVA_HOME', '')
    if java_home:
        environment['JAVA_HOME'] = java_home
        environment['PATH'] = os.path.join(java_home, 'bin') + os.pathsep + environment.get('PATH', '')
    return environment


def work_dir():
    """Where the schema cache and reports live.

    Read through getattr with a default because elsa/settings.py is gitignored: the
    settings block for validation does not travel with the code, so a host that has
    not had it applied yet must still import and run rather than raising
    AttributeError from an import-time path join. See docs/pds_validation_setup.md.
    """
    return getattr(settings, 'VALIDATE_WORK_DIR',
                   os.path.join(settings.BASE_DIR, 'validation'))


def label_count(bundle):
    """How many XML labels the bundle holds, which is what progress is measured against."""
    total = 0
    for _dirpath, _dirnames, filenames in os.walk(bundle.directory()):
        total += sum(1 for name in filenames if name.lower().endswith('.xml'))
    return total


# Raw reports kept per bundle. They exist so staff can see exactly what the tool
# said, which matters for the run someone is asking about and the few before it.
# Keeping every report of every run forever is how a disk fills up quietly,
# especially once validation runs automatically on change.
REPORTS_KEPT_PER_BUNDLE = 10


def reports_dir():
    reports = os.path.join(work_dir(), 'reports')
    os.makedirs(reports, exist_ok=True)
    return reports


def report_path_for(run):
    return os.path.join(reports_dir(), 'run-{}.json'.format(run.pk))


def prune_reports(bundle):
    """Delete raw reports for all but this bundle's most recent runs.

    The ValidationRun rows are left alone: they are small, they carry the parsed
    findings, and the history of what a bundle scored is worth keeping. It is only
    the raw JSON on disk that is pruned, so report_path is cleared on the rows whose
    file has gone rather than left pointing at nothing.
    """
    runs = list(bundle.validation_runs.order_by('-requested_at'))
    stale = runs[REPORTS_KEPT_PER_BUNDLE:]

    removed = 0
    for validation_run in stale:
        if not validation_run.report_path:
            continue
        try:
            if os.path.exists(validation_run.report_path):
                os.remove(validation_run.report_path)
                removed += 1
        except OSError as error:
            # Housekeeping must never be the reason a validation fails.
            print('could not remove {}: {}'.format(validation_run.report_path, error))
            continue
        validation_run.report_path = ''
        validation_run.save(update_fields=['report_path'])

    return removed


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

    catalog = os.path.join(work_dir(), 'catalog.xml')
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

    timeout = getattr(settings, 'VALIDATE_TIMEOUT_SECONDS', 3600)

    try:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=_environment(), text=True, bufsize=1)
    except OSError as error:
        validation_run.status = ValidationRun.STATUS_FAILED
        validation_run.failure_reason = 'Could not run validate: {}'.format(error)
        validation_run.finished_at = timezone.now()
        validation_run.save()
        return validation_run

    # Progress is read on its own thread so the timeout below actually covers a hang.
    # validate holds stdout open for as long as it lives, which makes reading that
    # pipe to EOF the blocking operation: a timeout applied after the read finishes
    # can only fire once the tool has already stopped, which is never the case that
    # matters. Waiting on the process instead means a tool that goes silent is still
    # stopped on schedule.
    reader = threading.Thread(
        target=_track_progress, args=(process, validation_run), daemon=True)
    reader.start()

    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        validation_run.status = ValidationRun.STATUS_FAILED
        validation_run.failure_reason = (
            'Validation ran longer than VALIDATE_TIMEOUT_SECONDS ({}s) and was '
            'stopped.'.format(timeout))
        validation_run.finished_at = timezone.now()
        validation_run.save()
        return validation_run

    # Let the reader drain whatever is left in the pipe, but never wait on it
    # indefinitely: the process has already exited, so this is bounded work.
    reader.join(timeout=10)

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

    # The report is written by another program that may have been killed partway
    # through. A half-written file raises here, and letting that escape would leave
    # the row at RUNNING, which wedges the bundle for good.
    try:
        parsed = parse_report(report_path)
    except (ValueError, OSError) as error:
        validation_run.status = ValidationRun.STATUS_FAILED
        validation_run.failure_reason = (
            'validate wrote a report that could not be read: {}'.format(error))
        validation_run.finished_at = timezone.now()
        validation_run.save()
        return validation_run

    validation_run.report_path = report_path
    validation_run.findings = parsed['findings']
    validation_run.error_count = parsed['summary']['errors']
    validation_run.warning_count = parsed['summary']['warnings']
    validation_run.phase = ValidationRun.PHASE_DONE
    validation_run.products_done = validation_run.products_total
    validation_run.status = ValidationRun.STATUS_DONE
    validation_run.finished_at = timezone.now()
    validation_run.save()

    prune_reports(validation_run.bundle)
    return validation_run


def reap_abandoned_runs(bundle=None):
    """Fail runs that cannot still be in flight, and say why.

    A child can disappear without recording anything: killed by a deploy, an OOM, a
    machine restart. Its row stays at QUEUED or RUNNING with nothing left to finish
    it, and because deduplication treats any such row as work in progress, that one
    dead row would refuse every future validation of the bundle. Permanently.

    A run is considered abandoned once it is older than its own timeout plus a
    margin, at which point a live run would have been stopped by the timeout anyway.
    Returns how many were reaped.
    """
    timeout = getattr(settings, 'VALIDATE_TIMEOUT_SECONDS', 3600)
    # The margin covers the gap between a row being created and its child actually
    # starting, so a queued run on a busy machine is not reaped out from under itself.
    cutoff = timezone.now() - timezone.timedelta(seconds=timeout + 300)

    runs = ValidationRun.objects.filter(
        status__in=[ValidationRun.STATUS_QUEUED, ValidationRun.STATUS_RUNNING],
        requested_at__lt=cutoff)
    if bundle is not None:
        runs = runs.filter(bundle=bundle)

    reaped = 0
    for validation_run in runs:
        validation_run.status = ValidationRun.STATUS_FAILED
        validation_run.failure_reason = (
            'This run stopped without finishing and was not recorded. The server may '
            'have been restarted while it was working. Running the check again is '
            'safe.')
        validation_run.finished_at = timezone.now()
        validation_run.save(
            update_fields=['status', 'failure_reason', 'finished_at'])
        reaped += 1

    return reaped


def active_run_for(bundle, tier=None):
    """The queued or running validation for this bundle, if there is one.

    Reaps abandoned runs first, so a row left behind by a crash cannot be mistaken
    for work in progress and block the bundle.
    """
    reap_abandoned_runs(bundle)

    runs = bundle.validation_runs.filter(
        status__in=[ValidationRun.STATUS_QUEUED, ValidationRun.STATUS_RUNNING])
    if tier is not None:
        runs = runs.filter(tier=tier)
    return runs.first()


def latest_run_for(bundle, tier=None):
    """This bundle's most recent validation, whatever state it is in.

    Reaps first, for the same reason active_run_for does but from the other side:
    this is what the polling endpoint reads, and a row left behind by a crash would
    otherwise be reported as still running to a page that refreshes every couple of
    seconds, forever. Reaping only on the start path would mean the panel never
    recovers on its own.
    """
    reap_abandoned_runs(bundle)

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
    # Checking for an existing run and creating one have to be one step. Two requests
    # arriving together - a double-click, or two open tabs - would otherwise both
    # look, both see nothing in flight, and both start a JVM against the same
    # directory.
    #
    # Two guards, because neither covers everything. The row lock serialises the
    # check on MariaDB, which is what production runs. SQLite does not implement
    # select_for_update at all, so the claim below is what actually holds there, and
    # it is also the backstop if two processes slip past the lock: whoever holds the
    # lowest id wins, and everyone else deletes their row and returns the winner's.
    # The important part is that only the winner launches a subprocess.
    with transaction.atomic():
        Bundle.objects.select_for_update().filter(pk=bundle.pk).first()

        existing = active_run_for(bundle, tier)
        if existing is not None:
            return existing

        validation_run = ValidationRun.objects.create(
            bundle=bundle, tier=tier, status=ValidationRun.STATUS_QUEUED,
            bundle_updated_at=bundle.updated_at)

    winner = ValidationRun.objects.filter(
        bundle=bundle, tier=tier,
        status__in=[ValidationRun.STATUS_QUEUED, ValidationRun.STATUS_RUNNING]
    ).order_by('pk').first()

    if winner is not None and winner.pk != validation_run.pk:
        validation_run.delete()
        return winner

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
