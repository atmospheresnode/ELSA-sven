# -*- coding: utf-8 -*-
"""All Admin Console changes go through here.

Each action checks permission, makes the change and writes the audit entry in one
transaction. Refusals raise ActionError with a message we can show to staff.
"""
from __future__ import unicode_literals

import logging
import re

from django.core.mail import EmailMessage
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from friends import devices
from friends.mail import build_branded
from friends.models import UserProfile, UserSession

from . import audit, comms, elevation, roles
from .models import Case, CaseNote, CaseRead, Lien, Notification


logger = logging.getLogger(__name__)

FROM_ADDRESS = 'atm-elsa@nmsu.edu'


class ActionError(Exception):
    pass


def _require(request, codename):
    if not roles.has(request.user, codename):
        raise ActionError('Your role does not allow this.')


def _require_confirmation(request):
    if not elevation.spend_confirmation(request):
        raise ActionError('Confirm with your passkey to do this.')


def _not_own(request, case=None, user=None):
    """Nobody decides their own request, account or bundle. A colleague has to do it."""
    owner_id = user.pk if user is not None else (case.requester_id if case is not None else None)
    if owner_id is not None and owner_id == request.user.pk:
        raise ActionError('This is your own request. A colleague has to decide it.')


def _storable(*texts):
    """Refuse text the database can't store, like emoji on utf8mb3 (see staff.dbtext)."""
    from . import dbtext
    bad = dbtext.unstorable(*texts)
    if bad:
        raise ActionError('ELSA cannot save emoji such as {0} yet. Please take them out and send it again.'.format(bad))


def _event(case, body, author=None):
    CaseNote.objects.create(case=case, kind=CaseNote.EVENT, body=body, author=author)


def _touch(case):
    case.updated_at = timezone.now()


# Only save the fields staff actions change, so we don't overwrite a newer answer or title.
WORK_FIELDS = ['status', 'stage', 'assignee', 'closed_at', 'closed_by', 'first_response_at', 'is_spam',
               'checklist', 'stage_changed_at', 'updated_at']


def _lock(case):
    """Re-read the case with a row lock. Call this first inside the transaction."""
    list(Case.objects.select_for_update().filter(pk=case.pk).values_list('pk', flat=True))
    case.refresh_from_db()


def _save(case):
    case.save(update_fields=WORK_FIELDS)


def _close(case, status, actor):
    case.status = status
    case.closed_at = timezone.now()
    case.closed_by = actor


def _reachable(case):
    """Whether anything we write can get to the person who asked."""
    if case.requester_id is not None and case.requester.is_active:
        return True
    return bool(case.requester_email)


def _first_response(case):
    if case.first_response_at is None:
        case.first_response_at = timezone.now()


def _signature(user):
    return '\n\n{0}\nPDS Atmospheres Node, ELSA team'.format(user.get_full_name() or user.username)


def email_account(user, subject, headline, lead, text='', staff_member=None):
    """A branded notice to an account that is not about any one case."""
    signature = ''
    if staff_member is not None:
        signature = '{0}\nPDS Atmospheres Node, ELSA team'.format(
            staff_member.get_full_name() or staff_member.username)
    build_branded([user.email], ' '.join(subject.split())[:200], 'staff/email/to_user', {
        'headline': headline, 'lead': lead, 'text': text, 'signature': signature,
        'link': '', 'reference': '', 'email_reply': False,
    }).send()


def _send(to, subject, body, staff_member):
    subject = ' '.join(subject.split())[:200]
    EmailMessage(subject=subject, body=body, from_email=FROM_ADDRESS, to=[to],
                 reply_to=[staff_member.email] if staff_member.email else [FROM_ADDRESS]).send()


# --- Cases ---

def claim(request, case):
    _require(request, roles.WORK_CASES)
    with transaction.atomic():
        _lock(case)
        before = case.assignee.username if case.assignee else ''
        case.assignee = request.user
        if case.status == Case.OPEN:
            case.status = Case.IN_PROGRESS
        _touch(case)
        _save(case)
        _event(case, '{0} took this request.'.format(request.user.username), request.user)
        audit.record(request, 'case.claim', case, changes={'assignee': [before, request.user.username]})


def unassign(request, case):
    _require(request, roles.WORK_CASES)
    with transaction.atomic():
        _lock(case)
        before = case.assignee.username if case.assignee else ''
        case.assignee = None
        _touch(case)
        _save(case)
        _event(case, '{0} unassigned this request.'.format(request.user.username), request.user)
        audit.record(request, 'case.unassign', case, changes={'assignee': [before, '']})


def add_note(request, case, body):
    _require(request, roles.WORK_CASES)
    body = (body or '').strip()
    if not body:
        raise ActionError('Write the note first.')
    _storable(body)
    with transaction.atomic():
        CaseNote.objects.create(case=case, kind=CaseNote.NOTE, body=body, author=request.user)
        _touch(case)
        case.save(update_fields=['updated_at'])
        audit.record(request, 'case.note', case)
    notify_mentions(request, case, body)


MENTION = re.compile(r'(?<![\w@])@([\w.+-]{2,150})')


def mentioned_staff(body):
    """Console members named with @username in a note."""
    from django.contrib.auth.models import User
    names = {m.group(1).rstrip('.') for m in MENTION.finditer(body or '')}
    if not names:
        return []
    return [u for u in User.objects.filter(username__in=names, is_active=True) if roles.is_member(u)]


