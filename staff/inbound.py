# -*- coding: utf-8 -*-
"""Turns emailed answers from requesters into case messages.

A reply finds its case by the plus address in Reply-To (elsa-replies+c12.<token>@...) or
the "[ELSA #12 ref:<token>]" tag in the subject. The token has to match.

Refused: auto-replies, bounces, mailing lists, ELSA's own addresses and duplicates.
Replies from another address are kept but flagged. Attachments aren't stored.
"""
from __future__ import unicode_literals

import email
import email.policy
import hashlib
import logging
import re
from email.utils import getaddresses, parseaddr

from django.utils.html import strip_tags

from friends import throttle

from . import comms
from .models import Case


logger = logging.getLogger(__name__)

RAW_LIMIT = 5 * 1024 * 1024
SEEN_WINDOW = 60 * 60 * 24 * 60
# A person does not write thirty answers an hour; a mail loop does.
PER_CASE_PER_HOUR = 30

OWN_ADDRESSES = {'atm-elsa@nmsu.edu'}

# Where the quoted earlier message starts in common mail clients.
QUOTE_STARTS = (
    re.compile(r'^\s*On .{0,300}wrote:\s*$', re.I),
    re.compile(r'^\s*-{2,}\s*Original Message\s*-{2,}', re.I),
    re.compile(r'^\s*_{10,}\s*$'),
    re.compile(r'^\s*From:\s.+', re.I),
    re.compile(r'^\s*Sent from my ', re.I),
    re.compile(r'^\s*--\s*$'),
)


def _addresses(message, *headers):
    found = []
    for header in headers:
        found.extend(addr for _, addr in getaddresses(message.get_all(header, [])))
    return [a.lower() for a in found if a]


def _is_automatic(message, sender):
    auto = (message.get('Auto-Submitted') or 'no').strip().lower()
    precedence = (message.get('Precedence') or '').strip().lower()
    local = sender.split('@', 1)[0]
    return (auto != 'no'
            or precedence in ('bulk', 'junk', 'list', 'auto_reply')
            or message.get('X-Autoreply') or message.get('X-Autorespond')
            or message.get('List-Id')
            or local in ('mailer-daemon', 'postmaster', 'noreply', 'no-reply'))


def find_case(message):
    """The case a reply belongs to, or None."""
    for address in _addresses(message, 'To', 'Cc', 'Delivered-To', 'X-Original-To'):
        match = comms.PLUS_TAG.search(address + '@') or comms.PLUS_TAG.search(address)
        if match:
            case = Case.objects.filter(pk=int(match.group(1)), reply_token=match.group(2).lower()).first()
            if case:
                return case
    match = comms.SUBJECT_TAG.search(str(message.get('Subject') or ''))
    if match:
        return Case.objects.filter(pk=int(match.group(1)), reply_token=match.group(2).lower()).first()
    return None


def body_text(message):
    part = message.get_body(preferencelist=('plain', 'html'))
    if part is None:
        return ''
    try:
        text = part.get_content()
    except (LookupError, UnicodeDecodeError):
        payload = part.get_payload(decode=True) or b''
        text = payload.decode('utf-8', errors='replace')
    if part.get_content_type() == 'text/html':
        text = strip_tags(re.sub(r'(?i)<br\s*/?>|</p>', '\n', text))
    return text


def new_text(text):
    """The part the person just wrote: everything above the quoted history."""
    kept = []
    for line in text.replace('\r\n', '\n').split('\n'):
        if line.lstrip().startswith('>') or any(p.match(line) for p in QUOTE_STARTS):
            break
        kept.append(line)
    return '\n'.join(kept).strip()


def attachment_names(message):
    return [part.get_filename() for part in message.iter_attachments() if part.get_filename()]


def _without_plus(address):
    local, _, domain = address.partition('@')
    return '{0}@{1}'.format(local.split('+', 1)[0], domain)


def ingest(raw):
    """Save one raw email. Never raises, so one bad message doesn't stop the rest.

    Returns (outcome, case). Outcome is stored, no_case, automatic, duplicate, empty, own,
    too_large, malformed or flooding.
    """
    if len(raw) > RAW_LIMIT:
        return 'too_large', None
    try:
        return _ingest(raw)
    except Exception:
        logger.exception('could not read an inbound message')
        return 'malformed', None


def _ingest(raw):
    message = email.message_from_bytes(raw, policy=email.policy.default)
    sender = parseaddr(str(message.get('From') or ''))[1].lower()
    own = set(OWN_ADDRESSES)
    if comms.inbound_address():
        own.add(comms.inbound_address().lower())
    if not sender or '@' not in sender or _without_plus(sender) in own:
        return 'own', None
    if _is_automatic(message, sender):
        return 'automatic', None

    case = find_case(message)
    if case is None:
        return 'no_case', None

    # A message without an ID is recognised by its own bytes.
    message_id = str(message.get('Message-ID') or '').strip().encode('utf-8') or raw
    key = 'inbound-' + hashlib.sha256(message_id).hexdigest()[:40]
    if throttle.hit(key, SEEN_WINDOW) > 1:
        return 'duplicate', case
    if throttle.hit('inbound-case-{0}'.format(case.pk), 3600) > PER_CASE_PER_HOUR:
        return 'flooding', case

    text = new_text(body_text(message))
    names = attachment_names(message)
    if names:
        text += '\n\n[Attachments were not kept: {0}]'.format(', '.join(names))
    if not text.strip():
        return 'empty', case

    sender_ok = bool(case.requester_email) and sender == case.requester_email.strip().lower()
    comms.record_user_message(case, text, via='email', sender_ok=sender_ok)
    return 'stored', case
