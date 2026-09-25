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
            if validation_run is None:
                # Recording failed earlier. Keep reading anyway: validate blocks once
                # the pipe buffer fills, and a reader that stops would hang it until
                # the timeout.
                continue

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

    # Cap the heap. validate's launcher does not read JAVA_OPTS: it hard-codes
    # -Xms2048m -Xmx4096m on the java command line, so every run started at 2GB and
    # could grow to 4GB whatever this was set to. _JAVA_OPTIONS is read by the JVM
    # itself and applied after the command line, so it is the one place a host can
    # still win. -Xms has to come down with it: a ceiling below the launcher's 2GB
    # floor stops the JVM starting at all.
    max_heap = getattr(settings, 'VALIDATE_JAVA_MAX_HEAP', '')
    # Skipped when a ceiling is already there: the web process builds this for the
    # child, and the child builds it again for validate from what it inherited.
    existing = environment.get('_JAVA_OPTIONS', '')
    if max_heap and '-Xmx' not in existing:
        environment['_JAVA_OPTIONS'] = '{} -Xms64m -Xmx{}'.format(existing, max_heap).strip()

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

    # Content validation used to be skipped on the structure tier, on the assumption
    # that reading inside data files was the expensive part. Measured against real
    # bundles, it is not: 6s against 7s on a 184MB NetCDF bundle, and within noise on
    # both Archive bundles. An AMA data product is a Product_External, which only
    # references its file, so there is nothing inside for validate to read.
    #
    # What the skip did cost was a whole class of error. error.label.missing_file --
    # a label naming a file that is not there -- only appears with content
    # validation, and every ELSA document does this today, because a document can be
    # declared but its file cannot be uploaded. Skipping meant nobody was told until
    # PDS staff found it, which is the exact round trip this feature exists to avoid.
    #
    # The escape hatch remains for a host that meets a bundle with large tables,
    # where reading every field really would be slow.
    if getattr(settings, 'VALIDATE_SKIP_CONTENT', False):
        command.append('--skip-content-validation')

    return command


def run(run_id):
    """Execute validate for one ValidationRun, updating the row as it progresses.

    Runs in the foreground of whatever process calls it; `start()` is what makes
    that a detached child. Always leaves the row in a terminal state, because a row
    stuck at RUNNING is indistinguishable to the UI from a job still working.
    """
    validation_run = ValidationRun.objects.get(pk=run_id)

    # Anything unexpected is recorded on the row rather than escaping. The first time
    # this ran in production, the work directory did not exist and apache could not
    # create it: the PermissionError escaped, the child died with the row at RUNNING,
    # and for the next hour the page said "Checking..." and the row held one of the
    # two slots every other bundle was waiting on. A failed run says why, frees its
    # slot at once, and is shown as "could not run".
    try:
        return _execute(validation_run)
    except Exception as error:                       # noqa: BLE001 - see comment
        return _fail(validation_run, 'The check stopped unexpectedly ({}: {}). Running '
                     'it again is safe; if it keeps happening, the server needs '
                     'looking at.'.format(type(error).__name__, error))


def _execute(validation_run):
    """The body of run(), for one ValidationRun already fetched."""
    validation_run.status = ValidationRun.STATUS_RUNNING
    validation_run.started_at = timezone.now()
    validation_run.bundle_updated_at = validation_run.bundle.updated_at
    # Taken here rather than when the row was created, so an edit made between
    # pressing the button and the JVM starting counts as a change and the result is
    # correctly reported as out of date.
    validation_run.content_fingerprint = validation_run.bundle.content_fingerprint()
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
            env=_environment(), text=True, errors='replace', bufsize=1,
            start_new_session=True)
    except OSError as error:
        return _fail(validation_run, 'Could not run validate: {}'.format(error))

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
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            process.kill()
        process.wait()
        return _fail(validation_run,
                     'Validation ran longer than VALIDATE_TIMEOUT_SECONDS ({}s) and '
                     'was stopped.'.format(timeout))

    # Let the reader drain whatever is left in the pipe, but never wait on it
    # indefinitely: the process has already exited, so this is bounded work.
    reader.join(timeout=10)

    # A non-zero exit means errors were found, which is a normal outcome and not a
    # failure of the run. Only a missing report means validate could not do its job.
    if not os.path.exists(report_path):
        return _fail(validation_run,
                     'validate exited with status {} without writing a report.'.format(
                         process.returncode))

    # The report is written by another program that may have been killed partway
    # through. A half-written file raises here, and letting that escape would leave
    # the row at RUNNING, which wedges the bundle for good.
    try:
        parsed = parse_report(report_path)
    except (ValueError, OSError) as error:
        return _fail(validation_run,
                     'validate wrote a report that could not be read: {}'.format(error))

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
    notify_if_slow(validation_run)
    return validation_run