def notify_mentions(request, case, body):
    """Notify colleagues named in an internal note, by bell and email unless they opted out."""
    from django.core.mail import EmailMessage
    from django.urls import reverse
    from .models import StaffPreference
    for person in mentioned_staff(body):
        if person.pk == request.user.pk:
            continue
        title = '{0} mentioned you on #{1} {2}'.format(request.user.username, case.pk, ' '.join(case.title.split())[:80])
        url = reverse('staff:case', args=[case.pk])
        Notification.objects.create(user=person, kind=Notification.MESSAGE, title=title[:200],
                                    body=' '.join(body.split())[:300], url=url, case=None)
        if person.email and StaffPreference.for_user(person).when_mentioned:
            try:
                EmailMessage(subject='Admin Console: ' + title, body='{0}\n\nOpen it:\n{1}\n'.format(
                    body, comms.absolute(url)), from_email=FROM_ADDRESS, to=[person.email]).send()
            except Exception:
                logger.exception('mention notice for case %s failed', case.pk)


def reply(request, case, body, waiting=False, then=None):
    """Email the requester and save the text on the case. Nothing is saved if the email fails."""
    _require(request, roles.WORK_CASES)
    then = then or ('waiting' if waiting else 'open')
    if then not in ('open', 'waiting', 'resolve'):
        raise ActionError('Unknown choice.')
    if then == 'resolve' and case.kind in (Case.AGENCY, Case.SUBMISSION):
        raise ActionError('Agency requests close with Approve or Decline, and bundles with their review stages.')
    body = (body or '').strip()
    if not body:
        raise ActionError('Write the reply first.')
    _storable(body)
    if not _reachable(case):
        raise ActionError('This request has no way to reach the person who sent it.')

    try:
        comms.tell_requester(case, Notification.REPLY, 'Re: {0}'.format(case.title), 'A reply from the ELSA team',
                             'About your request "{0}":'.format(case.title), text=body,
                             staff_member=request.user, request=request)
    except Exception:
        logger.exception('reply on case %s failed', case.pk)
        raise ActionError('The email could not be sent. Nothing was recorded; try again.')

    with transaction.atomic():
        _lock(case)
        CaseNote.objects.create(case=case, kind=CaseNote.REPLY, body=body, author=request.user)
        before = case.status
        if then == 'waiting' and case.is_active:
            case.status = Case.WAITING
        elif then == 'resolve' and case.is_active:
            # The reply is the user's news; no second "resolved" email.
            _close(case, Case.RESOLVED, request.user)
            _event(case, '{0} resolved this with their reply.'.format(request.user.username), request.user)
        _first_response(case)
        _touch(case)
        _save(case)
        audit.record(request, 'case.reply', case, changes={'to': case.requester_email})
        if case.status != before:
            audit.record(request, 'case.status', case, changes={'status': [before, case.status]})
    comms.mark_seen(case, request.user, CaseRead.STAFF)


# What the requester is told when a request ends or waits on them.
OUTCOME_WORDS = {
    Case.RESOLVED: ('Your request was resolved', 'Request resolved',
                    'The ELSA team marked your request "{title}" as resolved.'),
    Case.DECLINED: ('Your request was declined', 'Request declined',
                    'The ELSA team could not go ahead with your request "{title}".'),
    Case.WAITING: ('The ELSA team needs something from you', 'Your answer is needed',
                   'The ELSA team needs something from you to continue with "{title}".'),
}


def set_status(request, case, status, reason='', message=''):
    """Change a request's stage.

    ``message`` goes to the requester and is required to decline. ``reason`` stays internal.
    """
    _require(request, roles.WORK_CASES)
    if status not in dict(Case.STATUS_CHOICES):
        raise ActionError('Unknown status.')
    if case.kind == Case.AGENCY and status == Case.RESOLVED:
        raise ActionError('Use Approve to resolve an agency request, so the agency actually changes.')
    if case.kind == Case.SUBMISSION and status in Case.CLOSED:
        raise ActionError('Bundle submissions close through the review stages: Released, or Not accepted.')
    message = (message or '').strip()
    tell = _reachable(case) and not case.is_spam and status in OUTCOME_WORDS and status != case.status
    _storable(message, reason)
    if tell and status == Case.DECLINED and not message:
        raise ActionError('Tell them why it was declined; they receive this message.')
    if tell and status == Case.WAITING and not message:
        tell = False
    if tell:
        title, headline, lead = OUTCOME_WORDS[status]
        try:
            comms.tell_requester(case, Notification.OUTCOME, '{0}: {1}'.format(title, case.title), headline,
                                 lead.format(title=case.title), text=message, staff_member=request.user,
                                 request=request)
        except Exception:
            logger.exception('outcome notice on case %s failed', case.pk)
            raise ActionError('The email could not be sent. Nothing changed; try again.')
    with transaction.atomic():
        _lock(case)
        if message and tell:
            CaseNote.objects.create(case=case, kind=CaseNote.REPLY, body=message, author=request.user)
            _first_response(case)
        before = case.status
        if status in Case.CLOSED:
            _close(case, status, request.user)
        else:
            case.status = status
            case.closed_at = None
            case.closed_by = None
        _touch(case)
        _save(case)
        _event(case, '{0} set the status to {1}.{2}'.format(
            request.user.username, case.get_status_display(),
            ' Reason: {0}'.format(reason) if reason else ''), request.user)
        audit.record(request, 'case.status', case, reason=reason,
                     changes={'status': [before, status]})


