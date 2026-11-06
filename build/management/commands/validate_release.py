"""Keep this host's PDS validate release current, with a person deciding the switch.

    python3 manage.py validate_release status
    python3 manage.py validate_release check          # what the monthly cron runs
    python3 manage.py validate_release install 5.1.0
    python3 manage.py validate_release qualify 5.1.0
    python3 manage.py validate_release switch 5.1.0
    python3 manage.py validate_release rollback
    python3 manage.py validate_release prune --dry-run

`check` finds a new stable release on GitHub, downloads and verifies it, makes sure a
Java new enough to run it is installed, compares it with the release in use over
every bundle (and the validation test suites, where the host has them), and emails
the result once. It never switches anything. `switch` is the human step: it proves
the release starts, then points VALIDATE_CURRENT_LINK at it. `rollback` undoes the
last switch. After either, and after `check` installs a release, releases nothing
needs any more are removed (see validate_release.prune for what is kept).

A host takes part by following the link: VALIDATE_HOME set to VALIDATE_CURRENT_LINK
and VALIDATE_JAVA_HOME = 'auto'. See docs/pds_validation_setup.md.
"""
import fcntl
import getpass
import json
import os
import socket

from django.conf import settings
from django.core.mail import EmailMessage
from django.core.management.base import BaseCommand, CommandError
from django.template.loader import render_to_string

from build import validate_qualify, validate_release
from build.validate_release import ReleaseError

DEFAULT_RECIPIENTS = ['rupakdey@nmsu.edu']
PRODUCTION_DIR = '/export/atmos1/htdocs/elsa'
PRODUCTION_PYTHON = '/usr/local/venvs/ELSA_3.11/bin/python'


def upgrades_dir():
    path = os.path.join(os.path.dirname(validate_release._history_path()))
    os.makedirs(path, exist_ok=True)
    return path


