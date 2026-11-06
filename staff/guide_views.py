# -*- coding: utf-8 -*-
"""Today and Getting started pages. Read-only."""
from __future__ import unicode_literals

from collections import Counter

from django.db.models import Q
from django.shortcuts import render
from django.utils import timezone

from . import alerts, roles, targets
from .decorators import console_view
from .models import AuditEntry, Case, ConsoleSnapshot, Lien, Release
from .views import _console_context

TODAY_ROWS = 8


def _why(case, user):
    if case.my_seen_at is None and case.assignee_id is None:
        return 'New, nobody has taken it'
    if case.my_seen_at is None:
        return 'Assigned to you, not opened yet'
    return 'The user answered'


def _greeting(now):
    hour = timezone.localtime(now).hour
    return 'Good morning' if hour < 12 else 'Good afternoon' if hour < 18 else 'Good evening'


@console_view()
def today(request):
    user = request.user
    now = timezone.now()
    pending = list(alerts.for_attention(user, Case.objects.select_related('assignee', 'bundle'))
                   .order_by('created_at')[:50])
    late = [c for c in Case.objects.filter(status__in=Case.ACTIVE, is_spam=False, first_response_at__isnull=True)
            if targets.state(c) == 'late']

    # One row per request, however many reasons it has to be here.
    reviews, review_rows = [], {}

    def add_review(case, why):
        row = review_rows.get(case.pk)
        if row is None:
            review_rows[case.pk] = row = {'case': case, 'why': why, 'due': targets.state(case)}
            reviews.append(row)
        else:
            row['why'] = '{0} \u00b7 {1}'.format(row['why'], why)

    if roles.has(user, roles.REVIEW_BUNDLES):
        submissions = (Case.objects.filter(kind=Case.SUBMISSION, status__in=Case.ACTIVE, is_spam=False)
                       .exclude(requester=user).select_related('bundle').order_by('created_at'))
        for case in submissions:
            proposer = (case.data or {}).get('acceptance_proposed_by')
            if proposer and proposer != user.pk:
                # The person who proposed it is the assignee, so the second reviewer has to be someone else.
                add_review(case, 'Acceptance proposed; needs a second reviewer')
            elif (case.assignee_id in (None, user.pk) and not proposer
                  and case.stage in (Case.STAGE_SUBMITTED, Case.STAGE_UNDER_REVIEW)):
                add_review(case, 'Waiting for review' if case.stage == Case.STAGE_SUBMITTED else 'Under review')
        fixed = (Lien.objects.filter(status=Lien.OPEN, owner_fixed_at__isnull=False, case__status__in=Case.ACTIVE)
                 .filter(Q(case__assignee=user) | Q(case__assignee__isnull=True))
                 .exclude(case__requester=user).select_related('case').order_by('owner_fixed_at'))
        fixed_count = Counter(lien.case_id for lien in fixed)
        fixed_cases = {lien.case_id: lien.case for lien in fixed}
        for pk, count in fixed_count.items():
            add_review(fixed_cases[pk], 'The owner says {0}: check {1}'.format(
                '1 lien is fixed' if count == 1 else '{0} liens are fixed'.format(count),
                'it' if count == 1 else 'them'))
        for release in (Release.objects.filter(released_at__isnull=True).exclude(bundle__user=user)
                        .select_related('bundle').order_by('created_at')):
            step = next((label for _key, label, done in release.steps() if not done), '')
            reviews.append({'release': release, 'why': 'Release in progress{0}'.format(
                ': next, ' + step[0].lower() + step[1:] if step else '')})

    # Don't repeat bundles from the list above under Requests.
    needs_you = []
    for case in pending:
        why = _why(case, user)
        if case.pk in review_rows:
            if why == 'The user answered':
                add_review(case, why)
            continue
        needs_you.append({'case': case, 'why': why, 'due': targets.state(case)})

    stalled = ConsoleSnapshot.objects.filter(key='stalled').first()
    ready_count = len((stalled.data.get('v') or {}).get('ready', [])) if stalled else None
    first_visit = not AuditEntry.objects.filter(actor=user).exclude(
        action__in=('console.enter', 'console.exit')).exists()

    return render(request, 'staff/today.html', _console_context(
        request, section='today', greeting=_greeting(now), needs_you=needs_you[:TODAY_ROWS],
        needs_you_more=max(0, len(needs_you) - TODAY_ROWS), late_count=len(late),
        reviews=reviews[:TODAY_ROWS], reviews_more=max(0, len(reviews) - TODAY_ROWS),
        review_count=len(reviews), ready_count=ready_count, first_visit=first_visit,
        mine_open=Case.objects.filter(status__in=Case.ACTIVE, is_spam=False, assignee=user).count()))


@console_view()
def help_page(request):
    from .help_text import GLOSSARY
    return render(request, 'staff/help.html', _console_context(request, section='help',
                                                               page_title='Getting started', glossary=GLOSSARY))