# Where each review stage leaves the case in the queue.
STAGE_STATUS = {
    Case.STAGE_SUBMITTED: Case.OPEN,
    Case.STAGE_UNDER_REVIEW: Case.IN_PROGRESS,
    Case.STAGE_CHANGES: Case.WAITING,
    Case.STAGE_ACCEPTED: Case.IN_PROGRESS,
    Case.STAGE_RELEASED: Case.RESOLVED,
    Case.STAGE_NOT_ACCEPTED: Case.DECLINED,
}

STAGE_MESSAGES = {
    Case.STAGE_UNDER_REVIEW: 'The ELSA team has started reviewing your bundle "{name}".',
    Case.STAGE_CHANGES: 'The ELSA team reviewed your bundle "{name}" and is asking for changes '
                        'before it can be accepted. Make them in ELSA and submit the bundle again.',
    Case.STAGE_ACCEPTED: 'Your bundle "{name}" has been accepted for the PDS archive.',
    Case.STAGE_RELEASED: 'Your bundle "{name}" has been released in the PDS archive.',
    Case.STAGE_NOT_ACCEPTED: 'The ELSA team reviewed your bundle "{name}" and cannot accept it for the PDS archive.',
}


FINAL_STAGES = (Case.STAGE_ACCEPTED, Case.STAGE_RELEASED, Case.STAGE_NOT_ACCEPTED)


def set_stage(request, case, stage, message='', accept_anyway=False, move_back=False):
    """Moves a submitted bundle through review and tells the owner."""
    _require(request, roles.REVIEW_BUNDLES)
    _not_own(request, case)
    if case.kind != Case.SUBMISSION:
        raise ActionError('Only bundle submissions have review stages.')
    if stage not in STAGE_STATUS:
        raise ActionError('Unknown stage.')
    message = (message or '').strip()
    _storable(message)
    if stage == Case.STAGE_CHANGES and not message:
        raise ActionError('Say what needs to change; the owner sees this message.')
    if case.stage in FINAL_STAGES and stage not in FINAL_STAGES and not move_back:
        raise ActionError('This review already ended ({0}). Tick "move the review back" to reopen it.'.format(
            case.get_stage_display()))
    if stage == Case.STAGE_NOT_ACCEPTED and not message:
        raise ActionError('Tell the owner why it cannot be accepted; they receive this message.')
    if stage in (Case.STAGE_ACCEPTED, Case.STAGE_RELEASED) and not accept_anyway:
        from . import review
        missing = review.checklist_open(case)
        if missing:
            raise ActionError('{0} checklist item{1} not ticked ({2}). Tick them, or choose "accept anyway".'.format(
                len(missing), ' is' if len(missing) == 1 else 's are',
                '; '.join(item['label'] for item in missing[:3]) + ('; ...' if len(missing) > 3 else '')))
    if stage in (Case.STAGE_ACCEPTED, Case.STAGE_RELEASED) and case.liens.filter(status=Lien.OPEN).exists():
        raise ActionError('Resolve or waive the open liens first.')
    from .models import ConsoleSetting
    if (stage in (Case.STAGE_ACCEPTED, Case.STAGE_RELEASED) and case.stage not in (Case.STAGE_ACCEPTED, Case.STAGE_RELEASED)
            and ConsoleSetting.get('two_person_acceptance')):
        proposer = (case.data or {}).get('acceptance_proposed_by')
        if proposer is None or proposer == request.user.pk:
            # The first reviewer proposes; a different one confirms.
            if proposer is None:
                with transaction.atomic():
                    _lock(case)
                    data = dict(case.data or {}, acceptance_proposed_by=request.user.pk,
                                acceptance_proposed_at=timezone.now().isoformat())
                    Case.objects.filter(pk=case.pk).update(data=data, updated_at=timezone.now())
                    _event(case, '{0} proposed accepting this bundle; a second reviewer has to confirm.'.format(
                        request.user.username), request.user)
                    audit.record(request, 'case.propose_accept', case)
                comms.notify_second_reviewers(case, request.user)
                return 'proposed'
            raise ActionError('You proposed this acceptance. A second reviewer has to confirm it.')

    name = case.bundle.name if case.bundle else case.data.get('bundle_name', 'your bundle')
    email_body = ''
    if stage in STAGE_MESSAGES and _reachable(case):
        email_body = STAGE_MESSAGES[stage].format(name=name)
        try:
            comms.tell_requester(
                case, Notification.REVIEW, 'ELSA review: {0} ({1})'.format(name, dict(Case.STAGE_CHOICES)[stage]),
                'Review: {0}'.format(dict(Case.STAGE_CHOICES)[stage]), email_body,
                text=message, staff_member=request.user, request=request)
        except Exception:
            logger.exception('stage email on case %s failed', case.pk)
            raise ActionError('The email to the owner could not be sent. Nothing changed; try again.')

    with transaction.atomic():
        _lock(case)
        before = case.stage
        case.stage = stage
        if before != stage:
            case.stage_changed_at = timezone.now()
        status = STAGE_STATUS[stage]
        if status in Case.CLOSED:
            _close(case, status, request.user)
        else:
            case.status = status
            case.closed_at = None
            case.closed_by = None
        if case.assignee is None:
            case.assignee = request.user
        if email_body:
            _first_response(case)
        _touch(case)
        _save(case)
        if message and email_body:
            CaseNote.objects.create(case=case, kind=CaseNote.REPLY, body=message, author=request.user)
        elif message:
            CaseNote.objects.create(case=case, kind=CaseNote.NOTE, body=message, author=request.user)
        _event(case, '{0} moved the bundle to {1}.'.format(request.user.username,
                                                          case.get_stage_display()), request.user)
        audit.record(request, 'case.stage', case, reason=message,
                     changes={'stage': [before, stage], 'accept_anyway': bool(accept_anyway)})
        if stage == Case.STAGE_ACCEPTED and (case.data or {}).get('acceptance_proposed_by'):
            data = dict(case.data)
            data.pop('acceptance_proposed_by', None)
            data['acceptance_confirmed_by'] = request.user.pk
            Case.objects.filter(pk=case.pk).update(data=data)


