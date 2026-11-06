# -*- coding: utf-8 -*-
"""The part of the Admin Console that other apps call.

These work with or without the staff app installed, and a console problem shouldn't break
the user's request. Models are imported inside the functions for the same reason.
"""
from __future__ import unicode_literals

import logging

from django.apps import apps


logger = logging.getLogger(__name__)


BODY_LIMIT = 50000


def installed():
    return apps.is_installed('staff')


def routed():
    """True if the app is installed and its URLs are included. Otherwise links would give a 500."""
    if not installed():
        return False
    try:
        from django.urls import reverse
        reverse('staff_public:requests')
        reverse('staff:home')
        return True
    except Exception:
        return False


def open_case(kind, title, requester=None, body='', bundle=None, data=None,
              requester_name='', requester_email=''):
    """Record a request for staff. Returns the Case, or None if it couldn't be saved.

    A resubmitted bundle reopens its open case so the reviewer keeps the history.
    """
    if not installed():
        return None
    if requester is not None and not requester.is_authenticated:
        requester = None
    # Titles end up in email subjects, so keep them to one line.
    title = ' '.join((title or '').split()) or 'Request'
    # Some forms that feed this have no length limit of their own.
    body = (body or '')[:BODY_LIMIT]
    # Replace characters the database can't store instead of losing the text.
    from .dbtext import narrow
    title, body = narrow(title), narrow(body)
    requester_name = narrow(requester_name)
    try:
        from django.db import transaction
        from django.utils import timezone
        from .models import Case, CaseNote

        with transaction.atomic():
            if kind == Case.SUBMISSION and bundle is not None:
                existing = (Case.objects.select_for_update()
                            .filter(kind=kind, bundle=bundle)
                            .exclude(stage=Case.STAGE_RELEASED)
                            .order_by('-created_at').first())
                if existing is not None:
                    # Back in front of a reviewer, whatever it was waiting on.
                    existing.status = Case.OPEN
                    existing.stage = Case.STAGE_SUBMITTED
                    existing.stage_changed_at = timezone.now()
                    existing.closed_at = None
                    existing.closed_by = None
                    existing.updated_at = timezone.now()
                    existing.last_user_message_at = timezone.now()
                    existing.data = dict(existing.data or {}, **(data or {}))
                    existing.save()
                    CaseNote.objects.create(case=existing, kind=CaseNote.MESSAGE,
                                            body=body or 'The bundle was resubmitted.')
                    case = existing
                else:
                    case = None
            else:
                case = None

            if case is None:
                case = Case.objects.create(
                    kind=kind,
                    title=title[:255],
                    body=body or '',
                    requester=requester,
                    requester_username=getattr(requester, 'username', '') or '',
                    requester_name=' '.join((requester_name or (requester.get_full_name() if requester else '')).split())[:255],
                    requester_email=(requester_email or getattr(requester, 'email', '') or '')[:254],
                    bundle=bundle,
                    data=data or {},
                    stage=Case.STAGE_SUBMITTED if kind == Case.SUBMISSION else '',
                    stage_changed_at=timezone.now() if kind == Case.SUBMISSION else None,
                )
                # A sender staff marked as spam goes straight to the spam pile.
                if kind == Case.CONTACT and is_blocked_sender(case.requester_email):
                    case.is_spam = True
                    case.status = Case.DECLINED
                    case.closed_at = timezone.now()
                    case.save()

    except Exception:
        logger.exception('could not record a %s case', kind)
        return None
    if kind == 'submission' and bundle is not None:
        snapshot_submission(bundle)
    return case


def forget_requester(user):
    """Remove the name and email from a closed account's cases."""
    if not installed() or user is None:
        return
    try:
        from django.utils import timezone
        from .models import Case, CaseNote
        open_cases = list(Case.objects.filter(requester=user, status__in=Case.ACTIVE))
        Case.objects.filter(requester=user).update(requester_name='', requester_email='')
        # Nobody is left to answer: close them, and say why on each.
        for case in open_cases:
            Case.objects.filter(pk=case.pk).update(status=Case.RESOLVED, closed_at=timezone.now(),
                                                   updated_at=timezone.now())
            CaseNote.objects.create(case=case, kind=CaseNote.EVENT,
                                    body='Closed because the person who asked closed their ELSA account.')
    except Exception:
        logger.exception('could not scrub cases for user %s', getattr(user, 'pk', None))


def case_url(request, case):
    """Absolute console link for a staff email, or '' if there is no case."""
    if case is None:
        return ''
    try:
        from django.urls import reverse
        return request.build_absolute_uri(reverse('staff:case', args=[case.pk]))
    except Exception:
        return ''


def note_passkey_sign_in(request, user):
    """Lets a passkey sign-in count as the tap that opens admin mode."""
    if installed():
        from . import elevation
        elevation.note_passkey(request, user)


def passkey_sign_in_failed(request, user):
    """Failed passkey sign-in on ``user`` (or None). Bursts on staff accounts alert the developers."""
    if installed() and user is not None:
        from . import security_alerts
        security_alerts.failed_passkey(request, user)


def after_sign_in(user, default):
    """Where a fresh sign-in lands: the chooser for console members."""
    if not routed():
        return default
    try:
        from django.urls import reverse
        from . import roles
        if roles.is_member(user):
            return reverse('staff:choose')
    except Exception:
        logger.exception('could not resolve the console chooser')
    return default


def console_link(user):
    """The navbar link for console members, or ''."""
    if not routed() or user is None or not user.is_authenticated:
        return ''
    try:
        from django.urls import reverse
        from . import roles
        return reverse('staff:today') if roles.is_member(user) else ''
    except Exception:
        return ''


