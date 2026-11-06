# -*- coding: utf-8 -*-
"""Checks the outside things ELSA depends on and tells the team about new versions.

`manage.py staff_watch` runs daily and compares each source with what ELSA uses. A new
version opens one "Outside update" request. The rollout checklist is in staff.rollout.

Installing a new PDS validate is done by `manage.py validate_release`.

A source that can't be read for three days in a row opens its own request.
"""
from __future__ import unicode_literals

import logging
import re
from collections import OrderedDict
from datetime import date

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

logger = logging.getLogger(__name__)

USER_AGENT = 'ELSA update check (PDS Atmospheres Node; atm-elsa@nmsu.edu)'
TIMEOUT = 15
FAILURE_DAYS = 3

PDS_SCHEMA = 'https://pds.nasa.gov/pds4/pds/v1/PDS4_PDS_{0}.xsd'
AMA_SCHEMA = 'https://pds.nasa.gov/pds4/ama/v1/PDS4_AMA_{0}_{1}.xsd'
IM_PAGE = 'https://pds.nasa.gov/datastandards/dictionaries/index-{0}.shtml'
DJANGO_CYCLES = 'https://endoflife.date/api/django.json'
DJANGO_NOTES = 'https://docs.djangoproject.com/en/stable/releases/{0}/'
GEMINI_MODEL = 'https://generativelanguage.googleapis.com/v1beta/models/{0}'


class WatchError(Exception):
    """A source could not be read, with a reason fit for the Updates page."""


class Event(object):
    """Something new, worth one request: ``key`` makes it happen once."""

    def __init__(self, key, title, body, data=None):
        self.key, self.title, self.body, self.data = key, title, body, data or {}


class Result(object):
    def __init__(self, current='', latest='', url='', note='', details=None, events=()):
        self.current, self.latest, self.url, self.note = current, latest, url, note
        self.details = details or {}
        self.events = list(events)


# --- Talking to the outside ---

def _session():
    import requests
    session = requests.Session()
    session.headers['User-Agent'] = USER_AGENT
    return session


def _exists(session, url):
    """True if the file is published, False if not, WatchError if the site can't say."""
    import requests
    try:
        response = session.head(url, timeout=TIMEOUT, allow_redirects=True)
        if response.status_code == 405:
            response = session.get(url, timeout=TIMEOUT, stream=True)
            response.close()
    except requests.RequestException as error:
        raise WatchError('{0} could not be reached ({1}).'.format(url.split('/')[2], type(error).__name__))
    if response.status_code == 200:
        return True
    if response.status_code in (403, 404, 410):
        return False
    raise WatchError('{0} answered {1}.'.format(url.split('/')[2], response.status_code))


def _json(session, url, **kwargs):
    import requests
    try:
        response = session.get(url, timeout=TIMEOUT, **kwargs)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as error:
        raise WatchError('{0} could not be read ({1}).'.format(url.split('/')[2], type(error).__name__))


# --- PDS4 version codes: 1O00 is 1.24.0.0 (a component of 10 or more is a letter) ---

DIGITS = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'


def im_parts(code):
    return tuple(DIGITS.index(ch) for ch in code.upper())


def im_code(parts):
    return ''.join(DIGITS[n] for n in parts)


def im_dotted(code):
    return '.'.join(str(n) for n in im_parts(code))


def ldd_dotted(code):
    return '.'.join(code)


def offered_im():
    """The newest information model ELSA offers in its version list."""
    from build.forms import VERSION_CHOICES
    return max((value for value, _label in VERSION_CHOICES), key=im_parts)


def pinned_ama():
    """(IM code, dictionary code) the External templates use, e.g. ('1O00', '1300')."""
    import os
    path = os.path.join(settings.BASE_DIR, 'templates', 'pds4_labels', 'base_templates', 'Template_PE.xml')
    with open(path, encoding='utf-8') as handle:
        match = re.search(r'PDS4_AMA_([0-9A-Z]{4})_([0-9]{4})', handle.read())
    if not match:
        raise WatchError('The External template names no AMA dictionary.')
    return match.group(1), match.group(2)


def newer_ims(session, code, ahead=3):
    """IM versions published after ``code``, oldest first."""
    major, minor = im_parts(code)[:2]
    found = []
    for step in range(1, ahead + 1):
        candidate = im_code((major, minor + step, 0, 0))
        if _exists(session, PDS_SCHEMA.format(candidate)):
            found.append(candidate)
    return found


# --- The sources ---