def add_lien(request, case, description, file_path='', line=None):
    _require(request, roles.REVIEW_BUNDLES)
    description = (description or '').strip()
    if not description:
        raise ActionError('Describe the lien first.')
    _storable(description, file_path)
    file_path = (file_path or '').strip().lstrip('/')[:500]
    if '..' in file_path.split('/'):
        raise ActionError('That file path is not inside the bundle.')
    try:
        line = int(line) if line not in (None, '') else None
    except (TypeError, ValueError):
        line = None
    if line is not None and (line < 1 or line > 2147483647 or not file_path):
        line = None
    with transaction.atomic():
        lien = Lien.objects.create(case=case, description=description, created_by=request.user,
                                   file_path=file_path, line=line)
        _touch(case)
        case.save(update_fields=['updated_at'])
        audit.record(request, 'lien.add', case, changes={'lien': lien.pk})
    if case.kind == Case.SUBMISSION and _reachable(case):
        name = case.bundle.name if case.bundle else 'your bundle'
        try:
            comms.tell_requester(case, Notification.LIEN, 'Something to fix on {0}'.format(name),
                                 'Something to fix before acceptance',
                                 'The ELSA team found something to fix in "{0}". When it is done, mark it '
                                 'fixed on the bundle page.'.format(name), text=description,
                                 staff_member=request.user, request=request)
        except Exception:
            logger.exception('lien notice on case %s failed', case.pk)
    return lien


def close_lien(request, lien, status):
    _require(request, roles.REVIEW_BUNDLES)
    _not_own(request, lien.case)
    if status not in (Lien.RESOLVED, Lien.WAIVED, Lien.OPEN):
        raise ActionError('Unknown lien status.')
    with transaction.atomic():
        before = lien.status
        lien.status = status
        lien.closed_by = request.user if status != Lien.OPEN else None
        lien.closed_at = timezone.now() if status != Lien.OPEN else None
        lien.save()
        lien.case.updated_at = timezone.now()
        lien.case.save(update_fields=['updated_at'])
        audit.record(request, 'lien.close', lien.case,
                     changes={'lien': lien.pk, 'status': [before, status]})
    if status in (Lien.RESOLVED, Lien.WAIVED) and before == Lien.OPEN and lien.case.kind == Case.SUBMISSION:
        # In ELSA only: good news does not need an email of its own.
        comms.tell_requester(lien.case, Notification.LIEN, 'An item to fix was {0}'.format(
                                 'confirmed fixed' if status == Lien.RESOLVED else 'waived'),
                             '', '', text=lien.description, email=False)


# --- Agency ---

def agency_label(code):
    return dict(UserProfile.AGENCY_CHOICES).get(code, code)


def change_agency(request, user, agency, reason, case=None, message=''):
    """Change a user's agency. Labels written after this use the new agency in their identifiers."""
    _require(request, roles.DECIDE_AGENCY)
    _not_own(request, user=user)
    if agency not in dict(UserProfile.AGENCY_CHOICES):
        raise ActionError('Unknown agency.')
    reason = (reason or '').strip()
    if not reason and case is None:
        raise ActionError('Give a reason; it goes in the audit log.')
    _storable(reason, message)

    with transaction.atomic():
        profile = UserProfile.objects.select_for_update().get(user=user)
        if case is not None:
            _lock(case)
            if not case.is_active:
                raise ActionError('A colleague already closed this request.')
        before = profile.agency
        if before == agency:
            raise ActionError('{0} is already with {1}.'.format(user.username, agency_label(agency)))
        profile.agency = agency
        profile.save(update_fields=['agency'])
        if case is not None:
            _close(case, Case.RESOLVED, request.user)
            if case.assignee is None:
                case.assignee = request.user
            _first_response(case)
            _touch(case)
            _save(case)
            _event(case, '{0} approved the change: {1} to {2}.'.format(
                request.user.username, agency_label(before), agency_label(agency)), request.user)
        audit.record(request, 'user.agency', user, reason=reason or 'Approved request #{0}'.format(case.pk),
                     changes={'agency': [before, agency]})

    lead = ('Your ELSA account now belongs to {0} (it was {1}). Bundle labels ELSA writes '
            'from now on use the new agency in their identifiers.'.format(
                agency_label(agency), agency_label(before)))
    try:
        if case is not None and _reachable(case):
            message = (message or '').strip()
            comms.tell_requester(case, Notification.OUTCOME, 'Your ELSA agency is now {0}'.format(agency_label(agency)),
                                 'Agency changed', lead, text=message, staff_member=request.user, request=request)
            if message:
                CaseNote.objects.create(case=case, kind=CaseNote.REPLY, body=message, author=request.user)
        else:
            from django.urls import reverse
            comms.tell_user(user, Notification.ACCOUNT, 'Your ELSA agency is now {0}'.format(agency_label(agency)),
                            lead, staff_member=request.user, url=reverse('friends:useraccount'))
    except Exception:
        # The change is already done and audited, so don't undo it if the notice fails.
        logger.exception('agency notice to %s failed', user.username)


