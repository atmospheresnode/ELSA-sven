# -*- coding: utf-8 -*-
"""Conversations between the team and a requester.

The conversation is stored on the Case. A staff reply is saved first, then emailed with a
link back to ELSA. Users can answer in ELSA or by replying to the email.

Inbound email is optional (ELSA_INBOUND in settings.py). Without it email replies aren't read.
"""
from __future__ import unicode_literals

import logging
import re

from django.conf import settings
from django.contrib.auth.models import User
from django.db.models import BooleanField, F, OuterRef, Q, Subquery, Value, When
from django.db.models import Case as Case_
from django.core.mail import EmailMessage
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from friends.mail import build_branded

from . import roles
from .models import Case, CaseNote, CaseRead, Notification, StaffPreference, UserNotificationPreference


logger = logging.getLogger(__name__)

FROM_ADDRESS = 'atm-elsa@nmsu.edu'
PUBLIC_KINDS = (CaseNote.REPLY, CaseNote.MESSAGE)
MESSAGE_LIMIT = 10000

# Goes in every subject so an emailed answer finds its case, e.g. "[ELSA #12 ref:0a1b2c...]".
SUBJECT_TAG = re.compile(r'\[ELSA #(\d+) ref:([0-9a-f]{20})\]', re.I)
PLUS_TAG = re.compile(r'\+c(\d+)\.([0-9a-f]{20})@', re.I)


# --- Addresses and links ---

def inbound_address():
    """The mailbox ELSA reads replies from, or '' when this server has none."""
    return (getattr(settings, 'ELSA_INBOUND', None) or {}).get('address', '')


def reply_address(case):
    """Plus-addressed so the case is in the address itself, when inbound is on."""
    address = inbound_address()
    if not address or '@' not in address:
        return ''
    local, domain = address.split('@', 1)
    return '{0}+c{1}.{2}@{3}'.format(local, case.pk, case.reply_token, domain)


def reference(case):
    return '[ELSA #{0} ref:{1}]'.format(case.pk, case.reply_token)


def user_case_url(case):
    return reverse('staff_public:request', args=[case.pk])


def absolute(path):
    """Absolute link for email when there's no request (cron, inbound mail).

    ELSA_BASE_URL already ends in the mount point and reverse() adds it too, so remove one.
    """
    from django.urls import get_script_prefix
    base = (getattr(settings, 'ELSA_BASE_URL', '') or '').rstrip('/')
    if not base:
        base = 'http://localhost:7000' if settings.DEBUG else 'https://atmos.nmsu.edu/elsa'
    prefix = get_script_prefix()
    if prefix != '/' and path.startswith(prefix):
        path = '/' + path[len(prefix):]
    return base + path


def link_for(request, path):
    return request.build_absolute_uri(path) if request is not None else absolute(path)


# --- Reading the conversation ---

def public_notes(case):
    """What the requester can see: replies and their own messages, no internal notes."""
    return case.notes.filter(kind__in=PUBLIC_KINDS).select_related('author')


def mark_seen(case, user, role=CaseRead.STAFF):
    """``role`` is who's reading: staff in the console, the user on their own pages."""
    CaseRead.objects.update_or_create(case=case, user=user, role=role, defaults={'seen_at': timezone.now()})
    if role == CaseRead.USER:
        Notification.objects.filter(user=user, case=case, read_at__isnull=True).update(read_at=timezone.now())


def _seen_at(case, user, role):
    return (CaseRead.objects.filter(case=case, user=user, role=role)
            .values_list('seen_at', flat=True).first())


def unread_for_requester(case, user):
    """A staff reply the requester has not opened yet."""
    latest = (case.notes.filter(kind=CaseNote.REPLY).order_by('-created_at')
              .values_list('created_at', flat=True).first())
    if latest is None:
        return False
    seen = _seen_at(case, user, CaseRead.USER)
    return seen is None or latest > seen


def with_requester_unread(cases, user):
    """Add ``requester_unread`` to ``cases``. Done in one query since it's on every navbar."""
    latest_reply = (CaseNote.objects.filter(case=OuterRef('pk'), kind=CaseNote.REPLY)
                    .order_by('-created_at').values('created_at')[:1])
    seen = CaseRead.objects.filter(case=OuterRef('pk'), user=user, role=CaseRead.USER).values('seen_at')[:1]
    return cases.annotate(
        latest_reply_at=Subquery(latest_reply), user_seen_at=Subquery(seen),
    ).annotate(requester_unread=Case_(
        When(latest_reply_at__isnull=True, then=Value(False)),
        When(user_seen_at__isnull=True, then=Value(True)),
        When(latest_reply_at__gt=F('user_seen_at'), then=Value(True)),
        default=Value(False), output_field=BooleanField()))


def unread_count_for(user):
    """How many of a user's own requests have a reply they have not read."""
    return (with_requester_unread(Case.objects.filter(requester=user, is_spam=False), user)
            .filter(requester_unread=True).count())


