"""Runs PDS validate on a bundle and records the result.

`run()` runs validate and updates the ValidationRun row. `start()` launches that as a
detached child for views to call. It's in its own session so a recycled wsgi worker
doesn't kill it.

Progress comes from validate's output:

    [label.validation]   3 products completed.
    [content.validation] 2 products completed.

It prints nothing for about five seconds while compiling, so that shows as a loading phase.
"""
import collections
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import models, transaction
from django.utils import timezone

from build.models import Bundle, ValidationRun
from build.validate_report import parse_report

# The counters validate prints, e.g. "[label.validation] 3 products completed."
PROGRESS = re.compile(
    r'\[(label\.validation|content\.validation|reference\.integrity)\]'
    r'\s+(\d+)\s+products completed')

COUNTER_PHASES = {
    'label.validation': ValidationRun.PHASE_LABELS,
    'content.validation': ValidationRun.PHASE_CONTENT,
    'reference.integrity': ValidationRun.PHASE_REFERENCES,
}


# How often to save progress, so a big bundle doesn't write to the database for every line.
PROGRESS_WRITE_INTERVAL_SECONDS = 1.0


def _worth_quoting(line):
    """A line of validate's output that says something about why it stopped."""
    text = line.strip()
    return bool(text) and not PROGRESS.search(text) and not text.startswith('Picked up _JAVA_OPTIONS')


def last_words(tail, count=3):
    """validate's last few meaningful lines, for a failure that left no report."""
    lines = [line.strip() for line in tail if _worth_quoting(line)]
    return ' | '.join(lines[-count:])


def _track_progress(process, validation_run, tail=None):
    """Read validate's output and record progress.

    Runs on its own thread. All errors are swallowed, a progress bar problem shouldn't stop validate.
    """
    expected_phases = ValidationRun.PASSES.get(
        validation_run.tier, [ValidationRun.PHASE_LABELS])
    last_write = 0.0
    pending = False

    try:
        for line in process.stdout:
            # Keep the last lines, since they're the only record of why validate stopped with no report.
            if tail is not None:
                tail.append(line)

            if validation_run is None:
                # Keep reading even if recording failed, or validate blocks when the pipe fills up.
                continue

            match = PROGRESS.search(line)
            if not match:
                continue

            phase = COUNTER_PHASES.get(match.group(1))
            # validate prints a content counter even with --skip-content-validation, so go by the tier.
            if phase not in expected_phases:
                continue

            changed_phase = phase != validation_run.phase
            validation_run.phase = phase
            validation_run.products_done = int(match.group(2))
            pending = True

            # Always save a phase change right away.
            now = time.monotonic()
            if changed_phase or now - last_write >= PROGRESS_WRITE_INTERVAL_SECONDS:
                try:
                    validation_run.save(update_fields=['phase', 'products_done'])
                except Exception as error:           # noqa: BLE001 - see docstring
                    print('validate progress tracking stopped: {}'.format(error))
                    validation_run = None
                    continue
                last_write = now
                pending = False

        if pending and validation_run is not None:
            validation_run.save(update_fields=['phase', 'products_done'])
    except Exception as error:                       # noqa: BLE001 - see docstring
        print('validate progress tracking stopped: {}'.format(error))


class ValidateUnavailable(RuntimeError):
    """validate is not installed or not configured on this host."""