def open_agency_request(user):
    """The user's open agency case, so their account page can say so."""
    if not routed() or user is None or not user.is_authenticated:
        return None
    try:
        from .models import Case
        return (Case.objects.filter(kind=Case.AGENCY, requester=user, status__in=Case.ACTIVE)
                .order_by('-created_at').first())
    except Exception:
        return None


def review_state(bundle):
    """Latest submission case for a bundle: its stage and the last staff reply. None if there isn't one."""
    if not routed() or bundle is None:
        return None
    try:
        from .models import Case, CaseNote
        case = Case.objects.filter(kind=Case.SUBMISSION, bundle=bundle).order_by('-created_at').first()
        if case is None:
            return None
        from django.urls import reverse
        from .models import Lien
        reply = case.notes.filter(kind=CaseNote.REPLY).order_by('-created_at').first()
        return {'stage': case.stage, 'stage_label': case.get_stage_display(),
                'reply': reply.body if reply else '', 'replied_at': reply.created_at if reply else None,
                'liens': list(case.liens.filter(status=Lien.OPEN).order_by('created_at')),
                'doi': (case.releases.filter(released_at__isnull=False, doi_status='registered')
                        .order_by('-released_at').values_list('doi', flat=True).first() or ''),
                'url': reverse('staff_public:request', args=[case.pk]) if case.requester_id else ''}
    except Exception:
        return None


def view_as(request):
    """Staff "view as user"; see staff/view_as.py.  None when not in use."""
    if not routed():
        return None
    try:
        from . import view_as as module
        return module.apply(request)
    except Exception:
        logger.exception('view-as check failed')
        return None


def announcements_for(request):
    """Live announcements this user hasn't dismissed."""
    if not routed():
        return []
    try:
        from django.utils import timezone
        from .models import Announcement
        now = timezone.now()
        items = Announcement.objects.filter(starts_at__lte=now, ends_at__gt=now)
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            items = items.exclude(dismissals__user=user)
        return list(items[:3])
    except Exception:
        logger.exception('could not read announcements')
        return []


def unread_replies(user):
    """How many of a user's requests have a team reply they have not read."""
    if not routed() or user is None or not user.is_authenticated:
        return 0
    try:
        from . import comms
        return comms.unread_count_for(user)
    except Exception:
        return 0


def snapshot_submission(bundle):
    """Save the bundle's file list at submission, for "changed since last submission"."""
    if not installed() or bundle is None:
        return None
    try:
        from . import review
        return review.take_snapshot(bundle)
    except Exception:
        logger.exception('could not snapshot bundle %s', getattr(bundle, 'pk', None))
        return None


def is_blocked_sender(address):
    if not installed() or not address:
        return False
    try:
        from .models import BlockedSender
        return BlockedSender.objects.filter(email=address.strip().lower()).exists()
    except Exception:
        return False


def owner_liens(bundle):
    """Open liens on a bundle's review, for its owner's bundle page."""
    if not installed() or bundle is None:
        return []
    try:
        from .models import Case, Lien
        return list(Lien.objects.filter(case__bundle=bundle, case__kind=Case.SUBMISSION,
                                        status=Lien.OPEN).order_by('created_at'))
    except Exception:
        return []


def recent_requests(user, limit=3):
    """Requests section on the account page: latest few with unread markers. None without the console."""
    if not routed() or user is None or not user.is_authenticated:
        return None
    try:
        from django.urls import reverse
        from . import comms
        from .models import Case
        cases = Case.objects.filter(requester=user, is_spam=False).order_by('-updated_at')
        items = []
        for case in comms.with_requester_unread(cases, user)[:limit]:
            items.append({'title': case.title, 'status': case.user_status_label(),
                          'badge': case.user_status_badge(), 'stage': case.get_stage_display() if case.stage else '',
                          'updated_at': case.updated_at, 'unread': case.requester_unread,
                          'url': reverse('staff_public:request', args=[case.pk])})
        from .models import UserNotificationPreference
        return {'items': items, 'total': cases.count(), 'unread': comms.unread_count_for(user),
                'url': reverse('staff_public:requests'), 'new_url': reverse('staff_public:new_request'),
                'email_url': reverse('staff_public:notification_email'),
                'wants_email': UserNotificationPreference.wants_email(user),
                'news_url': reverse('staff_public:notification_news'),
                'wants_news': UserNotificationPreference.wants_news(user)}
    except Exception:
        logger.exception('could not list requests for user %s', getattr(user, 'pk', None))
        return None


def bell(user, limit=6):
    """Navbar bell: unread count and latest few notifications. None without the console."""
    if not routed() or user is None or not user.is_authenticated:
        return None
    try:
        from django.urls import reverse
        from .models import Notification
        items = list(Notification.objects.filter(user=user)[:limit])
        for item in items:
            item.link = reverse('staff_public:open_notification', args=[item.pk])
        return {'count': Notification.objects.filter(user=user, read_at__isnull=True).count(),
                'items': items, 'url': reverse('staff_public:notifications')}
    except Exception:
        logger.exception('could not read notifications for user %s', getattr(user, 'pk', None))
        return None


def admin_alerts(user):
    """Counts for the bell's Admin Console tab and the menu badge. None for non-members."""
    if not routed() or user is None or not user.is_authenticated:
        return None
    try:
        from django.urls import reverse
        from . import alerts, roles
        if not roles.is_member(user):
            return None
        data = alerts.counts(user)
        data['url'] = reverse('staff:home') + '?show=attention'
        data['queue_url'] = reverse('staff:home')
        data['updates_url'] = reverse('staff:updates')
        return data
    except Exception:
        logger.exception('could not count admin alerts for user %s', getattr(user, 'pk', None))
        return None
