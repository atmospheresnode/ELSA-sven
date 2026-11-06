# -*- coding: utf-8 -*-
"""Peer review campaigns: invite reviewers with no ELSA account and collect their answers.

Links are bearer tokens, so we only store the SHA-256. They expire and can be revoked.
"""
from __future__ import unicode_literals

import hashlib
import io
import logging
import secrets
from datetime import datetime, time, timedelta

from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from friends.mail import build_branded

from . import audit, comms, roles
from .actions import ActionError, _require, _storable
from .models import Case, CaseNote, ReviewCampaign, ReviewerInvite


logger = logging.getLogger(__name__)

# Same questions as the public peer review form (review/forms.py).
QUESTIONS = (
    ('question1', 'Does the data set/bundle provide clear and concise documentation adequate for its usage?'),
    ('question2', 'Are you able to manipulate and/or plot the data, interpret columns/rows into tables, '
                  'and understand the context and relationships of the data products?'),
    ('question3', 'Are there any concerns about the creation/generation, calibration, or general '
                  'usability of the data?'),
    ('question4', 'Were there any issues with the data access website, related references, or any other '
                  'accessibility concerns?'),
    ('question5', 'Do you have any further comments to the PDS Atmospheres Node about the data?'),
)
ANSWER_LIMIT = 10000
DEFAULT_LIFETIME = timedelta(days=60)