def decline_agency(request, case, message):
    _require(request, roles.DECIDE_AGENCY)
    _not_own(request, case)
    if not (message or '').strip():
        raise ActionError('Tell the user why; they receive this message.')
    set_status(request, case, Case.DECLINED, message=message)


# --- Accounts ---

def _guard_account_target(request, user):
    if user.pk == request.user.pk:
        raise ActionError('You cannot do this to your own account here.')
    if (user.is_superuser or roles.is_member(user)) and not request.user.is_superuser:
        raise ActionError('Only an ELSA developer can do this to a staff account.')


def _end_all_sessions(user):
    UserProfile.objects.filter(user=user).update(session_generation=F('session_generation') + 1)
    store = devices._session_store()
    for key in list(UserSession.objects.filter(user=user).values_list('session_key', flat=True)):
        store(key).delete()
    UserSession.objects.filter(user=user).delete()
    devices.forget_all_devices(user)


def sign_out_everywhere(request, user, reason):
    _require(request, roles.MANAGE_ACCOUNTS)
    _guard_account_target(request, user)
    if not (reason or '').strip():
        raise ActionError('Give a reason; it goes in the audit log.')
    _require_confirmation(request)
    with transaction.atomic():
        _end_all_sessions(user)
        audit.record(request, 'user.sign_out', user, reason=reason)


def set_active(request, user, active, reason):
    _require(request, roles.MANAGE_ACCOUNTS)
    _guard_account_target(request, user)
    if not (reason or '').strip():
        raise ActionError('Give a reason; it goes in the audit log.')
    if user.is_active == active:
        raise ActionError('The account is already {0}.'.format('enabled' if active else 'disabled'))
    if active and UserProfile.objects.filter(user=user, closed_at__isnull=False).exists():
        # Closing an account wipes the name, email and passkeys, so it can't be switched back on.
        raise ActionError('The owner closed this account. It cannot be enabled again.')
    if not active:
        _require_confirmation(request)
    with transaction.atomic():
        user.is_active = active
        user.save(update_fields=['is_active'])
        if not active:
            _end_all_sessions(user)
        audit.record(request, 'user.enable' if active else 'user.disable', user, reason=reason,
                     changes={'is_active': [not active, active]})



# --- Assignment and spam ---

def assign(request, case, person):
    """Hands a case to a colleague (or to yourself) and tells them."""
    _require(request, roles.WORK_CASES)
    if person is None or not person.is_active or not roles.has(person, roles.WORK_CASES):
        raise ActionError('Cases can only go to staff who can work them.')
    with transaction.atomic():
        _lock(case)
        before = case.assignee.username if case.assignee else ''
        case.assignee = person
        if case.status == Case.OPEN:
            case.status = Case.IN_PROGRESS
        _touch(case)
        _save(case)
        _event(case, '{0} assigned this request to {1}.'.format(request.user.username, person.username),
               request.user)
        audit.record(request, 'case.assign', case, changes={'assignee': [before, person.username]})
    comms.notify_assignee(case, request.user)


def mark_spam(request, case, block=True):
    """Close a junk request and block the address if the sender is anonymous."""
    from .models import BlockedSender
    _require(request, roles.WORK_CASES)
    with transaction.atomic():
        _lock(case)
        case.is_spam = True
        _close(case, Case.DECLINED, request.user)
        _touch(case)
        _save(case)
        _event(case, '{0} marked this as spam.'.format(request.user.username), request.user)
        blocked = ''
        if block and case.requester_id is None and case.requester_email:
            BlockedSender.objects.get_or_create(email=case.requester_email.strip().lower(),
                                                defaults={'created_by': request.user,
                                                          'reason': 'Spam on #{0}'.format(case.pk)})
            blocked = case.requester_email
        audit.record(request, 'case.spam', case, changes={'blocked': blocked})


def not_spam(request, case):
    from .models import BlockedSender
    _require(request, roles.WORK_CASES)
    with transaction.atomic():
        _lock(case)
        case.is_spam = False
        case.status = Case.OPEN
        case.closed_at = None
        case.closed_by = None
        _touch(case)
        _save(case)
        if case.requester_email:
            BlockedSender.objects.filter(email=case.requester_email.strip().lower()).delete()
        _event(case, '{0} said this is not spam.'.format(request.user.username), request.user)
        audit.record(request, 'case.not_spam', case)


def unblock(request, blocked):
    _require(request, roles.WORK_CASES)
    with transaction.atomic():
        audit.record(request, 'sender.unblock', changes={'email': blocked.email})
        blocked.delete()


# --- Saved replies ---

