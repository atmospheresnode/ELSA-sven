# -*- coding: utf-8 -*-
"""
Fixed-window rate limits for the account flows, counted in the database.

Each hit is one UPDATE that either advances a live window or starts a fresh
one, so concurrent requests cannot read the same count and each write back
count + 1.  That read-then-write is what the DB cache backend's incr() does,
and it is why these counters moved out of the cache.
"""
from __future__ import unicode_literals

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Case, F, Value, When
from django.utils import timezone

from .models import AuthThrottle


def _key(key):
    return key[:191]


def hit(key, window_seconds):
    """Counts one event against ``key`` and returns the count in the current window."""
    key = _key(key)
    now = timezone.now()
    fresh_expiry = now + timedelta(seconds=window_seconds)

    # count is assigned first.  MariaDB evaluates SET clauses left to right, so
    # both CASEs must read the old expires_at, which they do while it is last.
    advanced = AuthThrottle.objects.filter(key=key).update(
        count=Case(When(expires_at__lte=now, then=Value(1)),
                   default=F('count') + 1),
        expires_at=Case(When(expires_at__lte=now, then=Value(fresh_expiry)),
                        default=F('expires_at')),
    )

    if not advanced:
        try:
            # Savepoint, so losing the race to create the row does not poison
            # a surrounding transaction.
            with transaction.atomic():
                AuthThrottle.objects.create(key=key, count=1, expires_at=fresh_expiry)
            return 1
        except IntegrityError:
            AuthThrottle.objects.filter(key=key).update(count=F('count') + 1)

    return count(key)


def count(key):
    """Events against ``key`` in its live window, or 0."""
    value = (AuthThrottle.objects
             .filter(key=_key(key), expires_at__gt=timezone.now())
             .values_list('count', flat=True)
             .first())
    return value or 0


def reset(key):
    AuthThrottle.objects.filter(key=_key(key)).delete()


def prune():
    """Deletes counters whose window has closed.  Returns how many went."""
    deleted, _ = AuthThrottle.objects.filter(expires_at__lte=timezone.now()).delete()
    return deleted