def load_state():
    try:
        with open(os.path.join(upgrades_dir(), 'state.json'), encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def save_state(state):
    path = os.path.join(upgrades_dir(), 'state.json')
    with open(path + '.tmp', 'w', encoding='utf-8') as handle:
        json.dump(state, handle, indent=1)
    os.replace(path + '.tmp', path)


def in_use():
    """(version, directory) of the release this host's VALIDATE_HOME resolves to."""
    home = os.path.realpath(getattr(settings, 'VALIDATE_HOME', '') or '')
    return validate_release.release_version(home), home


class Command(BaseCommand):
    help = 'Check for, qualify, and switch to new PDS validate releases.'

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest='action', required=True)
        sub.add_parser('status', help='What this host runs and what is available.')

        check = sub.add_parser('check', help='Find, verify and qualify a new release, then email.')
        check.add_argument('--release', help='Qualify this release instead of the latest.')
        check.add_argument('--force', action='store_true',
                           help='Qualify and email again even if this release was reported.')
        check.add_argument('--no-email', action='store_true', help='Print the report instead.')
        check.add_argument('--to', action='append', help='Send to this address instead.')
        check.add_argument('--skip-tests', action='store_true')
        check.add_argument('--limit', type=int, help='Compare only this many bundles.')
        check.add_argument('--baseline', help='Compare with this installed release instead of the one in use.')
        check.add_argument('--trial', action='store_true',
                           help='Send a sample: subject marked [Trial], nothing recorded or removed.')

        install = sub.add_parser('install', help='Download and verify a release.')
        install.add_argument('version')

        qualify = sub.add_parser('qualify', help='Compare a release with the one in use.')
        qualify.add_argument('version')
        qualify.add_argument('--skip-tests', action='store_true')
        qualify.add_argument('--limit', type=int)

        switch = sub.add_parser('switch', help='Point this host at an installed release.')
        switch.add_argument('version')
        switch.add_argument('--install', action='store_true',
                            help='Download and verify it first if it is not installed.')
        switch.add_argument('--private', action='store_true',
                            help='Allow a release only its owner can read (development hosts).')

        prune = sub.add_parser('prune', help='Remove installed releases nothing needs any more.')
        prune.add_argument('--dry-run', action='store_true', help='Say what would go, remove nothing.')

        rollback = sub.add_parser('rollback', help='Go back to the release used before the last switch.')
        rollback.add_argument('--private', action='store_true',
                              help='Allow a release only its owner can read (development hosts).')

    def handle(self, *args, **options):
        try:
            getattr(self, 'do_' + options['action'])(options)
        except ReleaseError as error:
            raise CommandError(str(error))

    # -- status ------------------------------------------------------------------

    def do_status(self, options):
        version, home = in_use()
        write = self.stdout.write
        write('VALIDATE_HOME       {}'.format(getattr(settings, 'VALIDATE_HOME', '') or '(not set)'))
        write('  resolves to       {} (validate {})'.format(home, version or 'none'))
        write('VALIDATE_JAVA_HOME  {}'.format(getattr(settings, 'VALIDATE_JAVA_HOME', '') or '(not set)'))
        if version:
            try:
                write('  needs Java {}, would use {}'.format(
                    validate_release.required_java(home), validate_release.java_home_for(home)))
            except ReleaseError as error:
                write(self.style.ERROR('  ' + str(error)))
        write('Link                {} -> {}'.format(
            validate_release.current_link(), validate_release.linked_release() or '(none)'))
        if not validate_release.host_follows_link():
            write(self.style.WARNING(
                '  This host does not follow the link yet, so switch and rollback do not '
                'affect it. See docs/pds_validation_setup.md, "Following new releases".'))
        installed = validate_release.installed_releases()
        write('Installed releases  {}'.format(', '.join(installed) or 'none'))
        try:
            latest = validate_release.latest_release()
            write('Latest on GitHub    {} (published {})'.format(
                latest['version'], latest['published_at'][:10]))
        except Exception as error:                      # noqa: BLE001 - informational
            write('Latest on GitHub    could not ask: {}'.format(error))
        for entry in validate_release.history()[-5:]:
            write('Switched            {} {} -> {} by {}'.format(
                entry['at'], entry['from'] or '(nothing)', entry['to'], entry['by'] or '?'))
        for release, entry in sorted(load_state().items()):
            write('Checked             {}: {} at {}'.format(release, entry['status'], entry['at']))

    # -- install / qualify / switch / rollback -------------------------------------

    def do_install(self, options):
        info = validate_release.release_info(options['version'])
        home = validate_release.install(info)
        self.stdout.write(self.style.SUCCESS('validate {} verified (sha256 {}) and installed at {}'.format(
            info['version'], info['sha256'], home)))

    def do_qualify(self, options):
        home = self._installed(options['version'])
        result, tests = self._qualify(home, options.get('skip_tests'), options.get('limit'))
        self.stdout.write(validate_qualify.render(result, tests))

    def do_switch(self, options):
        version = options['version']
        if options.get('install') and version not in validate_release.installed_releases():
            self.do_install(options)
        record = validate_release.switch(version, who=getpass.getuser(),
                                         private=options.get('private'))
        self.stdout.write(self.style.SUCCESS('{} now points at validate {} (was {}), run with {}.'.format(
            validate_release.current_link(), record['to'], record['from'] or 'nothing',
            record['java_home'])))
        self._after_switch()

    def do_rollback(self, options):
        record = validate_release.rollback(who=getpass.getuser(), private=options.get('private'))
        self.stdout.write(self.style.SUCCESS('Rolled back: {} now points at validate {} (was {}).'.format(
            validate_release.current_link(), record['to'], record['from'])))
        self._after_switch()

    def do_prune(self, options):
        self._prune(dry_run=options.get('dry_run'))

    def _prune(self, dry_run=False):
        keep = validate_release.releases_to_keep()
        if keep is None:
            self.stdout.write('Not removing anything: nothing says which release is in use here.')
            return
        for version, why in sorted(keep.items(), key=lambda item: validate_release.version_key(item[0])):
            self.stdout.write('Keeping  validate {} ({})'.format(version, why))
        removed = validate_release.prune(dry_run=dry_run)
        for path, why in removed:
            self.stdout.write('{} {} ({})'.format('Would remove' if dry_run else 'Removed ', path, why))
        if not removed:
            self.stdout.write('Nothing to remove.')

    def _after_switch(self):
        self._prune()
        if not validate_release.host_follows_link():
            self.stdout.write(self.style.WARNING(
                'This host does not follow the link yet. In elsa/secrets.py set\n'
                "    VALIDATE_HOME = '{}'\n    VALIDATE_JAVA_HOME = 'auto'\n"
                'then touch elsa/wsgi.py.'.format(validate_release.current_link())))
        elif getattr(settings, 'VALIDATE_JAVA_HOME', '') != 'auto':
            self.stdout.write(self.style.WARNING(
                "VALIDATE_JAVA_HOME is not 'auto', so checks still use {}. Set it to 'auto' "
                'in elsa/secrets.py.'.format(settings.VALIDATE_JAVA_HOME)))
        else:
            self.stdout.write('The next check uses it; nothing needs restarting.')

    def _installed(self, version):
        installed = validate_release.installed_releases()
        if version not in installed:
            raise ReleaseError('validate {} is not installed here. Run: manage.py '
                               'validate_release install {}'.format(version, version))
        return installed[version]

    def _qualify(self, home, skip_tests, limit, baseline_home=None):
        def progress(done, total):
            if done == total or done % 50 == 0:
                self.stdout.write('  compared {}/{} runs'.format(done, total))

        baseline_home = os.path.realpath(baseline_home or settings.VALIDATE_HOME)
        result = validate_qualify.qualify(home, baseline_home=baseline_home, limit=limit,
                                          progress=progress)
        tests = None
        if not skip_tests:
            before = validate_qualify.run_tests(
                baseline_home, validate_release.java_home_for(baseline_home))
            if before is not None:
                after = validate_qualify.run_tests(home, validate_release.java_home_for(home))
                # Only what the new release breaks. A test already failing with the
                # release in use is somebody's work in progress, not this release.
                tests = {'baseline': before, 'candidate': after,
                         'new_failures': sorted(set(after['failed']) - set(before['failed']))}
                if not after['ok'] and not after['failed']:
                    tests['new_failures'] = ['the suite did not finish: ' + after['summary']]
        return result, tests

    # -- check (the cron entry) ----------------------------------------------------

    def do_check(self, options):
        lock = open(os.path.join(upgrades_dir(), 'check.lock'), 'w')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stdout.write('Another check is already running.')
            return
        try:
            self._check(options)
        finally:
            lock.close()

    def _check(self, options):
        current, baseline_home = in_use()
        if options.get('baseline'):
            baseline_home = self._installed(options['baseline'])
            current = options['baseline']
        # A trial sends a real report to show what one looks like; like a preview it
        # must leave no trace that would stop the real one being sent later.
        quiet = options.get('no_email') or options.get('trial')
        if options.get('release'):
            info = validate_release.release_info(options['release'])
        else:
            info = validate_release.latest_release()
            if validate_release.version_key(info['version']) <= validate_release.version_key(current):
                self.stdout.write('validate {} is the latest stable release and is in use.'.format(current))
                return
        version = info['version']

        state = load_state()
        previous = state.get(version, {}).get('status')
        if previous == 'reported' and not options.get('force') and not options.get('trial'):
            self.stdout.write('validate {} was already qualified and reported.'.format(version))
            return

        def record(kind, status, context, attachment=None):
            # Each blocked state is emailed once; the monthly run retries silently and
            # writes again only when something moves on.
            if previous != status or options.get('force') or options.get('trial'):
                self._send(kind, context, attachment, options)
            # A preview tells nobody, and a trial is not the real thing, so neither may
            # stop the real run from telling them.
            if quiet:
                return
            state[version] = {'status': status, 'at': validate_release._now()}
            save_state(state)

        base = {'version': version, 'current': current or 'none', 'info': info,
                'trial': bool(options.get('trial'))}

        self.stdout.write('validate {} is out (in use: {}). Installing and verifying.'.format(
            version, current or 'none'))
        try:
            home = validate_release.install(info)
        except Exception as error:                      # noqa: BLE001 - reported
            record('install_failed', 'install_failed', dict(base, problem=str(error)))
            return

        # Only when a newer release is on hand (an ordinary month returned above), and
        # never on a preview or a trial, which must change nothing.
        if not quiet:
            self._prune()

        try:
            java_home = validate_release.java_home_for(home)
        except ReleaseError as error:
            record('needs_java', 'needs_java', dict(
                base, problem=str(error), java=validate_release.required_java(home)))
            return

        try:
            validate_release.smoke_test(home, java_home)
        except ReleaseError as error:
            record('does_not_start', 'does_not_start', dict(
                base, problem=str(error), java_home=java_home))
            return

        self.stdout.write('Comparing validate {} with {} over every bundle.'.format(version, current))
        result, tests = self._qualify(home, options.get('skip_tests'), options.get('limit'),
                                      baseline_home=baseline_home)
        report = validate_qualify.render(result, tests)
        with open(os.path.join(upgrades_dir(), 'report-{}.txt'.format(version)), 'w',
                  encoding='utf-8') as handle:
            handle.write(report)

        kind = 'ready' if validate_qualify.is_clean(result, tests) else 'review'
        record(kind, 'reported', dict(base, result=result, tests=tests),
               ('validate-{}-comparison.txt'.format(version), report))

    # -- the email -----------------------------------------------------------------

    VERDICT_COLOURS = {
        'ready': ('#1e7e5a', '#e8f5ef'),
        'review': ('#a8670a', '#fff6e5'),
        'blocked': ('#c0392b', '#fdecea'),
    }
    GOOD, WARN, BAD, NEUTRAL = '#1e7e5a', '#a8670a', '#c0392b', '#008080'

    def _email_context(self, kind, context):
        """Everything templates/build/email/validate_release.* show, for one outcome."""
        version, current, info = context['version'], context['current'], context['info']
        published = _long_date(info.get('published_at') or '')
        plural = lambda n: '' if n == 1 else 's'              # noqa: E731

        ctx = {
            'kind': kind, 'trial': context['trial'], 'version': version, 'current': current,
            'notes_url': info.get('notes_url', ''), 'problem': context.get('problem', ''),
            'host': '{}@{}'.format(getpass.getuser(), socket.gethostname()),
            'tiles': [], 'changed': [], 'steps': [], 'attached': kind in ('ready', 'review'),
        }
        details = [('Released', published), ('In use now', 'validate {}'.format(current))]
        if info.get('sha256'):
            details.append(('Download', 'SHA-256 {}...{} checked against GitHub'.format(
                info['sha256'][:8], info['sha256'][-6:])))

        dev_python = 'python3 manage.py validate_release'
        prod_python = '{} manage.py validate_release'.format(PRODUCTION_PYTHON)
        switch_steps = [
            {'label': 'Development (--private because only you can read this checkout):',
             'command': 'cd {}\n{} switch {} --private'.format(settings.BASE_DIR, dev_python, version)},
            {'label': 'Production, as sajomont:',
             'command': 'cd {}\n{} switch {} --install'.format(PRODUCTION_DIR, prod_python, version)},
            {'label': 'To undo on development:',
             'command': 'cd {}\n{} rollback --private'.format(settings.BASE_DIR, dev_python)},
            {'label': 'To undo on production, as sajomont:',
             'command': 'cd {}\n{} rollback'.format(PRODUCTION_DIR, prod_python)},
        ]

        if kind in ('ready', 'review'):
            result, tests = context['result'], context['tests']
            bundles, changed = result['bundles'], result['changed']
            new_failures = (tests or {}).get('new_failures') or []
            ctx['tiles'] = [
                {'value': bundles, 'label': 'bundles compared', 'color': self.NEUTRAL},
                {'value': result['identical'], 'label': 'identical',
                 'color': self.GOOD if not changed else self.WARN},
                {'value': result['gate_flips'], 'label': 'submit decisions changed',
                 'color': self.GOOD if not result['gate_flips'] else self.BAD},
                {'value': len(new_failures) if tests else 'n/a', 'label': 'tests newly failing',
                 'color': self.GOOD if tests and not new_failures else (self.BAD if new_failures else '#999999')},
            ]
            ordered = sorted(changed, key=lambda b: not any(c.startswith('GATE') for c in b['changes']))
            for bundle in ordered[:8]:
                ctx['changed'].append({
                    'name': '/'.join(bundle['dir'].rstrip('/').split('/')[-2:]),
                    'dir': bundle['dir'],
                    'gate': any(c.startswith('GATE') for c in bundle['changes']),
                    'changes': bundle['changes'][:6] + (['...'] if len(bundle['changes']) > 6 else []),
                })
            ctx['changed_more'] = max(0, len(changed) - 8)
            ctx['lead'] = ('NASA released validate {} on {}. ELSA downloaded it, verified it, and '
                           'ran it beside {}, the release in use, over every bundle and the '
                           'validation tests.'.format(version, published, current))
            if kind == 'ready':
                ctx['headline'] = 'validate {} is ready'.format(version)
                ctx['verdict_title'] = 'Safe to switch'
                ctx['verdict_text'] = (
                    'Nothing a user sees changes on any of the {} bundle{}, no submit decision '
                    'changes, and no validation test fails that did not already fail with {}.'
                    .format(bundles, plural(bundles), current))
            else:
                ctx['headline'] = 'validate {} needs a look'.format(version)
                ctx['verdict_title'] = '{} bundle{} would change'.format(len(changed), plural(len(changed)))
                parts = []
                if result['gate_flips']:
                    parts.append('the submit decision changes on {}'.format(result['gate_flips']))
                if new_failures:
                    parts.append('{} test{} newly fail{}'.format(
                        len(new_failures), plural(len(new_failures)), 's' if len(new_failures) == 1 else ''))
                ctx['verdict_text'] = (
                    'Switching would change what users are told{}. Each change is listed below. '
                    'A new kind of finding usually means build/validate_rules.py needs a rule '
                    'before switching.'.format(' ({})'.format('; '.join(parts)) if parts else ''))
            ctx['steps_title'] = 'When you decide to switch'
            ctx['steps'] = switch_steps
            ctx['steps_note'] = ('If production is not on the link yet, do the one-time steps in '
                                 'docs/pds_validation_setup.md, section 7, first.')
            details += [
                ('Java', 'Java {} for {}, Java {} for {}'.format(
                    validate_release._java_major(result['candidate_java']), version,
                    validate_release._java_major(result['baseline_java']), current)),
                ('Time to check every bundle', '{:.0f} min with {}, {:.0f} min with {}'.format(
                    result['seconds']['candidate'] / 60, version,
                    result['seconds']['baseline'] / 60, current)),
            ]
            if tests:
                details.append(('Validation tests', '{} with {}; {} with {}'.format(
                    tests['candidate']['ran'].replace('Ran ', '').split(' in ')[0] + ' ' +
                    tests['candidate']['summary'], version,
                    tests['baseline']['ran'].replace('Ran ', '').split(' in ')[0] + ' ' +
                    tests['baseline']['summary'], current)))
            details.append(('Bundles from', ', '.join(result['roots'])))
        else:
            ctx['verdict_title'] = 'Nothing was changed'
            ctx['footer'] = ('This check runs on the 1st of each month from {} and writes again '
                             'when something moves on.'.format(ctx['host']))
            if kind == 'needs_java':
                java = context['java']
                ctx['headline'] = 'validate {} needs Java {}'.format(version, java)
                ctx['verdict_title'] = 'Waiting on Java {}'.format(java)
                ctx['lead'] = ('NASA released validate {} on {}. ELSA downloaded and verified it, '
                               'but this server has no Java new enough to run it.'.format(version, published))
                ctx['verdict_text'] = ('validate {} stays in use. Once Java {} is installed, the '
                                       'monthly check compares the two releases and writes again.'
                                       .format(current, java))
                ctx['steps_title'] = 'To unblock it'
                ctx['steps'] = [{'label': 'Someone with root installs Java {} (Temurin, as Java 25 was):'.format(java),
                                 'command': 'sudo dnf install temurin-{}-jdk'.format(java)}]
                ctx['steps_note'] = 'Nothing else is needed: ELSA finds the new Java by itself.'
            elif kind == 'install_failed':
                ctx['headline'] = 'validate {} could not be installed'.format(version)
                ctx['lead'] = 'NASA released validate {} on {}, but ELSA could not install it.'.format(
                    version, published)
                ctx['verdict_text'] = ('validate {} stays in use. Nothing was unpacked; the monthly '
                                       'check tries again.'.format(current))
            else:
                ctx['headline'] = 'validate {} does not start'.format(version)
                ctx['lead'] = ('NASA released validate {} on {}. ELSA installed it, but it did not '
                               'start under {}.'.format(version, published, context['java_home']))
                ctx['verdict_text'] = 'validate {} stays in use.'.format(current)

        palette = {'ready': 'ready', 'review': 'review', 'needs_java': 'review'}.get(kind, 'blocked')
        ctx.setdefault('footer', '{}Nothing is switched until someone runs one of the commands '
                                 'above. This check runs on the 1st of each month from {}.'.format(
                                     'The full comparison, bundle by bundle, is attached. '
                                     if ctx['attached'] else '', ctx['host']))
        ctx['verdict_color'], ctx['verdict_bg'] = self.VERDICT_COLOURS[palette]
        ctx['details'] = details
        return ctx

    def _subject(self, kind, context):
        version = context['version']
        if kind == 'ready':
            n = context['result']['bundles']
            subject = 'validate {} is ready: identical on all {} bundles'.format(version, n)
        elif kind == 'review':
            n = len(context['result']['changed'])
            subject = 'validate {} needs a look: {} bundle{} would change'.format(
                version, n, '' if n == 1 else 's')
        elif kind == 'needs_java':
            subject = 'validate {}: needs a newer Java on the server'.format(version)
        elif kind == 'install_failed':
            subject = 'validate {}: could not be installed'.format(version)
        else:
            subject = 'validate {}: does not start'.format(version)
        return ('[Trial] ' if context['trial'] else '') + subject

    def _send(self, kind, context, attachment, options):
        subject = self._subject(kind, context)
        ctx = self._email_context(kind, context)
        text = render_to_string('build/email/validate_release.txt', ctx)
        if options.get('no_email'):
            self.stdout.write('--- email not sent (--no-email) ---\nSubject: {}\n\n{}'.format(subject, text))
            return
        recipients = options.get('to') or getattr(
            settings, 'VALIDATE_UPGRADE_RECIPIENTS', DEFAULT_RECIPIENTS)
        build_report_email(subject, text, ctx, recipients, attachment).send()
        self.stdout.write(self.style.SUCCESS('Emailed {}: {}'.format(', '.join(recipients), subject)))