def validate_executable():
    """Path to the validate launcher, or raise saying what's missing.

    Usually it's the Java version, and validate's own error doesn't make that clear.
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


def configured_java_home(home=None):
    """The Java to run validate with.

    VALIDATE_JAVA_HOME is a path, or 'auto' to pick what the release needs (validate_release.java_home_for).
    Raises validate_release.ReleaseError if 'auto' finds nothing new enough.
    """
    java_home = getattr(settings, 'VALIDATE_JAVA_HOME', '')
    if java_home != 'auto':
        return java_home
    from build import validate_release
    return validate_release.java_home_for(home or getattr(settings, 'VALIDATE_HOME', ''))


def _environment(java_tmpdir=None, java_home=None):
    """Copy of the environment with JAVA_HOME set.

    Also passes the settings module so the child uses the same database. `java_home` overrides
    the configured one, for testing another release.
    """
    environment = os.environ.copy()

    settings_module = os.environ.get('DJANGO_SETTINGS_MODULE') or settings.SETTINGS_MODULE
    if settings_module:
        environment['DJANGO_SETTINGS_MODULE'] = settings_module

    if java_home is None:
        # Don't raise here. A Java problem should be that run's failure reason (see build_command).
        from build.validate_release import ReleaseError
        try:
            java_home = configured_java_home()
        except ReleaseError:
            java_home = ''
    if java_home:
        environment['JAVA_HOME'] = java_home
        environment['PATH'] = os.path.join(java_home, 'bin') + os.pathsep + environment.get('PATH', '')

    # Cap the heap. The launcher hard-codes -Xms2048m -Xmx4096m and ignores JAVA_OPTS, but the
    # JVM reads _JAVA_OPTIONS after the command line. -Xms has to come down too or Java won't start.
    max_heap = getattr(settings, 'VALIDATE_JAVA_MAX_HEAP', '')
    # Skip if already set, since the child builds this again from what it inherited.
    existing = environment.get('_JAVA_OPTIONS', '')
    if max_heap and '-Xmx' not in existing:
        environment['_JAVA_OPTIONS'] = '{} -Xms64m -Xmx{}'.format(existing, max_heap).strip()

    # veraPDF copies each PDF to java.io.tmpdir and never deletes it, so use a directory
    # for this run, which run() removes at the end.
    if java_tmpdir:
        environment['_JAVA_OPTIONS'] = '{} -Djava.io.tmpdir={}'.format(
            environment.get('_JAVA_OPTIONS', ''), java_tmpdir).strip()

    return environment


def work_dir():
    """Where the schema cache and reports live.

    Uses getattr with a default since elsa/settings.py is gitignored. See docs/pds_validation_setup.md.
    """
    return getattr(settings, 'VALIDATE_WORK_DIR',
                   os.path.join(settings.BASE_DIR, 'validation'))


def label_count(bundle):
    """How many XML labels the bundle holds, which is what progress is measured against."""
    total = 0
    for _dirpath, _dirnames, filenames in os.walk(bundle.directory()):
        total += sum(1 for name in filenames if name.lower().endswith('.xml'))
    return total


# Raw reports to keep per bundle, so the disk doesn't fill up.
REPORTS_KEPT_PER_BUNDLE = 10


def reports_dir():
    reports = os.path.join(work_dir(), 'reports')
    os.makedirs(reports, exist_ok=True)
    return reports


def report_path_for(run):
    return os.path.join(reports_dir(), 'run-{}.json'.format(run.pk))


def java_tmpdir_for(run):
    """The temporary directory validate gets for one run. Removed by run()."""
    return os.path.join(work_dir(), 'tmp', 'run-{}'.format(run.pk))


def prune_reports(bundle):
    """Delete raw reports for all but this bundle's latest runs.

    The ValidationRun rows stay. report_path is cleared on rows whose file is gone.
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


def build_command(run, report_path, executable=None):
    """The validate command for this run's tier. `executable` runs a different release."""
    if executable is None:
        executable = validate_executable()
        # Check here so missing Java shows up as the failure reason in plain words.
        from build.validate_release import ReleaseError
        try:
            configured_java_home()
        except ReleaseError as error:
            raise ValidateUnavailable(str(error))

    command = [
        executable,
        '-R', 'pds4.bundle',
        '-t', run.bundle.directory(),
        '-r', report_path,
        '-s', 'json',
        # Prints the counters for the progress bar.
        '--progressN', '1',
    ]

    catalog = os.path.join(work_dir(), 'catalog.xml')
    if os.path.exists(catalog):
        # Use the local schema cache so we don't depend on pds.nasa.gov (`manage.py build_schema_catalog`).
        command += ['-C', catalog]

    # Run content validation on the structure tier too. It only costs about a second on real
    # bundles, and error.label.missing_file only shows up with it. The setting is there for
    # a host with large tables, where it could be slow.
    if getattr(settings, 'VALIDATE_SKIP_CONTENT', False):
        command.append('--skip-content-validation')

    return command