def staff_has_unread(case, staff_user):
    """The requester wrote since this staff member last opened the case."""
    if case.last_user_message_at is None:
        return False
    seen = _seen_at(case, staff_user, CaseRead.STAFF)
    return seen is None or case.last_user_message_at > seen


def staff_unread_ids(staff_user, cases):
    """Ids in ``cases`` with a user message this staff member hasn't seen."""
    seen = dict(CaseRead.objects.filter(user=staff_user, role=CaseRead.STAFF, case__in=cases)
                .values_list('case_id', 'seen_at'))
    return {case.pk for case in cases
            if case.last_user_message_at and (case.pk not in seen or case.last_user_message_at > seen[case.pk])}


# --- Writing to the requester ---

def email_requester(case, subject, headline, lead, text='', staff_member=None, request=None):
    """Send an email to a requester, with the case reference and a link back to ELSA.

    Raises on failure so the caller can decide whether to record anything.
    """
    if not case.requester_email:
        raise ValueError('no address')
    has_account = case.requester_id is not None
    link = link_for(request, user_case_url(case)) if has_account else ''
    inbound = bool(reply_address(case))
    signature = ''
    if staff_member is not None:
        signature = '{0}\nPDS Atmospheres Node, ELSA team'.format(
            staff_member.get_full_name() or staff_member.username)

    message = build_branded([case.requester_email], '{0} {1}'.format(
        ' '.join(subject.split())[:180], reference(case)), 'staff/email/to_user', {
            'headline': headline,
            'lead': lead,
            'text': text,
            'signature': signature,
            'link': link,
            'button': 'View and reply in ELSA' if has_account else 'View in ELSA',
            'can_reply': has_account,
            'email_reply': inbound or not has_account,
            'email_reply_note': ('You can also reply to this email.' if inbound else
                                 'Please answer in ELSA with the button above. Replies to this email are not '
                                 'tracked with your request.' if has_account else ''),
            'answer_in_elsa_only': has_account and not inbound,
            'reference': reference(case),
        })
    if inbound:
        message.reply_to = [reply_address(case)]
    elif not has_account and staff_member is not None and staff_member.email:
        # No ELSA page and no inbound mailbox: email is the only way back.
        message.reply_to = [staff_member.email]
    else:
        message.reply_to = [FROM_ADDRESS]
    message.send()


def tell_requester(case, kind, title, headline, lead, text='', staff_member=None, request=None, email=True):
    """Tell a requester something, by bell and by email unless they turned email off.

    People without an account always get the email. Raises if the email fails.
    """
    has_account = case.requester_id is not None and case.requester.is_active
    if email and case.requester_email and (not has_account or UserNotificationPreference.wants_email(case.requester)):
        email_requester(case, title, headline, lead, text=text, staff_member=staff_member, request=request)
    if has_account:
        Notification.objects.create(user=case.requester, kind=kind, title=title[:200],
                                    body=' '.join((text or lead or '').split())[:300],
                                    url=user_case_url(case), case=case)


def tell_user(user, kind, title, lead, staff_member=None, url='', always_email=False):
    """Notice about an account rather than a request (agency changed, passkey removed)."""
    if not user.is_active and not always_email:
        return
    Notification.objects.create(user=user, kind=kind, title=title[:200], body=lead[:300], url=url)
    if user.email and (always_email or UserNotificationPreference.wants_email(user)):
        signature = ''
        if staff_member is not None:
            signature = '{0}\nPDS Atmospheres Node, ELSA team'.format(
                staff_member.get_full_name() or staff_member.username)
        build_branded([user.email], ' '.join(title.split())[:200], 'staff/email/to_user', {
            'headline': title, 'lead': lead, 'text': '', 'signature': signature,
            'link': absolute(url) if url else '', 'button': 'Open ELSA', 'reference': '', 'email_reply': False,
        }).send()


def _wants_unassigned(person):
    """Staff who want email about unassigned requests and aren't away."""
    pref = StaffPreference.for_user(person)
    away = pref.away_until and pref.away_until >= timezone.localdate()
    return bool(person.email and pref.when_unassigned and not away)


def notify_staff_new_conversation(case):
    """User started a conversation from ELSA. Tell the team like an unassigned answer."""
    to = sorted({p.email for p in members() if _wants_unassigned(p)})
    if not to:
        return
    try:
        EmailMessage(
            subject='Admin Console: new message from {0}: {1}'.format(' '.join(case.requester_label().split())[:80],
                                                                   ' '.join(case.title.split())[:120]),
            body='{0} wrote:\n\n{1}\n\nOpen it:\n{2}\n'.format(
                case.requester_label(), case.body, absolute(reverse('staff:case', args=[case.pk]))),
            from_email=FROM_ADDRESS, to=to).send()
    except Exception:
        logger.exception('staff notice for new conversation %s failed', case.pk)


# --- Hearing from the requester ---