def save_reply(request, saved, title, body):
    from .models import SavedReply
    _require(request, roles.WORK_CASES)
    title = (title or '').strip()[:120]
    body = (body or '').strip()
    if not title or not body:
        raise ActionError('A saved reply needs a name and some text.')
    _storable(title, body)
    with transaction.atomic():
        if saved is None:
            saved = SavedReply(created_by=request.user)
        saved.title, saved.body, saved.updated_at = title, body, timezone.now()
        saved.save()
        audit.record(request, 'saved_reply.save', changes={'title': title})
    return saved


def delete_reply(request, saved):
    _require(request, roles.WORK_CASES)
    with transaction.atomic():
        audit.record(request, 'saved_reply.delete', changes={'title': saved.title})
        saved.delete()


# --- Review checklist ---

def set_checklist(request, case, ticked):
    from . import review
    if case.kind == Case.UPDATE:
        # Rollout checklist for an outside update (staff.rollout). ELSA's own checks aren't stored.
        from . import rollout
        _require(request, roles.WORK_CASES)
        items = [item for item in rollout.checklist_for(case) if not item['auto']]
        if not items:
            raise ActionError('This request has no checklist.')
    else:
        _require(request, roles.REVIEW_BUNDLES)
        _not_own(request, case)
        if case.kind != Case.SUBMISSION:
            raise ActionError('Only bundle submissions have a review checklist.')
        items = review.checklist_for(case)
    answers = {item['key']: item['key'] in ticked for item in items}
    with transaction.atomic():
        _lock(case)
        before = dict(case.checklist or {})
        case.checklist = answers
        _touch(case)
        _save(case)
        audit.record(request, 'case.checklist', case, changes={'checklist': [before, answers]})


# --- Account support ---

def unlock_sign_in(request, user, reason):
    """Clear a code lockout and the failed password counters."""
    from friends import throttle
    from friends.views import _password_key
    _require(request, roles.WORK_CASES)
    with transaction.atomic():
        UserProfile.objects.filter(user=user).update(otp_attempts=0, otp_locked_until=None)
        throttle.reset(_password_key(user.username))
        audit.record(request, 'user.unlock', user, reason=reason or '')


def remind_to_confirm(request, user):
    """An account that never confirmed its address: tell them how to finish."""
    _require(request, roles.WORK_CASES)
    if user.is_active or not user.email:
        raise ActionError('Only unconfirmed accounts with an email address need this.')
    try:
        email_account(user, 'Finish setting up your ELSA account', 'Finish setting up your account',
                      'Your ELSA account "{0}" is waiting for you to confirm your email address. '
                      'Sign in with your username and password, and ELSA sends a fresh code to this '
                      'address.'.format(user.username), staff_member=request.user)
    except Exception:
        logger.exception('confirmation reminder to %s failed', user.username)
        raise ActionError('The email could not be sent.')
    audit.record(request, 'user.remind_confirm', user)


def remove_passkey(request, passkey, reason):
    """For a lost or stolen authenticator.  The owner is told by email."""
    user = passkey.user
    _require(request, roles.MANAGE_ACCOUNTS)
    _guard_account_target(request, user)
    if not (reason or '').strip():
        raise ActionError('Give a reason; it goes in the audit log.')
    _require_confirmation(request)
    name = passkey.name
    with transaction.atomic():
        passkey.delete()
        devices.record(user, 'passkey_removed', request, detail='{0} (by ELSA staff)'.format(name)[:255])
        audit.record(request, 'user.passkey_remove', user, reason=reason, changes={'passkey': name})
    try:
        from django.urls import reverse
        comms.tell_user(user, Notification.ACCOUNT, 'A passkey was removed from your ELSA account',
                        'The ELSA team removed the passkey "{0}" from your account, as part of a support '
                        'request. If you did not ask for this, contact the ELSA team.'.format(name),
                        staff_member=request.user, url=reverse('friends:useraccount'), always_email=True)
    except Exception:
        logger.exception('passkey removal notice to %s failed', user.username)


# --- Announcements and broadcasts ---

def post_announcement(request, title, body, level, starts_at, ends_at):
    from .models import Announcement
    _require(request, roles.MANAGE_ACCOUNTS)
    title = (title or '').strip()[:150]
    if not title:
        raise ActionError('An announcement needs a title.')
    _storable(title, body)
    if ends_at is None or starts_at is None or ends_at <= starts_at:
        raise ActionError('The end must be after the start.')
    if level not in dict(Announcement.LEVEL_CHOICES):
        level = Announcement.INFO
    with transaction.atomic():
        item = Announcement.objects.create(title=title, body=(body or '').strip()[:2000], level=level,
                                           starts_at=starts_at, ends_at=ends_at, created_by=request.user)
        audit.record(request, 'announcement.post', changes={'title': title, 'id': item.pk})
    return item


def end_announcement(request, item):
    _require(request, roles.MANAGE_ACCOUNTS)
    with transaction.atomic():
        item.ends_at = timezone.now()
        item.save(update_fields=['ends_at'])
        audit.record(request, 'announcement.end', changes={'title': item.title, 'id': item.pk})