def run(run_id):
    """Run validate for one ValidationRun and update the row as it goes.

    Always leaves the row in a final state, since RUNNING looks like a job still working.
    """
    validation_run = ValidationRun.objects.get(pk=run_id)

    # Record anything unexpected on the row. If it escapes, the row stays at RUNNING
    # and holds a slot other bundles are waiting on.
    try:
        return _execute(validation_run)
    except Exception as error:                       # noqa: BLE001 - see comment
        return _fail(validation_run, 'The check stopped unexpectedly ({}: {}). Running '
                     'it again is safe; if it keeps happening, the server needs '
                     'looking at.'.format(type(error).__name__, error))
    finally:
        shutil.rmtree(java_tmpdir_for(validation_run), ignore_errors=True)


def repair_before_check(bundle):
    """Repair an External bundle's labels. Returns the fixes.

    Runs before each check and before submission. Archive labels are left to repair_labels.
    If this fails we still run the check, and validate will report what's wrong.
    """
    if bundle.bundle_type != 'External':
        return []
    from build.label_repair import repair_bundle
    try:
        return repair_bundle(bundle)
    except Exception as error:                           # noqa: BLE001 - see docstring
        print('repair_before_check: bundle {}: {}: {}'.format(
            bundle.pk, type(error).__name__, error))
        return []


def _execute(validation_run):
    """The body of run(), for one ValidationRun already fetched."""
    validation_run.status = ValidationRun.STATUS_RUNNING
    validation_run.started_at = timezone.now()
    validation_run.bundle_updated_at = validation_run.bundle.updated_at
    # Upgrade and repair before the fingerprint so the result isn't out of date as soon as it finishes.
    from build import label_upgrades
    label_upgrades.safe_upgrade(validation_run.bundle, source=label_upgrades.SOURCE_CHECK,
                                wait_seconds=120)
    repair_before_check(validation_run.bundle)
    # Taken here so an edit made before the JVM starts counts as a change.
    validation_run.content_fingerprint = validation_run.bundle.content_fingerprint()
    from build.checker import checker_version
    validation_run.checker_version = checker_version()
    validation_run.products_total = label_count(validation_run.bundle)
    validation_run.products_done = 0
    validation_run.phase = ValidationRun.PHASE_LOADING
    validation_run.save()

    report_path = report_path_for(validation_run)

    try:
        command = build_command(validation_run, report_path)
    except ValidateUnavailable as error:
        return _fail(validation_run, str(error))

    timeout = getattr(settings, 'VALIDATE_TIMEOUT_SECONDS', 3600)

    # Not being able to make this directory shouldn't stop the check. validate just uses /tmp.
    java_tmpdir = java_tmpdir_for(validation_run)
    try:
        os.makedirs(java_tmpdir, exist_ok=True)
    except OSError:
        java_tmpdir = None

    try:
        # errors='replace' because a file name validate echoes back is not
        # guaranteed to be UTF-8, and one undecodable byte would otherwise end the
        # progress reader and leave the pipe to fill.
        #
        # Its own session because bin/validate is a shell script that runs java as a
        # child rather than exec-ing it. Killing the script alone on timeout left the
        # JVM running, orphaned and outside VALIDATE_MAX_CONCURRENT; killing the
        # group takes both.
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            env=_environment(java_tmpdir), text=True, errors='replace', bufsize=1,
            start_new_session=True)
    except OSError as error:
        return _fail(validation_run, 'Could not run validate: {}'.format(error))

    # Read progress on its own thread so the timeout below works if validate hangs.
    # validate keeps stdout open, so waiting on the read would never time out.
    tail = collections.deque(maxlen=40)
    reader = threading.Thread(
        target=_track_progress, args=(process, validation_run, tail), daemon=True)
    reader.start()

    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            process.kill()
        process.wait()
        return _fail(validation_run,
                     'Validation ran longer than VALIDATE_TIMEOUT_SECONDS ({}s) and '
                     'was stopped.'.format(timeout))

    # Let the reader finish the pipe, but don't wait forever. The process has already exited.
    reader.join(timeout=10)

    # Non-zero exit just means errors were found. Only a missing report means it couldn't run.
    if not os.path.exists(report_path):
        said = last_words(tail)
        return _fail(validation_run,
                     'validate exited with status {} without writing a report.{}'.format(
                         process.returncode, ' It said: ' + said if said else ''))

    # The report could be half-written if validate was killed. Don't let that leave the row at RUNNING.
    try:
        parsed = parse_report(report_path)
    except (ValueError, OSError) as error:
        return _fail(validation_run,
                     'validate wrote a report that could not be read: {}'.format(error))

    validation_run.report_path = report_path
    validation_run.findings = parsed['findings']
    validation_run.error_count = parsed['summary']['errors']
    validation_run.warning_count = parsed['summary']['warnings']
    validation_run.tool_version = parsed['summary']['tool_version'][:32]
    validation_run.phase = ValidationRun.PHASE_DONE
    validation_run.products_done = validation_run.products_total
    validation_run.status = ValidationRun.STATUS_DONE
    validation_run.finished_at = timezone.now()
    validation_run.save()

    prune_reports(validation_run.bundle)
    notify_if_slow(validation_run)
    return validation_run