class Source(object):
    key = ''
    label = ''
    summary = ''

    def check(self, session):   # pragma: no cover - each source defines it
        raise NotImplementedError


class ValidateSource(Source):
    key = 'validate'
    label = 'PDS validate'
    summary = 'The tool that checks bundles before they are submitted.'

    def check(self, session):
        from build import validate_release
        try:
            latest = validate_release.latest_release()
        except Exception as error:
            raise WatchError('GitHub could not be read ({0}).'.format(type(error).__name__))
        current = validate_release.in_use_release() or ''
        result = Result(current=current, latest=latest['version'], url=latest.get('notes_url', ''))
        if current and validate_release.version_key(latest['version']) > validate_release.version_key(current):
            result.events.append(Event(
                latest['version'], 'PDS validate {0} is out'.format(latest['version']),
                'PDS validate {0} was released (ELSA uses {1}). "manage.py validate_release check" installs it '
                'and compares it with {1} over every bundle; nothing switches until someone runs "switch".\n\n'
                'Release notes: {2}'.format(latest['version'], current, latest.get('notes_url', '')),
                data={'version': latest['version'], 'current': current}))
        elif not current:
            result.note = 'This server has no validate release configured.'
        return result


class InformationModelSource(Source):
    key = 'pds4_im'
    label = 'PDS4 information model'
    summary = 'The core PDS4 standard that Archive bundle labels follow.'

    def check(self, session):
        current = offered_im()
        newer = newer_ims(session, current)
        latest = newer[-1] if newer else current
        result = Result(current='{0} ({1})'.format(im_dotted(current), current),
                        latest='{0} ({1})'.format(im_dotted(latest), latest),
                        url=IM_PAGE.format(im_dotted(latest)), details={'code': latest})
        for code in newer:
            result.events.append(Event(
                code, 'PDS4 information model {0} ({1}) is published'.format(im_dotted(code), code),
                'PDS has published information model {0} ({1}); the newest ELSA offers is {2} ({3}).\n\n'
                'Which versions ELSA offers is the team\'s decision. It only affects Archive bundles: External '
                'bundles stay on the version the AMA dictionary is built for.\n\nDictionaries for this version: '
                '{4}'.format(im_dotted(code), code, im_dotted(current), current, IM_PAGE.format(im_dotted(code))),
                data={'version': code, 'current': current}))
        return result


class AmaSource(Source):
    key = 'ama_ldd'
    label = 'AMA dictionary'
    summary = 'The Atmospheric Modeling Annex dictionary that External bundle labels use.'

    def check(self, session):
        im, ldd = pinned_ama()
        ims = [im] + newer_ims(session, im)
        number = int(ldd)
        tried, found = set(), []
        for candidate_im in ims:
            for candidate_ldd in sorted({number, number + 10, number + 100, number + 200}):
                pair = (candidate_im, '{0:04d}'.format(candidate_ldd))
                if pair == (im, ldd) or pair in tried:
                    continue
                tried.add(pair)
                if _exists(session, AMA_SCHEMA.format(*pair)):
                    found.append(pair)
        newest = max([(im, ldd)] + found, key=lambda pair: (im_parts(pair[0]), pair[1]))
        result = Result(current='{0} for IM {1}'.format(ldd_dotted(ldd), im_dotted(im)),
                        latest='{0} for IM {1}'.format(ldd_dotted(newest[1]), im_dotted(newest[0])),
                        url=AMA_SCHEMA.format(*newest), details={'im': newest[0], 'ldd': newest[1]})
        for pair in found:
            result.events.append(Event(
                '{0}_{1}'.format(*pair),
                'AMA dictionary {0} for IM {1} is published'.format(ldd_dotted(pair[1]), im_dotted(pair[0])),
                'PDS has published the AMA dictionary {0} built for information model {1} (schema: {2}). '
                'External bundles use {3} for IM {4}.\n\nThe AMA dictionary and the core information model move '
                'together: the four External templates name both.'.format(
                    ldd_dotted(pair[1]), im_dotted(pair[0]), AMA_SCHEMA.format(*pair), ldd_dotted(ldd),
                    im_dotted(im)),
                data={'version': '{0}_{1}'.format(*pair), 'current': '{0}_{1}'.format(im, ldd)}))
        return result


def _version_key(text):
    return tuple(int(part) for part in re.findall(r'\d+', text or ''))