def save_broadcast(request, broadcast, subject, body, segment):
    from .models import Broadcast
    _require(request, roles.MANAGE_ACCOUNTS)
    subject = ' '.join((subject or '').split())[:150]
    body = (body or '').strip()
    if not subject or not body:
        raise ActionError('Write a subject and a message.')
    _storable(subject, body)
    if segment not in dict(Broadcast.SEGMENT_CHOICES):
        raise ActionError('Choose who it is for.')
    if broadcast is not None and broadcast.status != Broadcast.DRAFT:
        raise ActionError('This email has already been sent.')
    with transaction.atomic():
        broadcast = broadcast or Broadcast(created_by=request.user)
        broadcast.subject, broadcast.body, broadcast.segment = subject, body, segment
        broadcast.save()
        audit.record(request, 'broadcast.save', changes={'id': broadcast.pk, 'subject': subject})
    return broadcast


def send_broadcast_test(request, broadcast):
    from . import broadcasts
    _require(request, roles.MANAGE_ACCOUNTS)
    if not request.user.email:
        raise ActionError('Your account has no email address.')
    broadcasts.send_one(broadcast, request.user, test=True)


def send_broadcast(request, broadcast):
    """Queue the email and start the sender in the background (see staff.broadcasts).

    Returns how many people it's going to.
    """
    from . import broadcasts
    from .models import Broadcast
    _require(request, roles.MANAGE_ACCOUNTS)
    _require_confirmation(request)
    total = broadcasts.audience(broadcast.segment).count()
    with transaction.atomic():
        # The one way out of Draft, so two clicks can never send it twice.
        claimed = Broadcast.objects.filter(pk=broadcast.pk, status=Broadcast.DRAFT).update(
            status=Broadcast.SENDING, total=total, cursor=0, recipient_count=0, failed_count=0,
            worker='', heartbeat_at=timezone.now())
        if not claimed:
            raise ActionError('This email has already been sent.')
        audit.record(request, 'broadcast.send', changes={'id': broadcast.pk, 'subject': broadcast.subject,
                                                         'segment': broadcast.segment, 'recipients': total})
    broadcast.refresh_from_db()
    broadcasts.launch(broadcast)
    return total


def resume_broadcast(request, broadcast):
    """Restart a stopped sender. It carries on after the last person handled."""
    from . import broadcasts
    from .models import Broadcast
    _require(request, roles.MANAGE_ACCOUNTS)
    broadcast.refresh_from_db()
    if broadcast.status != Broadcast.SENDING:
        raise ActionError('This email is not being sent.')
    if not broadcast.stalled():
        raise ActionError('This email is still being sent. Refresh the page in a minute.')
    _require_confirmation(request)
    audit.record(request, 'broadcast.resume', changes={'id': broadcast.pk, 'subject': broadcast.subject,
                                                       'handled_before': broadcast.handled(),
                                                       'recipients': broadcast.total})
    broadcasts.launch(broadcast)


# --- Running ELSA ---

def cancel_validation(request, run):
    from build import validate_runner
    from build.models import ValidationRun
    _require(request, roles.REVIEW_BUNDLES)
    if run.status != ValidationRun.STATUS_QUEUED:
        raise ActionError('Only a queued run can be cancelled here. A running check stops on its own '
                          'or at the time limit.')
    validate_runner._fail(run, 'Cancelled by ELSA staff ({0}).'.format(request.user.username))
    audit.record(request, 'validate.cancel', run.bundle, changes={'run': run.pk})


def clear_stuck_validations(request):
    from build import validate_runner
    _require(request, roles.REVIEW_BUNDLES)
    cleared = validate_runner.reap_abandoned_runs()
    audit.record(request, 'validate.reap', changes={'cleared': str(cleared)})
    return cleared


def run_validation(request, bundle, tier):
    from build import validate_runner
    from build.models import ValidationRun
    _require(request, roles.REVIEW_BUNDLES)
    if tier not in dict(ValidationRun.TIER_CHOICES):
        tier = ValidationRun.TIER_STRUCTURE
    run = validate_runner.start(bundle, tier)
    audit.record(request, 'validate.start', bundle, changes={'tier': tier,
                                                             'run': getattr(run, 'pk', None)})
    return run


def apply_label_upgrades(request, bundle):
    """Bring a bundle's labels up to date, submitted or not."""
    from build import label_upgrades
    _require(request, roles.REVIEW_BUNDLES)
    try:
        rows = label_upgrades.upgrade_bundle(bundle, source=label_upgrades.SOURCE_STAFF,
                                             include_submitted=True, wait_seconds=30)
    except label_upgrades.UpgradeBusy:
        raise ActionError('This bundle is already being updated. Try again in a minute.')
    audit.record(request, 'bundle.label_upgrade', bundle,
                 changes={'upgrades': [row.pk for row in rows], 'revision': bundle.label_revision})
    return rows


def undo_label_upgrade(request, row):
    from build import label_upgrades
    _require(request, roles.REVIEW_BUNDLES)
    try:
        label_upgrades.undo(row)
    except ValueError as error:
        raise ActionError(str(error))
    except label_upgrades.UpgradeBusy:
        raise ActionError('This bundle is being updated right now. Try again in a minute.')
    audit.record(request, 'bundle.label_upgrade_undo', row.bundle,
                 changes={'upgrade': row.pk, 'migration': row.number})
    return row


ASSISTANT_FLAG = 'assistant-disabled'


def assistant_enabled():
    from django.core.cache import cache
    return not cache.get(ASSISTANT_FLAG)