QUEUED_GRACE_SECONDS = 300

BUSY_REASON = ('The server is already running as many validations as it allows at '
               'once.')


def reap_abandoned_runs(bundle=None):
    """Fail runs that can't still be going, and say why. Returns how many.

    A child can die without recording anything (deploy, OOM, restart), and its row would then
    block every future validation of the bundle. A run is abandoned once it's older than its
    timeout plus a margin.
    """
    timeout = getattr(settings, 'VALIDATE_TIMEOUT_SECONDS', 3600)
    # Margin for the gap between creating the row and the child starting.
    cutoff = timezone.now() - timezone.timedelta(seconds=timeout + 300)

    # QUEUED this long means the child died on startup, so don't wait out the full timeout.
    queued_cutoff = timezone.now() - timezone.timedelta(seconds=QUEUED_GRACE_SECONDS)

    runs = ValidationRun.objects.filter(
        models.Q(status=ValidationRun.STATUS_RUNNING, requested_at__lt=cutoff)
        | models.Q(status=ValidationRun.STATUS_QUEUED, requested_at__lt=queued_cutoff))
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
    """The queued or running validation for this bundle, if any. Reaps abandoned runs first."""
    reap_abandoned_runs(bundle)

    runs = bundle.validation_runs.filter(
        status__in=[ValidationRun.STATUS_QUEUED, ValidationRun.STATUS_RUNNING])
    if tier is not None:
        runs = runs.filter(tier=tier)
    return runs.first()


def latest_run_for(bundle, tier=None):
    """This bundle's latest validation in any state. Reaps first so the panel can recover from a crash."""
    reap_abandoned_runs(bundle)

    runs = bundle.validation_runs.all()
    if tier is not None:
        runs = runs.filter(tier=tier)
    return runs.first()


def notify_if_slow(validation_run):
    """Email the owner only if the run took long enough that they probably stopped waiting.

    If sending fails we ignore it, the result is on the run either way.
    """
    threshold = getattr(settings, 'VALIDATE_EMAIL_AFTER_SECONDS', 30)
    duration = validation_run.duration_seconds()

    if duration is None or duration < threshold:
        return False

    user = validation_run.bundle.user
    if not getattr(user, 'email', ''):
        return False

    if validation_run.status == ValidationRun.STATUS_FAILED:
        subject = 'Validation could not finish: {}'.format(validation_run.bundle.name)
        body = (
            'The validation check on your bundle "{}" did not finish.\n\n'
            '{}\n\n'
            'You can run it again from the bundle page.\n'
        ).format(validation_run.bundle.name, validation_run.failure_reason)
    else:
        # Use the translated counts, most of the raw errors can be ELSA's own.
        from build import validate_rules
        summary = validate_rules.summarise(
            validation_run.findings or [],
            validate_rules.unresolved_items(validation_run.findings, validation_run.bundle))
        if summary['can_submit']:
            subject = 'Validation passed: {}'.format(validation_run.bundle.name)
            body = (
                'Your bundle "{}" has been checked against the PDS validation '
                'tool and nothing is blocking it from going for review.\n'
            ).format(validation_run.bundle.name)
        else:
            subject = 'Validation finished: {}'.format(validation_run.bundle.name)
            body = (
                'Your bundle "{}" has been checked against the PDS validation tool.\n\n'
                '{} item{} need{} your attention before it can be submitted. Open the '
                'bundle page to see what they are; each one has a Fix button that '
                'takes you to the right place.\n'
            ).format(validation_run.bundle.name, summary['blocking'],
                     '' if summary['blocking'] == 1 else 's',
                     's' if summary['blocking'] == 1 else '')

    try:
        EmailMessage(subject=subject, body=body, from_email='atm-elsa@nmsu.edu',
                     to=[user.email]).send()
        return True
    except Exception as error:
        print('Could not email validation result: {}'.format(error))
        return False