class DjangoSource(Source):
    key = 'django'
    label = 'Django'
    summary = 'The web framework ELSA is built on: security fixes and how long each version is supported.'

    def check(self, session):
        import django
        current = django.get_version()
        cycle_name = '.'.join(current.split('.')[:2])
        cycles = _json(session, DJANGO_CYCLES)
        cycle = next((c for c in cycles if str(c.get('cycle')) == cycle_name), None)
        if cycle is None:
            raise WatchError('endoflife.date does not list Django {0}.'.format(cycle_name))
        today = date.today()

        def ended(value):
            if value in (True, False):
                return bool(value)
            try:
                return date.fromisoformat(str(value)) <= today
            except ValueError:
                return False

        supported = not ended(cycle.get('eol'))
        lts = [c for c in cycles if c.get('lts') and not ended(c.get('eol'))]
        newest_lts = max(lts, key=lambda c: _version_key(str(c.get('cycle')))) if lts else None
        details = {'cycle': cycle_name, 'eol': str(cycle.get('eol')), 'supported': supported,
                   'newest_lts': str(newest_lts.get('cycle')) if newest_lts else '',
                   'newest_lts_latest': str(newest_lts.get('latest')) if newest_lts else ''}
        result = Result(current=current, latest=str(cycle.get('latest') or current), details=details,
                        url=DJANGO_NOTES.format(cycle.get('latest') or current))
        if not supported:
            target = details['newest_lts'] or 'a supported version'
            result.latest = details['newest_lts_latest'] or result.latest
            result.url = 'https://docs.djangoproject.com/en/{0}/releases/'.format(details['newest_lts'] or 'stable')
            result.note = 'Django {0} no longer gets security fixes (since {1}).'.format(cycle_name, details['eol'])
            result.events.append(Event(
                'end-of-support-{0}'.format(cycle_name),
                'Django {0} is no longer supported; move to {1}'.format(cycle_name, target),
                'ELSA runs Django {0}. Django {1} stopped receiving security fixes on {2}. The supported long-term '
                'release is {3} ({4}). Moving needs the release notes read, the code checked against them, '
                'and the full test suites run.'.format(current, cycle_name, details['eol'], target,
                                                        details['newest_lts_latest']),
                data={'version': details['newest_lts_latest'] or target, 'current': current}))
        elif _version_key(str(cycle.get('latest'))) > _version_key(current):
            result.events.append(Event(
                str(cycle.get('latest')), 'Django {0} is out (ELSA runs {1})'.format(cycle.get('latest'), current),
                'A newer {0} release is out. Django releases within a series are security and bug fixes. '
                'Release notes: {1}'.format(cycle_name, result.url),
                data={'version': str(cycle.get('latest')), 'current': current}))
        return result


class AssistantModelsSource(Source):
    key = 'gemini'
    label = 'Assistant models'
    summary = 'The Gemini models the ELSA Assistant answers with: still offered by Google?'

    def models(self):
        from assistant import llm
        from assistant import views as assistant_views
        names = list(getattr(settings, 'GEMINI_MODELS', None) or llm.DEFAULT_MODELS)
        names += [m for m in assistant_views.TITLE_MODELS if m not in names]
        return names

    def check(self, session):
        from django.apps import apps
        key = getattr(settings, 'GEMINI_API_KEY', '')
        if not apps.is_installed('assistant') or not key:
            return Result(note='The Assistant is not set up on this server.')
        names = self.models()
        gone = []
        for name in names:
            import requests
            try:
                response = session.get(GEMINI_MODEL.format(name), timeout=TIMEOUT, headers={'x-goog-api-key': key})
            except requests.RequestException as error:
                raise WatchError('Google could not be reached ({0}).'.format(type(error).__name__))
            if response.status_code == 404:
                gone.append(name)
            elif response.status_code != 200:
                raise WatchError('Google answered {0}.'.format(response.status_code))
        result = Result(current=', '.join(names), latest='{0} of {1} available'.format(len(names) - len(gone), len(names)),
                        url='https://ai.google.dev/gemini-api/docs/deprecations', details={'gone': gone})
        for name in gone:
            result.events.append(Event(
                'retired-{0}'.format(name), 'Assistant model {0} is no longer offered'.format(name),
                'Google no longer offers {0}, which the ELSA Assistant tries in its list. The Assistant falls '
                'back to the next model, but the list should be updated (assistant/llm.py, or GEMINI_MODELS in '
                'settings.py).'.format(name), data={'version': name, 'current': ', '.join(names)}))
        return result


SOURCES = OrderedDict((source.key, source) for source in (
    ValidateSource(), InformationModelSource(), AmaSource(), DjangoSource(), AssistantModelsSource()))