def record_user_message(case, body, via, sender_ok=True):
    """Save an answer from the requester and move a closed or waiting case back to Open.

    ``via`` is 'elsa' or 'email'. Email from a different address is kept but flagged.
    """
    from .dbtext import narrow
    body = narrow((body or '').strip()[:MESSAGE_LIMIT])
    if not body:
        return None
    if not sender_ok:
        body = ('[Sent by email from an address that is not the requester\'s. '
                'Check before acting on it.]\n\n' + body)
    now = timezone.now()
    with transaction.atomic():
        # Lock the row and only write these fields, so we don't undo a new assignment.
        list(Case.objects.select_for_update().filter(pk=case.pk).values_list('pk', flat=True))
        case.refresh_from_db()
        note = CaseNote.objects.create(case=case, kind=CaseNote.MESSAGE, body=body,
                                       author=case.requester if via == 'elsa' else None)
        if case.status in Case.CLOSED or case.status == Case.WAITING:
            case.status = Case.OPEN
            case.closed_at = None
            case.closed_by = None
        case.last_user_message_at = now
        case.updated_at = now
        case.save(update_fields=['status', 'closed_at', 'closed_by', 'last_user_message_at', 'updated_at'])
        CaseNote.objects.create(case=case, kind=CaseNote.EVENT,
                                body='The user answered {0}.'.format('in ELSA' if via == 'elsa' else 'by email'))
    notify_staff_of_message(case, note)
    return note


def members():
    return (User.objects.filter(is_active=True)
            .filter(Q(is_superuser=True) | Q(groups__name__in=roles.GROUP_NAMES)).distinct())


def notify_staff_of_message(case, note):
    """Tell the assignee about an answer. If unassigned, only staff who asked for those emails."""
    if case.assignee_id and case.assignee.is_active:
        to = sorted({p.email for p in [case.assignee]
                     if p.email and StaffPreference.for_user(p).when_user_replies})
    else:
        to = sorted({p.email for p in members() if _wants_unassigned(p)})
    if not to:
        return
    try:
        EmailMessage(
            subject='Admin Console: {0} answered #{1} {2}'.format(' '.join(case.requester_label().split())[:80],
                                                               case.pk, ' '.join(case.title.split())[:120]),
            body='{0} wrote:\n\n{1}\n\nOpen the request:\n{2}\n'.format(
                case.requester_label(), note.body, absolute(reverse('staff:case', args=[case.pk]))),
            from_email=FROM_ADDRESS, to=to).send()
    except Exception:
        logger.exception('staff notice for case %s failed', case.pk)


def notify_assignee(case, assigned_by):
    """Tells someone a case was handed to them by a colleague."""
    person = case.assignee
    if person is None or person.pk == assigned_by.pk or not person.email:
        return
    if not StaffPreference.for_user(person).when_assigned:
        return
    try:
        EmailMessage(
            subject='Admin Console: {0} gave you #{1} {2}'.format(assigned_by.username, case.pk,
                                                               ' '.join(case.title.split())[:120]),
            body='{0} assigned this request to you.\n\nOpen it:\n{1}\n'.format(
                assigned_by.get_full_name() or assigned_by.username,
                absolute(reverse('staff:case', args=[case.pk]))),
            from_email=FROM_ADDRESS, to=[person.email]).send()
    except Exception:
        logger.exception('assignment notice for case %s failed', case.pk)


def notify_second_reviewers(case, proposer):
    """Two-person acceptance: tell everyone who could confirm it, by bell and email."""
    title = '{0} proposed accepting {1}; a second reviewer is needed'.format(
        proposer.username, ' '.join(case.title.split())[:120])
    url = reverse('staff:case', args=[case.pk])
    for person in members():
        if person.pk in (proposer.pk, case.requester_id) or not roles.has(person, roles.REVIEW_BUNDLES):
            continue
        Notification.objects.create(user=person, kind=Notification.REVIEW, title=title[:200], url=url)
        if person.email and StaffPreference.for_user(person).when_assigned:
            try:
                EmailMessage(subject='Admin Console: ' + title, body='Confirm it, or ask for changes:\n{0}\n'.format(absolute(url)),
                             from_email=FROM_ADDRESS, to=[person.email]).send()
            except Exception:
                logger.exception('second reviewer notice for case %s failed', case.pk)


# --- News and updates: the unsubscribe link in every such email ---

UNSUBSCRIBE_SALT = 'staff.news.unsubscribe'


def unsubscribe_token(user):
    """Signed so nobody can make one for another account. Doesn't expire, old emails have to work."""
    from django.core import signing
    return signing.dumps({'u': user.pk}, salt=UNSUBSCRIBE_SALT, compress=True)


def unsubscribe_url(user):
    return absolute(reverse('staff_public:unsubscribe', args=[unsubscribe_token(user)]))


def user_from_unsubscribe(token):
    from django.core import signing
    try:
        data = signing.loads(token, salt=UNSUBSCRIBE_SALT)
    except signing.BadSignature:
        return None
    return User.objects.filter(pk=data.get('u')).first() if isinstance(data, dict) else None
