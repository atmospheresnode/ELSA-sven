# -*- coding: utf-8 -*-
"""What needs a staff member's attention, for the queue filter and the bell's Admin Console tab.

A request counts if it's active, not spam, unassigned or assigned to you, and you haven't
opened it since the requester last wrote.
"""
from __future__ import unicode_literals

from django.db.models import F, OuterRef, Q, Subquery

from . import roles
from .models import Case, CaseRead


def with_my_seen(cases, user):
    seen = CaseRead.objects.filter(case=OuterRef('pk'), user=user, role=CaseRead.STAFF).values('seen_at')[:1]
    return cases.annotate(my_seen_at=Subquery(seen))


def for_attention(user, cases=None):
    cases = with_my_seen(cases if cases is not None else Case.objects.all(), user)
    # Read-only roles can't act on requests, so nothing is waiting on them. They still see new versions.
    if not roles.has(user, roles.WORK_CASES):
        cases = cases.filter(kind=Case.UPDATE)
    return (cases.filter(status__in=Case.ACTIVE, is_spam=False)
            .filter(Q(assignee__isnull=True) | Q(assignee=user))
            .filter(Q(my_seen_at__isnull=True)
                    | Q(last_user_message_at__isnull=False, last_user_message_at__gt=F('my_seen_at'))))


def counts(user):
    """Numbers only since the navbar is on every page."""
    pending = for_attention(user)
    unopened = pending.filter(my_seen_at__isnull=True).count()
    total = pending.count()
    return {'total': total, 'unopened': unopened, 'answered': total - unopened,
            'mine': Case.objects.filter(status__in=Case.ACTIVE, is_spam=False, assignee=user).count(),
            # Of those, new versions of outside services (staff.watchers).
            'updates': pending.filter(kind=Case.UPDATE).count()}