# --- Running the checks ---

def run(keys=None):
    """Checks every source (or ``keys``).  Returns {key: 'ok' | 'failed'}."""
    from .models import WatchedSource
    session = _session()
    outcome = {}
    for key, source in SOURCES.items():
        if keys and key not in keys:
            continue
        row, _ = WatchedSource.objects.get_or_create(key=key)
        now = timezone.now()
        try:
            result = source.check(session)
        except WatchError as error:
            _failed(row, source, str(error), now)
            outcome[key] = 'failed'
            continue
        except Exception as error:      # a bug in one source must not stop the others
            logger.exception('update check %s failed', key)
            _failed(row, source, 'Unexpected error ({0}).'.format(type(error).__name__), now)
            outcome[key] = 'failed'
            continue
        WatchedSource.objects.filter(pk=row.pk).update(
            current=result.current[:100], latest=result.latest[:100], latest_url=result.url[:500],
            note=result.note[:500], details=result.details, checked_at=now, ok_at=now, error='', failures=0)
        for event in result.events:
            open_update(source, event)
        outcome[key] = 'ok'
    return outcome


def _failed(row, source, reason, now):
    from .models import Case, WatchedSource
    # Only count once a day, so pressing Check now five times isn't five days.
    new_day = row.checked_at is None or row.error == '' or timezone.localdate(row.checked_at) != timezone.localdate(now)
    failures = row.failures + 1 if new_day else max(row.failures, 1)
    WatchedSource.objects.filter(pk=row.pk).update(checked_at=now, error=reason[:500], failures=failures)
    open_case = row.failure_case_id and Case.objects.filter(pk=row.failure_case_id, status__in=Case.ACTIVE).exists()
    if failures >= FAILURE_DAYS and not open_case:
        case = open_update(source, Event(
            'unreadable-{0:%Y%m%d}'.format(timezone.localdate(now)),
            '{0}: could not check for {1} days'.format(source.label, failures),
            'ELSA has not been able to check {0} for {1} days. Last reason: {2}\n\nUntil it can, a new version '
            'would go unnoticed. The Updates page in the Admin Console shows each check.'.format(source.label, failures, reason),
            data={'unreadable': True}))
        if case is not None:
            WatchedSource.objects.filter(pk=row.pk).update(failure_case=case)


def open_update(source, event):
    """One request per (source, event), and tell the Admin Console. Returns the new case or None."""
    from . import hooks
    from .models import Case
    with transaction.atomic():
        if Case.objects.filter(kind=Case.UPDATE, data__source=source.key, data__event=event.key).exists():
            return None
        case = hooks.open_case(Case.UPDATE, event.title, body=event.body,
                               data=dict(event.data, source=source.key, event=event.key))
    if case is not None:
        tell_members(case)
    return case


def tell_members(case):
    """Email members who have the update email on. Shows on the Admin Console tab, not their own list."""
    from django.core.mail import EmailMessage
    from . import comms
    from .models import StaffPreference
    url = reverse('staff:case', args=[case.pk])
    to = []
    for person in comms.members():
        pref = StaffPreference.for_user(person)
        away = pref.away_until and pref.away_until >= timezone.localdate()
        if person.email and pref.when_update and not away:
            to.append(person.email)
    if not to:
        return
    try:
        EmailMessage(subject='Admin Console: ' + ' '.join(case.title.split())[:150],
                     body='{0}\n\nOpen it in the Admin Console:\n{1}\n\nYou get these because you are an Admin Console member. '
                          'Switch them off under My preferences.\n'.format(case.body, comms.absolute(url)),
                     from_email='atm-elsa@nmsu.edu', to=sorted(set(to))).send()
    except Exception:
        logger.exception('update notice for case %s failed', case.pk)


def launch():
    """Run the checks in their own process (Check now)."""
    import os
    import subprocess  # nosec B404: fixed argument list, no shell
    import sys
    command = [sys.executable, os.path.join(settings.BASE_DIR, 'manage.py'), 'staff_watch']
    environment = os.environ.copy()
    module = os.environ.get('DJANGO_SETTINGS_MODULE') or settings.SETTINGS_MODULE
    if module:
        environment['DJANGO_SETTINGS_MODULE'] = module
    try:
        subprocess.Popen(command, cwd=settings.BASE_DIR, env=environment,  # nosec B603: fixed argv, no shell
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        logger.exception('could not start the update check')
        return False
    return True