QUEUED_GRACE_SECONDS = 300

BUSY_REASON = ('The server is already running as many validations as it allows at '
               'once.')


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

    # A run still QUEUED is one whose child never got as far as marking it RUNNING,
    # which takes seconds. Past this it is a child that died on startup (a database
    # refusing connections, say), and waiting out the full timeout for it would hold
    # the bundle, and a slot under VALIDATE_MAX_CONCURRENT, for over an hour.
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


def notify_if_slow(validation_run):
    """Email the bundle's owner, but only for a run they are unlikely to have watched.

    A structure check takes a few seconds and its result is on the screen before an
    email could arrive. Mailing every run would mean five messages during one
    ten-minute fixing session, and a sender that does that gets filtered. So the
    message goes out only when the run took long enough that its owner had probably
    stopped waiting - a full content check on a large bundle, typically.

    Failure to send is swallowed. The result is recorded on the run either way, and
    a mail server having a bad afternoon must not turn a finished validation into a
    failed one.
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
        # Counts come from the translation, not from the raw totals: telling someone
        # their bundle has 43 errors when 41 of them are ELSA's own is not useful.
        from build import validate_rules
        summary = validate_rules.summarise(validation_run.findings or [])
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
    """Put a run into its terminal failed state and tell its owner if it ran long.

    Every failure exit goes through here rather than repeating four lines, so a new
    one cannot quietly forget the notification or, worse, forget finished_at and
    leave the row looking like work still in progress.
    """
    validation_run.status = ValidationRun.STATUS_FAILED
    validation_run.failure_reason = reason
    validation_run.finished_at = timezone.now()
    validation_run.save()
    notify_if_slow(validation_run)
    return validation_run



# Why a bundle cannot be submitted, if it cannot. Each is a state the panel and the
# view both have to agree about, so they are named rather than repeated as strings.
BLOCK_NOT_CHECKED = 'not_checked'
BLOCK_STALE = 'stale'
BLOCK_FINDINGS = 'findings'
BLOCK_REQUIREMENTS = 'requirements'


def submission_block(bundle, user=None):
    """Why this bundle may not be submitted yet, or None if it may.

    Returns (reason_code, message) so the page can explain and the view can refuse
    with the same words.

    ELSA's own requirements (a citation, a modification history, a target) are
    checked first and unconditionally. Everything below concerns validation only.

    Three deliberate holes in the validation part of the gate:

    Staff are never blocked. The people who would have to open the gate when a rule
    is wrong are the people operating it, and making them edit a setting to accept
    one bundle is how a gate becomes a thing everyone routes around.

    A validation that could not run does not block. If validate is missing or
    broken, blocking is the worst possible response: the node cannot receive
    anything at all, for a reason nobody outside the team can fix. Being unable to
    check is not evidence of a problem.

    And the whole thing can be turned off with VALIDATE_BLOCKS_SUBMISSION.
    """
    from build import preflight, validate_rules

    # ELSA's own requirements come first and are not subject to either hole below.
    # They are not part of the validate feature: the Review & Submit button has
    # refused a bundle without a citation, a modification history or a target since
    # long before validate existed, and turning validation off, or being staff, was
    # never meant to waive them. Enforcing them only by disabling the button left
    # them unenforced for anyone who posted the form anyway.
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

    if latest.is_stale():
        return (BLOCK_STALE,
                'This bundle changed after it was last checked. Run the check again '
                'so the result describes what you are submitting.')

    # No requirement items passed: they are handled above and would be counted
    # twice here.
    summary = validate_rules.summarise(latest.findings or [])
    if not summary['can_submit']:
        count = summary['blocking']
        return (BLOCK_FINDINGS,
                '{} item{} in the PDS Validation card still need{} attention. '
                'Each one has a Fix button.'.format(
                    count, '' if count == 1 else 's', 's' if count == 1 else ''))

    return None


def judged_run(bundle):
    """The finished check a bundle's verdict rests on, or None if there is none.

    Normally the latest structure run. When that one failed, a failure is not
    evidence of no problem: the previous finished check still describes the bundle
    if nothing has changed since, so it still decides. The gate and the list of
    things to fix both read this, so they cannot disagree about which run counts.
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
    """Whether the bundle's result no longer answers "can this be submitted".

    True when it has never been checked or has changed since it was. False while a
    check is in flight, and false after a failed check: whatever stopped it will
    almost certainly stop the next one too, so it waits to be asked.

    The one rule both callers use: the bundle page, which checks quietly in the
    background, and the Review & Submit window, which checks before it offers
    Submit.
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

    True when there is nothing to show, or what is shown no longer describes the
    bundle. False while a run is in flight, and false for a short cooldown after one
    finishes, so a user reloading the page while fixing things does not start a run
    on every load.

    This only ever decides whether the *page* offers to start one. The view never
    starts anything, so a crawler, a link preview or a HEAD request spawns no JVM.
    """
    if not getattr(settings, 'VALIDATE_AUTO_CHECK', False):
        return False

    if not needs_check(bundle):
        return False

    latest = latest_run_for(bundle, ValidationRun.TIER_STRUCTURE)
    if latest is None:
        return True

    # No cooldown by default, where this used to wait five minutes.
    #
    # That cooldown existed because staleness was measured against a timestamp that
    # never moved: without it, every page load would have started a run. Now a result
    # only looks stale when the bundle's files actually changed, which page loads were
    # measured not to do, so an untouched bundle starts nothing however often it is
    # opened. The wait only got in the way, because someone who fixed the thing the
    # panel told them to fix then had to sit it out, or press the button, to see the
    # panel agree with them.
    #
    # This is also self-limiting rather than merely rate-limited. A run makes the
    # result match the files, so the next load is not stale and starts nothing; it
    # repeats only while someone keeps editing, which is when it should. Per-bundle
    # deduplication already refuses a second run while one is in flight.
    #
    # The setting remains as a safety valve for a host that wants one.
    if latest.finished_at is not None:
        debounce = getattr(settings, 'VALIDATE_AUTO_CHECK_DEBOUNCE_SECONDS', 0)
        if debounce and timezone.now() - latest.finished_at < timezone.timedelta(
                seconds=debounce):
            return False

    return True


def at_capacity():
    """True when as many validations are already running as this host allows.

    Per-bundle deduplication stops one bundle starting two runs. It says nothing
    about twenty people opening twenty bundles at once, and each run is a JVM on a
    machine shared with other services - at the time of writing, two Tomcat
    instances, Apache, MariaDB and other people's work. This is the ceiling for the
    whole install rather than for one bundle.
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

        # Refused rather than queued: a queue needs something to drain it, and there
        # is no worker process here. The page waits and asks again.
        #
        # Not saved. A refusal says the server was busy, not anything about the
        # bundle, and stored as the latest run it was read as a result: the badge
        # stuck at "Could not run", the panel dropped the findings of the check
        # before it, and nothing retried because failed runs are not retried.
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
            # Its own session, so a recycled wsgi worker does not take the run with
            # it. The child records its own result, so nothing is waiting on it.
            start_new_session=True)
    except OSError as error:
        validation_run.status = ValidationRun.STATUS_FAILED
        validation_run.failure_reason = 'Could not start validation: {}'.format(error)
        validation_run.finished_at = timezone.now()
        validation_run.save()

    return validation_run