def _fail(validation_run, reason):
    """Mark a run as failed and tell the owner if it ran long."""
    validation_run.status = ValidationRun.STATUS_FAILED
    validation_run.failure_reason = reason
    validation_run.finished_at = timezone.now()
    validation_run.save()
    notify_if_slow(validation_run)
    return validation_run



# Reasons a bundle can't be submitted, shared by the panel and the view.
BLOCK_NOT_CHECKED = 'not_checked'
BLOCK_STALE = 'stale'
BLOCK_FINDINGS = 'findings'
BLOCK_UNRESOLVED = 'unresolved'
BLOCK_REQUIREMENTS = 'requirements'


def submission_block(bundle, user=None):
    """Why this bundle can't be submitted yet, or None if it can.

    Returns (reason_code, message). ELSA's own requirements are checked first, for everyone.

    For the validation part:
    - staff are never blocked, in case a rule is wrong
    - a validation that couldn't run doesn't block, since that's not the user's problem
    - it can all be turned off with VALIDATE_BLOCKS_SUBMISSION
    """
    from build import preflight, validate_rules

    # ELSA's own requirements come first. Being staff or turning validation off doesn't skip them.
    outstanding = preflight.requirements(bundle)
    if outstanding:
        return (BLOCK_REQUIREMENTS,
                '{} still needed before this bundle can go for review: {}.'.format(
                    'One thing is' if len(outstanding) == 1 else
                    '{} things are'.format(len(outstanding)),
                    '; '.join(item['title'] for item in outstanding)))

    if not getattr(settings, 'VALIDATE_BLOCKS_SUBMISSION', False):
        return None

    if user is not None and getattr(user, 'is_staff', False):
        return None

    latest = latest_run_for(bundle, ValidationRun.TIER_STRUCTURE)

    if latest is None or latest.status == ValidationRun.STATUS_QUEUED:
        return (BLOCK_NOT_CHECKED,
                'Run the PDS validation check before submitting, so anything it finds '
                'can be fixed here rather than coming back from review.')

    if latest.status == ValidationRun.STATUS_RUNNING:
        return (BLOCK_NOT_CHECKED,
                'The validation check is still running. It takes a few seconds.')

    latest = judged_run(bundle)
    if latest is None:
        # Cannot check is not the same as found a problem.
        return None

    reason = latest.stale_reason()
    if reason == ValidationRun.STALE_CHECKS:
        return (BLOCK_STALE,
                'ELSA\'s checks have been updated since this bundle was last checked. Run the '
                'check again so the result is from the current checks.')
    if reason is not None:
        return (BLOCK_STALE,
                'This bundle changed after it was last checked. Run the check again '
                'so the result describes what you are submitting.')

    # No requirement items here, they're handled above.
    unresolved = validate_rules.unresolved_items(latest.findings, bundle)
    summary = validate_rules.summarise(latest.findings or [], unresolved)
    if not summary['can_submit']:
        count = summary['blocking']
        # The user can't fix these, so don't tell them to look for a Fix button.
        if count == len(unresolved):
            return (BLOCK_UNRESOLVED, unresolved[0]['title'] + '. ' + unresolved[0]['detail'])
        return (BLOCK_FINDINGS,
                '{} item{} in the PDS Validation card still need{} attention. '
                'Each one has a Fix button.'.format(
                    count, '' if count == 1 else 's', 's' if count == 1 else ''))

    return None