def _hash(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def _expiry(campaign):
    if campaign.deadline:
        end = datetime.combine(campaign.deadline + timedelta(days=14), time(23, 59))
        return timezone.make_aware(end) if timezone.is_naive(end) else end
    return timezone.now() + DEFAULT_LIFETIME


# --- Staff side ---

def create(request, title, data_set, description, deadline):
    _require(request, roles.REVIEW_BUNDLES)
    title = ' '.join((title or '').split())[:200]
    data_set = ' '.join((data_set or '').split())[:255]
    if not title or not data_set:
        raise ActionError('Give the review a title and name the data set.')
    _storable(title, data_set, description)
    with transaction.atomic():
        case = Case.objects.create(
            kind=Case.PEER_REVIEW, title='Peer review campaign: {0}'.format(title),
            body=(description or '').strip(), assignee=request.user, status=Case.IN_PROGRESS,
            requester_name=request.user.get_full_name() or request.user.username,
            data={'reviewed_data_set': data_set, 'campaign': True})
        campaign = ReviewCampaign.objects.create(
            title=title, data_set=data_set, description=(description or '').strip(),
            deadline=deadline, case=case, created_by=request.user)
        audit.record(request, 'campaign.create', case, changes={'campaign': campaign.pk})
    return campaign


def _send_invite(request, invite):
    token = secrets.token_urlsafe(32)
    invite.token_hash = _hash(token)
    invite.expires_at = _expiry(invite.campaign)
    link = comms.link_for(request, reverse('staff_public:review_invite', args=[token]))
    campaign = invite.campaign
    lead = ('Hello {0}, the PDS Atmospheres Node is asking you to peer review the data set '
            '"{1}". Your review helps decide whether it is ready for the PDS archive.'.format(
                invite.name, campaign.data_set))
    if campaign.deadline:
        lead += ' We would be grateful for your review by {0:%B %d, %Y}.'.format(campaign.deadline)
    build_branded([invite.email], 'Invitation to peer review "{0}"'.format(campaign.data_set),
                  'staff/email/to_user', {
                      'headline': 'Peer review invitation',
                      'lead': lead,
                      'text': campaign.description,
                      'signature': '{0}\nPDS Atmospheres Node'.format(
                          request.user.get_full_name() or request.user.username),
                      'link': link,
                      'button': 'Open the review form',
                      'email_reply_note': 'This link is personal to you. No ELSA account is needed.',
                      'reference': '', 'email_reply': False,
                  }).send()
    invite.sent_at = timezone.now()
    invite.save()


def invite(request, campaign, name, email):
    _require(request, roles.REVIEW_BUNDLES)
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email
    name = ' '.join((name or '').split())[:150]
    email = (email or '').strip().lower()[:254]
    try:
        validate_email(email)
    except ValidationError:
        email = ''
    if not name or not email:
        raise ActionError('Give the reviewer a name and a valid email address.')
    _storable(name)
    if campaign.status != ReviewCampaign.OPEN:
        raise ActionError('This review is closed.')
    if campaign.invites.filter(email=email).exclude(status=ReviewerInvite.REVOKED).exists():
        raise ActionError('{0} is already invited.'.format(email))
    item = ReviewerInvite(campaign=campaign, name=name, email=email)
    try:
        _send_invite(request, item)
    except Exception:
        logger.exception('invite to %s failed', email)
        raise ActionError('The invitation could not be sent. Nothing was saved; try again.')
    audit.record(request, 'campaign.invite', campaign.case, changes={'email': email})
    return item


def resend(request, item):
    """A fresh link; the old one stops working."""
    _require(request, roles.REVIEW_BUNDLES)
    if item.status in (ReviewerInvite.SUBMITTED, ReviewerInvite.REVOKED):
        raise ActionError('This reviewer has already finished or was removed.')
    try:
        _send_invite(request, item)
    except Exception:
        logger.exception('resend to %s failed', item.email)
        raise ActionError('The invitation could not be sent.')
    audit.record(request, 'campaign.resend', item.campaign.case, changes={'email': item.email})


def revoke(request, item):
    _require(request, roles.REVIEW_BUNDLES)
    item.status = ReviewerInvite.REVOKED
    item.save(update_fields=['status'])
    audit.record(request, 'campaign.revoke', item.campaign.case, changes={'email': item.email})


def close(request, campaign):
    _require(request, roles.REVIEW_BUNDLES)
    campaign.status = ReviewCampaign.CLOSED
    campaign.save(update_fields=['status'])
    audit.record(request, 'campaign.close', campaign.case)


# --- Reviewer side ---

def find(token):
    """The invite a link belongs to, if it can still be used."""
    if not token or len(token) > 100:
        return None
    item = (ReviewerInvite.objects.select_related('campaign', 'campaign__case')
            .filter(token_hash=_hash(token)).first())
    if item is None or item.status == ReviewerInvite.REVOKED or item.expires_at <= timezone.now():
        return None
    if item.campaign.status != ReviewCampaign.OPEN and item.status != ReviewerInvite.SUBMITTED:
        return None
    return item


def clean_answers(data):
    from .dbtext import narrow
    return {key: narrow((data.get(key) or '').strip()[:ANSWER_LIMIT]) for key, _ in QUESTIONS}


def save(item, data, submit):
    """Keeps a draft, or submits.  A submitted review cannot be changed."""
    if item.status == ReviewerInvite.SUBMITTED:
        return False
    item.answers = clean_answers(data)
    now = timezone.now()
    if item.opened_at is None:
        item.opened_at = now
    if submit:
        item.status = ReviewerInvite.SUBMITTED
        item.submitted_at = now
    elif item.status == ReviewerInvite.INVITED:
        item.status = ReviewerInvite.OPENED
    item.save()
    if submit and item.campaign.case is not None:
        case = item.campaign.case
        text = '\n\n'.join('{0}\n{1}'.format(label, item.answers[key] or '(no answer)')
                           for key, label in QUESTIONS)
        CaseNote.objects.create(case=case, kind=CaseNote.MESSAGE,
                                body='Review from {0} <{1}>:\n\n{2}'.format(item.name, item.email, text))
        case.last_user_message_at = now
        case.updated_at = now
        case.save(update_fields=['last_user_message_at', 'updated_at'])
        comms.notify_staff_of_message(case, case.notes.order_by('-pk').first())
    return True


def report_pdf(campaign):
    """The campaign's reviews and liens as one PDF, for the review record."""
    from xhtml2pdf import pisa
    html = render_to_string('staff/campaign_report.html', {
        'campaign': campaign,
        'questions': QUESTIONS,
        'reviews': [{'name': r.name, 'email': r.email, 'submitted_at': r.submitted_at,
                     'answers': [(label, (r.answers or {}).get(key, '')) for key, label in QUESTIONS]}
                    for r in campaign.invites.filter(status=ReviewerInvite.SUBMITTED)],
        'liens': campaign.case.liens.all() if campaign.case else [],
        'generated': timezone.now(),
    })
    buffer = io.BytesIO()
    pisa.CreatePDF(src=html, dest=buffer)
    return buffer.getvalue()