def _long_date(timestamp):
    """'2026-10-02T20:05:13Z' -> 'October 2, 2026'. Unparseable stays as given."""
    import datetime
    try:
        day = datetime.date.fromisoformat(timestamp[:10])
    except ValueError:
        return timestamp or 'unknown'
    return '{} {}, {}'.format(day.strftime('%B'), day.day, day.year)


class _Prebuilt(EmailMessage):
    """An EmailMessage whose MIME tree was built by hand (see build_report_email)."""

    def __init__(self, mime, **kwargs):
        super().__init__(**kwargs)
        self._mime = mime

    def message(self):
        return self._mime


def build_report_email(subject, text, ctx, recipients, attachment=None):
    """The release email, in ELSA's branded account-email shell, ready to send.

    Built by hand because it needs a shape Django will not produce: the logo has to
    sit beside the HTML in multipart/related, while the report is a real attachment
    one level out, in multipart/mixed. Django puts both in one container, and mail
    clients then either hide the attachment or show the logo as one.

        mixed
          related
            alternative (text, html)
            logo
          report.txt
    """
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.utils import formatdate, make_msgid

    try:
        from friends.mail import EMAIL_LOGO_CID, FROM_ADDRESS, _elsa_logo_attachment
        logo = _elsa_logo_attachment()
    except ImportError:
        EMAIL_LOGO_CID, FROM_ADDRESS, logo = '', 'atm-elsa@nmsu.edu', None
    html = render_to_string('build/email/validate_release.html',
                            dict(ctx, logo_cid=EMAIL_LOGO_CID if logo else ''))

    alternative = MIMEMultipart('alternative')
    alternative.attach(MIMEText(text, 'plain', 'utf-8'))
    alternative.attach(MIMEText(html, 'html', 'utf-8'))
    body = alternative
    if logo:
        body = MIMEMultipart('related')
        body.attach(alternative)
        body.attach(logo)
    mime = body
    if attachment:
        mime = MIMEMultipart('mixed')
        mime.attach(body)
        report = MIMEText(attachment[1], 'plain', 'utf-8')
        report.add_header('Content-Disposition', 'attachment', filename=attachment[0])
        mime.attach(report)

    mime['Subject'] = subject
    mime['From'] = FROM_ADDRESS
    mime['To'] = ', '.join(recipients)
    mime['Date'] = formatdate(localtime=True)
    mime['Message-ID'] = make_msgid(domain='nmsu.edu')
    return _Prebuilt(mime, subject=subject, body=text, from_email=FROM_ADDRESS, to=recipients,
                     attachments=[attachment] if attachment else None)
