# -*- coding: utf-8 -*-
"""Slow console lists, computed ahead of time by `manage.py staff_precompute`.

If a snapshot is missing or over a day old the page computes it on the spot.
"""
from __future__ import unicode_literals

from datetime import timedelta

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from friends import throttle

from .models import ConsoleSnapshot

MAX_AGE = timedelta(hours=26)
REFRESH_EVERY = 120          # seconds between refreshes by one person
REFRESH_EVERY_GLOBAL = 30    # and by anyone, since each walks every bundle


def _when(value):
    return value.isoformat() if value else None


def _parse(value):
    return parse_datetime(value) if value else None


def stalled_data():
    from . import outreach
    lists = outreach.stalled_lists()
    out = {'days': lists['days']}
    for name in ('quiet', 'ready', 'waiting'):
        out[name] = [{'bundle': row['bundle'].pk, 'last': _when(row['last']),
                      'case': row['case'].pk if row.get('case') else None} for row in lists[name]]
    return out


def insights_data():
    from . import outreach
    return [dict(row, bundles=[b.pk for b in row['bundles']]) for row in outreach.insights()]


COMPUTE = {'stalled': stalled_data, 'insights': insights_data}


def store(key):
    data = COMPUTE[key]()
    snapshot, _ = ConsoleSnapshot.objects.update_or_create(
        key=key, defaults={'data': {'v': data}, 'computed_at': timezone.now()})
    return snapshot


def load(key):
    """Returns (data, computed_at), computing it if the saved copy is missing or old."""
    snapshot = ConsoleSnapshot.objects.filter(key=key).first()
    if snapshot is None or snapshot.computed_at < timezone.now() - MAX_AGE:
        snapshot = store(key)
    return snapshot.data.get('v'), snapshot.computed_at


def refresh(request, key):
    """"Refresh now" button. False if asked again too soon."""
    if throttle.hit('snapshot-{0}-{1}'.format(key, request.user.pk), REFRESH_EVERY) > 1:
        return False
    if throttle.hit('snapshot-{0}'.format(key), REFRESH_EVERY_GLOBAL) > 1:
        return False
    store(key)
    return True


# --- Back to objects ---

def stalled_lists():
    """The shape outreach.stalled_lists() returns, from the snapshot."""
    from build.models import Bundle
    from . import outreach
    from .models import Case
    data, computed_at = load('stalled')
    ids = {row['bundle'] for name in ('quiet', 'ready', 'waiting') for row in data.get(name, [])}
    # Bundles deleted or submitted since the snapshot drop out.
    bundles = {b.pk: b for b in Bundle.objects.filter(pk__in=ids).select_related('user')}
    cases = {c.pk: c for c in Case.objects.filter(
        pk__in=[row['case'] for row in data.get('waiting', []) if row.get('case')])}
    lists = {'days': data.get('days'), 'computed_at': computed_at}
    for name in ('quiet', 'ready', 'waiting'):
        rows = []
        for row in data.get(name, []):
            bundle = bundles.get(row['bundle'])
            if bundle is None or (name != 'waiting' and bundle.submitted_at):
                continue
            item = {'bundle': bundle, 'last': _parse(row['last']), 'nudged': outreach.last_nudge(bundle)}
            if name == 'waiting':
                case = cases.get(row.get('case'))
                if case is None or case.stage != Case.STAGE_CHANGES or case.status not in Case.ACTIVE:
                    continue
                item['case'] = case
            rows.append(item)
        lists[name] = rows
    return lists


def insights():
    from build.models import Bundle
    data, computed_at = load('insights')
    ids = {pk for row in data for pk in row['bundles']}
    bundles = {b.pk: b for b in Bundle.objects.filter(pk__in=ids).select_related('user')}
    rows = []
    for row in data:
        rows.append(dict(row, bundles=[bundles[pk] for pk in row['bundles'] if pk in bundles]))
    return rows, computed_at