def set_assistant(request, enabled, reason):
    """The same switch as `manage.py assistant_toggle`, from the console."""
    from django.core.cache import cache
    _require(request, roles.MANAGE_ACCOUNTS)
    if not enabled and not (reason or '').strip():
        raise ActionError('Say why the Assistant is being switched off.')
    if enabled:
        cache.delete(ASSISTANT_FLAG)
    else:
        cache.set(ASSISTANT_FLAG, 1, None)
    audit.record(request, 'assistant.on' if enabled else 'assistant.off', reason=reason or '')



def start_view_as(request, person, reason):
    """Read-only "view as user" for 15 minutes.  See staff/view_as.py."""
    from . import view_as
    _require(request, roles.MANAGE_ACCOUNTS)
    _guard_account_target(request, person)
    if roles.is_member(person) or person.is_superuser or not person.is_active:
        raise ActionError('Only active, non-staff accounts can be viewed this way.')
    if not (reason or '').strip():
        raise ActionError('Give a reason; it goes in the audit log.')
    _require_confirmation(request)
    view_as.begin(request, person)
    audit.record(request, 'user.view_as', person, reason=reason)
    from . import security_alerts
    security_alerts.view_as_started(request, person)



def start_conversation(request, person, subject, body, data=None):
    """Staff start a new conversation with a user, by bell and email."""
    _require(request, roles.WORK_CASES)
    subject = ' '.join((subject or '').split())[:150]
    body = (body or '').strip()
    if not subject or not body:
        raise ActionError('Write a subject and a message.')
    _storable(subject, body)
    if not person.is_active or not person.email:
        raise ActionError('This account cannot receive messages.')
    if person.pk == request.user.pk:
        raise ActionError('You cannot start a conversation with yourself.')
    with transaction.atomic():
        case = Case.objects.create(
            kind=Case.MESSAGE, title=subject, requester=person, requester_username=person.username,
            requester_name=person.get_full_name(), requester_email=person.email,
            assignee=request.user, status=Case.WAITING, first_response_at=timezone.now(),
            data=dict(data or {}, started_by=request.user.username))
        try:
            comms.tell_requester(case, Notification.MESSAGE, subject, 'A message from the ELSA team',
                                 'The ELSA team wrote to you:', text=body, staff_member=request.user,
                                 request=request)
        except Exception:
            logger.exception('new conversation with %s failed', person.username)
            raise ActionError('The email could not be sent. Nothing was saved; try again.')
        CaseNote.objects.create(case=case, kind=CaseNote.REPLY, body=body, author=request.user)
        audit.record(request, 'case.start', case, changes={'to': person.username})
    comms.mark_seen(case, request.user, CaseRead.STAFF)
    return case



SETTING_RULES = {
    'two_person_acceptance': ('bool', None, None),
    'first_reply_days': ('int', 1, 30),
    'stalled_days': ('int', 7, 365),
    'pipeline_stuck_days': ('int', 3, 365),
}


def change_setting(request, key, raw):
    """A team-wide console setting, by an account admin, audited."""
    from .models import ConsoleSetting
    _require(request, roles.MANAGE_ACCOUNTS)
    if key not in SETTING_RULES:
        raise ActionError('Unknown setting.')
    kind, low, high = SETTING_RULES[key]
    if kind == 'bool':
        value = raw in ('1', 'true', 'on', True)
    else:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise ActionError('Enter a whole number.')
        if not low <= value <= high:
            raise ActionError('Choose a number from {0} to {1}.'.format(low, high))
    before = ConsoleSetting.get(key)
    with transaction.atomic():
        ConsoleSetting.objects.update_or_create(key=key, defaults={'value': {'v': value}, 'updated_by': request.user,
                                                                   'updated_at': timezone.now()})
        audit.record(request, 'setting.change', changes={key: [before, value]})
    if before != value:
        from . import security_alerts
        security_alerts.setting_changed(request, key, before, value)



# --- Knowledge notes and hand-over ---

def save_knowledge(request, note, title, topic, body, pinned):
    from .models import KnowledgeNote
    _require(request, roles.WORK_CASES)
    title = ' '.join((title or '').split())[:200]
    body = (body or '').strip()
    if not title or not body:
        raise ActionError('A note needs a title and some text.')
    _storable(title, topic, body)
    with transaction.atomic():
        creating = note is None
        note = note or KnowledgeNote(created_by=request.user)
        note.title, note.topic, note.body, note.pinned = title, ' '.join((topic or '').split())[:100], body, pinned
        note.updated_by, note.updated_at = request.user, timezone.now()
        note.save()
        audit.record(request, 'knowledge.create' if creating else 'knowledge.update', changes={'title': title})
    return note


def delete_knowledge(request, note):
    _require(request, roles.WORK_CASES)
    with transaction.atomic():
        audit.record(request, 'knowledge.delete', changes={'title': note.title})
        note.delete()


def hand_over(request, person):
    """Hand all your open requests to a colleague for time away. Returns how many."""
    _require(request, roles.WORK_CASES)
    if person is None or person.pk == request.user.pk:
        raise ActionError('Choose a colleague to hand them to.')
    if not person.is_active or not roles.has(person, roles.WORK_CASES):
        raise ActionError('They cannot work requests.')
    count = 0
    for case in Case.objects.filter(assignee=request.user, status__in=Case.ACTIVE):
        assign(request, case, person)
        count += 1
    if not count:
        raise ActionError('You have no open requests to hand over.')
    return count