def judged_run(bundle):
    """The finished check a bundle's verdict is based on, or None.

    Normally the latest structure run. If that failed, the previous finished check still
    counts as long as nothing changed. The gate and the fix list both use this.
    """
    latest = latest_run_for(bundle, ValidationRun.TIER_STRUCTURE)
    if latest is None or latest.status != ValidationRun.STATUS_FAILED:
        return latest

    previous = bundle.validation_runs.filter(
        tier=ValidationRun.TIER_STRUCTURE, status=ValidationRun.STATUS_DONE).first()
    if previous is None or previous.is_stale():
        return None
    return previous


def needs_check(bundle):
    """Whether the result no longer tells us if this can be submitted.

    True if never checked or changed since. False while running and after a failed check.
    Used by the bundle page and the Review & Submit window.
    """
    if active_run_for(bundle) is not None:
        return False

    latest = latest_run_for(bundle, ValidationRun.TIER_STRUCTURE)
    if latest is None:
        return True
    if latest.status == ValidationRun.STATUS_FAILED:
        return False
    return latest.is_stale()


def should_auto_check(bundle):
    """Whether the page should start a structure check by itself.

    True if there's nothing to show or the result is out of date. False while running and during
    the cooldown. The view itself never starts anything, so crawlers don't spawn a JVM.
    """
    if not getattr(settings, 'VALIDATE_AUTO_CHECK', False):
        return False

    if not needs_check(bundle):
        return False

    latest = latest_run_for(bundle, ValidationRun.TIER_STRUCTURE)
    if latest is None:
        return True

    # No cooldown by default. A result only goes stale when the files change, so page loads don't
    # start anything. The setting is there if a host wants one.
    if latest.finished_at is not None:
        debounce = getattr(settings, 'VALIDATE_AUTO_CHECK_DEBOUNCE_SECONDS', 0)
        if debounce and timezone.now() - latest.finished_at < timezone.timedelta(
                seconds=debounce):
            return False

    return True


def at_capacity():
    """True if this host is already running as many validations as it allows.

    Each run is a JVM on a shared server, so this is a limit for the whole install.
    """
    limit = getattr(settings, 'VALIDATE_MAX_CONCURRENT', 2)
    if not limit:
        return False

    reap_abandoned_runs()
    running = ValidationRun.objects.filter(
        status__in=[ValidationRun.STATUS_QUEUED, ValidationRun.STATUS_RUNNING]).count()
    return running >= limit


def start(bundle, tier=ValidationRun.TIER_STRUCTURE):
    """Queue a validation and launch it detached. Returns the ValidationRun.

    A run already going for this bundle is returned instead of starting a second one.
    """
    # Checking for a run and creating one have to be one step, or a double click starts two JVMs.
    # The row lock covers MariaDB. SQLite has no select_for_update, so the claim below handles that:
    # lowest id wins and only the winner launches.
    with transaction.atomic():
        Bundle.objects.select_for_update().filter(pk=bundle.pk).first()

        existing = active_run_for(bundle, tier)
        if existing is not None:
            return existing

        # Refused, not queued, since nothing would drain a queue. The page waits and asks again.
        # Not saved, because a busy server says nothing about the bundle.
        if at_capacity():
            return ValidationRun(
                bundle=bundle, tier=tier, status=ValidationRun.STATUS_FAILED,
                bundle_updated_at=bundle.updated_at, finished_at=timezone.now(),
                failure_reason=BUSY_REASON)

        validation_run = ValidationRun.objects.create(
            bundle=bundle, tier=tier, status=ValidationRun.STATUS_QUEUED,
            bundle_updated_at=bundle.updated_at,
            content_fingerprint=bundle.content_fingerprint())

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
            # Own session so a recycled wsgi worker doesn't take the run with it.
            start_new_session=True)
    except OSError as error:
        validation_run.status = ValidationRun.STATUS_FAILED
        validation_run.failure_reason = 'Could not start validation: {}'.format(error)
        validation_run.finished_at = timezone.now()
        validation_run.save()

    return validation_run
