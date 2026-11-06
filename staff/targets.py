# -*- coding: utf-8 -*-
"""Response-time targets: first reply within N working days, set on the Settings page."""
from __future__ import unicode_literals

from datetime import timedelta

from django.utils import timezone

from .models import Case, ConsoleSetting


def add_working_days(moment, days):
    current = timezone.localtime(moment)
    added = 0
    while added < days:
        current += timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current


def first_reply_due(case):
    return add_working_days(case.created_at, ConsoleSetting.get('first_reply_days'))


def state(case, now=None):
    """'late', 'soon' (due within a day), 'ok', or None once answered or closed."""
    if case.first_response_at is not None or case.status not in Case.ACTIVE or case.is_spam:
        return None
    now = now or timezone.now()
    due = first_reply_due(case)
    if now >= due:
        return 'late'
    if due - now <= timedelta(days=1):
        return 'soon'
    return 'ok'
