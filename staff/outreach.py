# -*- coding: utf-8 -*-
"""Helping users get to the archive: quiet bundles, common validate findings, and drafts."""
from __future__ import unicode_literals

import os
from collections import Counter, defaultdict
from datetime import timedelta

from django.utils import timezone

from build.models import Bundle, ValidationRun

from . import audit, roles
from .actions import ActionError, _require, start_conversation
from .models import Case, ConsoleSetting

NUDGE_QUIET_DAYS = 14
NUDGES_PER_REQUEST = 50


def last_activity(bundle):
    """Latest sign of work on a bundle: its record, a validate run or a file change."""
    times = [t for t in (bundle.updated_at, bundle.created_at) if t]
    run = (ValidationRun.objects.filter(bundle=bundle).order_by('-requested_at')
           .values_list('requested_at', flat=True).first())
    if run:
        times.append(run)
    folder = bundle.directory()
    newest = 0
    try:
        for current, dirs, files in os.walk(folder):
            for name in files:
                try:
                    newest = max(newest, os.stat(os.path.join(current, name)).st_mtime)
                except OSError:
                    continue
    except OSError:
        pass
    if newest:
        times.append(timezone.datetime.fromtimestamp(newest, tz=timezone.utc))
    return max(times) if times else None


def stalled_lists():
    """Three lists of bundles that may need a word from the team."""
    from build import preflight
    days = ConsoleSetting.get('stalled_days')
    cutoff = timezone.now() - timedelta(days=days)
    quiet, ready = [], []
    for bundle in Bundle.objects.filter(submitted_at__isnull=True, user__is_active=True).select_related('user'):
        last = last_activity(bundle)
        met = preflight.met(bundle)
        row = {'bundle': bundle, 'last': last, 'nudged': last_nudge(bundle)}
        if met:
            ready.append(row)
        elif last and last < cutoff:
            quiet.append(row)
    waiting = []
    for case in (Case.objects.filter(kind=Case.SUBMISSION, stage=Case.STAGE_CHANGES, status__in=Case.ACTIVE,
                                     bundle__isnull=False)
                 .select_related('bundle', 'bundle__user')):
        since = case.last_user_message_at or case.stage_changed_at or case.updated_at
        if since and since < cutoff:
            waiting.append({'bundle': case.bundle, 'last': since, 'case': case, 'nudged': last_nudge(case.bundle)})
    for rows in (quiet, ready, waiting):
        rows.sort(key=lambda r: r['last'] or timezone.now())
    return {'days': days, 'quiet': quiet, 'ready': ready, 'waiting': waiting}


def last_nudge(bundle):
    case = (Case.objects.filter(kind=Case.MESSAGE, data__nudge_bundle=bundle.pk)
            .order_by('-created_at').first())
    return case.created_at if case else None


def fill(text, bundle):
    user = bundle.user
    return (text.replace('{name}', user.first_name or user.username).replace('{username}', user.username)
            .replace('{bundle}', bundle.name))


def nudge(request, bundles, subject, body):
    """Message each bundle's owner, at most every two weeks per bundle. Returns (sent, skipped)."""
    _require(request, roles.WORK_CASES)
    subject = ' '.join((subject or '').split())[:150]
    body = (body or '').strip()
    if not subject or not body:
        raise ActionError('Write a subject and a message.')
    sent, skipped = 0, []
    for bundle in list(bundles)[:NUDGES_PER_REQUEST]:
        previous = last_nudge(bundle)
        if previous and previous > timezone.now() - timedelta(days=NUDGE_QUIET_DAYS):
            skipped.append('{0} (nudged {1} days ago)'.format(bundle.name, (timezone.now() - previous).days))
            continue
        if bundle.user_id == request.user.pk or not bundle.user.is_active or not bundle.user.email:
            skipped.append('{0} (owner cannot be messaged)'.format(bundle.name))
            continue
        try:
            start_conversation(request, bundle.user, fill(subject, bundle), fill(body, bundle),
                               data={'nudge_bundle': bundle.pk})
            sent += 1
        except ActionError as error:
            skipped.append('{0} ({1})'.format(bundle.name, str(error).rstrip('.')))
    audit.record(request, 'outreach.nudge', changes={'sent': sent, 'skipped': len(skipped)})
    return sent, skipped


# --- Validation insights ---

def latest_runs():
    """The newest finished run of each bundle."""
    seen, runs = set(), []
    for run in (ValidationRun.objects.filter(status=ValidationRun.STATUS_DONE)
                .select_related('bundle', 'bundle__user').order_by('-requested_at')):
        if run.bundle_id in seen:
            continue
        seen.add(run.bundle_id)
        runs.append(run)
    return runs


def insights():
    """Most common problems across the node, and which bundles have them."""
    from build import validate_rules
    counts = Counter()
    titles = {}
    bundles = defaultdict(set)
    for run in latest_runs():
        for item in validate_rules.translate(run.findings or []).get('user', []):
            key = item.get('key') or item.get('title')
            counts[key] += 1
            titles[key] = (item.get('title') or key, item.get('detail') or '')
            bundles[key].add(run.bundle)
    rows = []
    for key, count in counts.most_common(25):
        title, detail = titles[key]
        affected = sorted(bundles[key], key=lambda b: b.name)
        rows.append({'title': title, 'detail': detail, 'count': count, 'bundles': affected[:12],
                     'more': max(0, len(affected) - 12)})
    return rows


def findings_draft(bundle, greeting_name=''):
    """Draft reply explaining the latest validate findings, for staff to edit."""
    from build import validate_rules
    run = (ValidationRun.objects.filter(bundle=bundle, status=ValidationRun.STATUS_DONE)
           .order_by('-requested_at').first())
    if run is None:
        return ''
    items = validate_rules.translate(run.findings or []).get('user', [])
    if not items:
        return ''
    lines = ['Hi {0},'.format(greeting_name or bundle.user.first_name or bundle.user.username), '',
             'The latest PDS validate check on "{0}" found {1} thing{2} to fix before it can be accepted:'.format(
                 bundle.name, len(items), '' if len(items) == 1 else 's'), '']
    for number, item in enumerate(items[:12], 1):
        lines.append('{0}. {1}'.format(number, item.get('title', '').strip()))
        if item.get('detail'):
            lines.append('   ' + ' '.join(item['detail'].split()))
        lines.append('')
    if len(items) > 12:
        lines.append('There are {0} more on the bundle page.'.format(len(items) - 12))
        lines.append('')
    lines.append('Each one is also listed on the bundle page in ELSA, with a link to where it is fixed.')
    return '\n'.join(lines)
