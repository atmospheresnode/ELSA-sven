# -*- coding: utf-8 -*-
"""Archive pipeline: where each submitted bundle is, how long it's been there, what's holding it."""
from __future__ import unicode_literals

from collections import OrderedDict
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from .models import Case, ConsoleSetting, Lien, Release


COLUMNS = OrderedDict([
    ('submitted', 'Submitted'),
    ('in_review', 'In review'),
    ('changes', 'Changes requested'),
    ('accepted', 'Accepted'),
    ('delivered', 'Delivered'),
    ('released', 'Released (last 30 days)'),
])


def _column(case, release):
    if case.stage == Case.STAGE_RELEASED:
        return 'released'
    if case.stage == Case.STAGE_ACCEPTED:
        return 'delivered' if release is not None and release.delivered_at else 'accepted'
    if case.stage == Case.STAGE_CHANGES:
        return 'changes'
    if case.stage == Case.STAGE_UNDER_REVIEW:
        return 'in_review'
    return 'submitted'


def board(project=None, bundle_type='', reviewer=None):
    """{column: [card, ...]} plus counts, oldest first within a column."""
    now = timezone.now()
    stuck_days = ConsoleSetting.get('pipeline_stuck_days')
    cases = (Case.objects.filter(kind=Case.SUBMISSION, bundle__isnull=False, is_spam=False)
             .exclude(stage=Case.STAGE_NOT_ACCEPTED)
             .filter(Q(status__in=Case.ACTIVE) | Q(stage=Case.STAGE_RELEASED,
                                                    closed_at__gte=now - timedelta(days=30)))
             .select_related('bundle', 'bundle__user', 'assignee').prefetch_related('bundle__projects')
             .annotate(open_liens=Count('liens', filter=Q(liens__status=Lien.OPEN))))
    if project is not None:
        cases = cases.filter(bundle__projects=project)
    if bundle_type in ('Archive', 'External'):
        cases = cases.filter(bundle__bundle_type=bundle_type)
    if reviewer is not None:
        cases = cases.filter(assignee=reviewer)
    releases = {}
    for release in Release.objects.filter(case__in=cases).order_by('created_at'):
        releases[release.case_id] = release
    columns = OrderedDict((key, []) for key in COLUMNS)
    for case in cases:
        release = releases.get(case.pk)
        key = _column(case, release)
        since = case.stage_changed_at or case.updated_at
        if key == 'delivered' and release is not None:
            since = release.delivered_at
        if key == 'released' and release is not None and release.released_at:
            since = release.released_at
        days = (now - since).days if since else 0
        columns[key].append({
            'case': case, 'bundle': case.bundle, 'release': release, 'days': days,
            'stuck': key != 'released' and days >= stuck_days,
            'open_liens': case.open_liens,
            'projects': [p.name for p in case.bundle.projects.all()][:2],
        })
    for key in columns:
        columns[key].sort(key=lambda card: -card['days'])
    return [(key, COLUMNS[key], cards) for key, cards in columns.items()], stuck_days
