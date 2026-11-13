# -*- coding: utf-8 -*-
"""Admin Console pages.

All views are wrapped by console_view or member_view. Changes go through staff.actions.
"""
from __future__ import unicode_literals

import json
import logging
import os
import shutil
from datetime import timedelta

from django.apps import apps as django_apps
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db.models import Count, F, Min, OuterRef, Q, Subquery
from django.http import FileResponse, Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.views.decorators.http import require_POST

from build import label_migrations, label_upgrades, preflight, validate_report
from build.downloads import bundle_tarball_response
from build.models import Bundle, ValidationRun
from friends import passkeys, throttle
from friends.models import AccountEvent, UserProfile, UserSession

from . import actions, audit, comms, dbtext, elevation, review, roles, sessions
from .decorators import console_view, limited, member_view
from .models import AuditEntry, Case, CaseRead, Lien, SavedReply


logger = logging.getLogger(__name__)

CHALLENGE_KEY = 'staff_passkey_challenge'
VERIFY_FAILURES_ALLOWED = 10     # per member per 15 minutes
PAGE_SIZE = 50
TEXT_LIMIT = 10000
REASON_LIMIT = 1000


def _safe_next(request, fallback='staff:today', candidate=None):
    target = candidate or request.POST.get('next') or request.GET.get('next') or ''
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()},
                                                  require_https=request.is_secure()):
        return target
    return reverse(fallback)


def _text(request, field, limit=TEXT_LIMIT):
    return (request.POST.get(field) or '').strip()[:limit]


def _pk(value):
    """Id from a form or URL, or 0 if it's junk, so it's a not found instead of a server error."""
    text = str(value or '').strip()
    return int(text) if text.isdecimal() else 0


def _date(value):
    """Date from a form, or None. parse_date raises on impossible dates like February 31."""
    try:
        return parse_date(value or '')
    except ValueError:
        return None


def _query(request, name='q', limit=100):
    """Search text. Narrowed because an emoji compared to a utf8mb4 column over utf8mb3 is a DB error."""
    return dbtext.narrow((request.GET.get(name) or '').strip()[:limit])


def _page(request, queryset):
    return Paginator(queryset, PAGE_SIZE).get_page(request.GET.get('page'))


def _console_context(request, **extra):
    """What the console layout needs on every page."""
    context = {
        'console_role': roles.role_label(request.user),
        'console_expires_at': elevation.expires_at(request),
        'console_expiry': elevation.expiry_state(request),
        'can': {
            'work': roles.has(request.user, roles.WORK_CASES),
            'agency': roles.has(request.user, roles.DECIDE_AGENCY),
            'review': roles.has(request.user, roles.REVIEW_BUNDLES),
            'accounts': roles.has(request.user, roles.MANAGE_ACCOUNTS),
            'audit': roles.has(request.user, roles.VIEW_AUDIT),
        },
        'open_case_count': Case.objects.filter(status__in=Case.ACTIVE, is_spam=False).count(),
        'search_query': '',
    }
    params = request.GET.copy()
    params.pop('page', None)
    encoded = params.urlencode()
    context['querystring'] = encoded + '&' if encoded else ''
    context.update(extra)
    from . import navigation
    context['nav'] = navigation.build(request.user, context.get('section', ''))
    context.setdefault('page_title', context['nav']['title'])
    from . import help_text
    context['page_help'] = help_text.for_topic(context.get('help_topic') or context.get('section', ''))
    return context


# --- Getting in and out ---

@member_view
def choose(request):
    """After sign-in, for people who are both an ELSA user and staff."""
    if elevation.is_elevated(request):
        return redirect('staff:today')
    return render(request, 'staff/choose.html', {
        'has_passkey': request.user.passkeys.exists(),
        'passkey_fresh': elevation.fresh_passkey(request),
        'role': roles.role_label(request.user),
    })


@member_view
@require_POST
def enter(request):
    """Opens admin mode on a passkey tap from the last few minutes."""
    if elevation.elevate(request):
        audit.record(request, 'console.enter', changes={'via': 'recent passkey'})
        sessions.started(request)
        return HttpResponseRedirect(_safe_next(request))
    return HttpResponseRedirect('{0}?next={1}'.format(reverse('staff:verify'),
                                                     _safe_next(request)))


@member_view
def verify(request):
    """The passkey step in front of admin mode."""
    if elevation.is_elevated(request):
        return HttpResponseRedirect(_safe_next(request))
    return render(request, 'staff/verify.html', {
        'has_passkey': request.user.passkeys.exists(),
        'next': _safe_next(request),
    })


def _verify_key(request):
    return 'staff-verify-{0}'.format(request.user.pk)


@member_view
@require_POST
def passkey_options(request):
    if throttle.count(_verify_key(request)) >= VERIFY_FAILURES_ALLOWED:
        return JsonResponse({'error': 'Too many failed attempts. Wait 15 minutes.'}, status=429)
    if not request.user.passkeys.exists():
        return JsonResponse({'error': 'Add a passkey on your Account page first.'}, status=400)
    return JsonResponse(passkeys.authentication_options(request, user=request.user,
                                                        challenge_key=CHALLENGE_KEY))


@member_view
@require_POST
def passkey_verify(request):
    """Check a passkey tap. ``purpose`` is 'enter' (admin mode) or 'confirm' (one dangerous action)."""
    try:
        body = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'error': 'Malformed request.'}, status=400)
    if not isinstance(body, dict) or not isinstance(body.get('credential'), dict):
        return JsonResponse({'error': 'Malformed request.'}, status=400)
    if throttle.count(_verify_key(request)) >= VERIFY_FAILURES_ALLOWED:
        return JsonResponse({'error': 'Too many failed attempts. Wait 15 minutes.'}, status=429)

    passkey, error = passkeys.verify_authentication(request, body['credential'], user=request.user,
                                                    challenge_key=CHALLENGE_KEY)
    if error:
        throttle.hit(_verify_key(request), 15 * 60)
        from . import security_alerts
        security_alerts.failed_passkey(request, request.user)
        return JsonResponse({'error': error}, status=400)

    elevation.note_passkey(request, request.user)

    if (body.get('purpose') or request.GET.get('purpose')) == 'confirm':
        if not elevation.is_elevated(request):
            return JsonResponse({'error': 'Admin mode has ended. Reload the page.'}, status=401)
        scope = (request.GET.get('for') or '')[:300]
        if not scope.startswith('/') or '#' not in scope:
            return JsonResponse({'error': 'Malformed request.'}, status=400)
        elevation.issue_confirmation(request, scope)
        return JsonResponse({'ok': True})

    elevation.elevate(request)
    audit.record(request, 'console.enter', changes={'via': 'passkey', 'passkey': passkey.name})
    sessions.started(request)
    return JsonResponse({'ok': True, 'redirect': _safe_next(request, candidate=body.get('next') or request.GET.get('next'))})


@console_view()
@require_POST
def stay(request):
    """Keeps admin mode open (the guard has just restarted the idle clock)."""
    left, hard = elevation.expiry_state(request) or (0, False)
    return JsonResponse({'seconds_left': int(left), 'hard_limit': hard})


@member_view
@require_POST
def leave(request):
    if elevation.is_elevated(request):
        audit.record(request, 'console.exit')
    sessions.ended(request)
    from . import view_as
    view_as.end(request)
    elevation.end(request)
    messages.info(request, 'You left the Admin Console.')
    return redirect('main:index')


# --- Queue ---

STATUS_FILTERS = (
    ('attention', 'For you'),
    ('active', 'All open'),
    ('waiting', 'Waiting on user'),
    ('closed', 'Closed'),
    ('due', 'First reply due'),
    ('new', 'No reply yet'),
    ('answered', 'User answered'),
    ('mine', 'Assigned to me'),
    ('spam', 'Spam'),
    ('all', 'Everything'),
)
# The four shown as buttons; the rest are under "More filters".
MAIN_FILTERS = ('attention', 'active', 'waiting', 'closed')


def _with_unseen(cases, user):
    """Annotates when this staff member last opened each case."""
    seen = CaseRead.objects.filter(case=OuterRef('pk'), user=user, role=CaseRead.STAFF).values('seen_at')[:1]
    return cases.annotate(my_seen_at=Subquery(seen))


def _answered(cases):
    return cases.filter(last_user_message_at__isnull=False).filter(
        Q(my_seen_at__isnull=True) | Q(last_user_message_at__gt=F('my_seen_at')))


@console_view()
def home(request):
    from . import alerts
    show = request.GET.get('show')
    if not show:
        # Start on what's assigned to me, or everything open if there's nothing.
        show = 'attention' if alerts.for_attention(request.user).exists() else 'active'
    kind = request.GET.get('kind', '')
    query = _query(request)

    cases = _with_unseen(Case.objects.select_related('assignee', 'bundle'), request.user)
    if show != 'spam':
        cases = cases.filter(is_spam=False)
    if show == 'attention':
        from . import alerts
        cases = alerts.for_attention(request.user, Case.objects.select_related('assignee', 'bundle'))
    elif show == 'due':
        from . import targets
        waiting_reply = Case.objects.filter(status__in=Case.ACTIVE, is_spam=False, first_response_at__isnull=True)
        cases = cases.filter(pk__in=[c.pk for c in waiting_reply if targets.state(c) in ('late', 'soon')])
    elif show == 'active':
        cases = cases.filter(status__in=Case.ACTIVE)
    elif show == 'new':
        cases = cases.filter(status__in=Case.ACTIVE, first_response_at__isnull=True)
    elif show == 'answered':
        cases = _answered(cases.filter(status__in=Case.ACTIVE))
    elif show == 'mine':
        cases = cases.filter(status__in=Case.ACTIVE, assignee=request.user)
    elif show == 'waiting':
        cases = cases.filter(status=Case.WAITING)
    elif show == 'closed':
        cases = cases.filter(status__in=Case.CLOSED)
    elif show == 'spam':
        cases = cases.filter(is_spam=True)
    else:
        show = 'all'
    if kind in dict(Case.KIND_CHOICES):
        cases = cases.filter(kind=kind)
    else:
        kind = ''
    if query:
        filters = (Q(title__icontains=query) | Q(requester_username__icontains=query)
                   | Q(requester_name__icontains=query) | Q(requester_email__icontains=query)
                   | Q(bundle__name__icontains=query))
        if query.lstrip('#').isdecimal():
            filters |= Q(pk=int(query.lstrip('#')))
        cases = cases.filter(filters)

    # Oldest first while there is work to do, so nothing sits at the bottom.
    working = show in ('attention', 'due', 'active', 'new', 'answered', 'mine', 'waiting')
    cases = cases.order_by('created_at' if working else '-updated_at')
    page = _page(request, cases)
    unread = {case.pk for case in page.object_list
              if case.last_user_message_at and (case.my_seen_at is None
                                                or case.last_user_message_at > case.my_seen_at)}

    active = Case.objects.filter(status__in=Case.ACTIVE, is_spam=False)
    by_kind = dict(active.values_list('kind').annotate(n=Count('pk')))
    kinds = [(key, label, by_kind.get(key, 0)) for key, label in Case.KIND_CHOICES]

    return render(request, 'staff/queue.html', _console_context(
        request, section='queue', page=page, show=show, kind=kind, query=query,
        kinds=kinds, status_filters=STATUS_FILTERS, main_filters=MAIN_FILTERS,
        show_label=dict(STATUS_FILTERS).get(show, ''), unread=unread, working=working,
        workers=_workers(), seen_version='{0:.6f}'.format(timezone.now().timestamp()),
        saved_views=_saved_views(request, 'staff:home'), view_page='staff:home',
        counts={
            'attention': alerts.for_attention(request.user).count(),
            'active': active.count(),
            'new': active.filter(first_response_at__isnull=True).count(),
            'answered': _answered(_with_unseen(active, request.user)).count(),
            'mine': active.filter(assignee=request.user).count(),
            'unassigned': active.filter(assignee__isnull=True).count(),
            'oldest': active.aggregate(oldest=Min('created_at'))['oldest'],
        }))


def _case_facts(case):
    """Panel beside the timeline, depends on the kind of request."""
    facts = {}
    if case.kind == Case.AGENCY and case.requester is not None:
        profile = UserProfile.objects.filter(user=case.requester).first()
        bundles = Bundle.objects.filter(user=case.requester)
        facts = {
            'current': profile.agency if profile else '',
            'current_label': actions.agency_label(profile.agency) if profile else '',
            'requested': case.data.get('requested', ''),
            'requested_label': actions.agency_label(case.data.get('requested', '')),
            'bundle_count': bundles.count(),
            'already': profile is not None and profile.agency == case.data.get('requested'),
        }
    if case.bundle is not None:
        facts['bundle'] = case.bundle
        facts['bundle_status'] = case.bundle.get_status()
        facts['requirements'] = preflight.requirements(case.bundle)
        facts['runs'] = ValidationRun.objects.filter(bundle=case.bundle).order_by('-requested_at')[:3]
    return facts


PLACEHOLDER_HELP = '{name} {username} {bundle} {case} {staff}'


def _case_replies(case, staff_member):
    """Saved replies for this case, starting with a draft from the latest validate findings."""
    from . import outreach
    replies = []
    if case.bundle is not None:
        draft = outreach.findings_draft(case.bundle, greeting_name=(case.requester.first_name if case.requester else ''))
        if draft:
            replies.append(('Explain the latest validate findings', draft))
    replies += [(r.title, fill_placeholders(r.body, case, staff_member)) for r in SavedReply.objects.all()]
    return replies


def fill_placeholders(text, case, staff_member):
    """Saved reply placeholders for a case. Don't use str.format on user text."""
    values = {
        '{name}': (case.requester.first_name if case.requester else '') or case.requester_name or 'there',
        '{username}': case.requester_username or '',
        '{bundle}': case.bundle.name if case.bundle else case.data.get('bundle_name', ''),
        '{case}': '#{0}'.format(case.pk),
        '{staff}': staff_member.get_full_name() or staff_member.username,
    }
    for key, value in values.items():
        text = text.replace(key, value)
    return text


def _saved_views(request, page):
    from .models import SavedView
    return (SavedView.objects.filter(page=page).filter(Q(user=request.user) | Q(shared=True))
            .select_related('user'))


def _workers():
    from django.contrib.auth.models import Permission
    perm = Permission.objects.filter(content_type__app_label='staff', codename=roles.WORK_CASES)
    return (User.objects.filter(is_active=True)
            .filter(Q(is_superuser=True) | Q(groups__permissions__in=perm)).distinct()
            .order_by('username'))


# For each review button: (title, message box text, message required, button, what the owner gets).
STAGE_WINDOWS = {
    Case.STAGE_UNDER_REVIEW: ('Start the review', 'Message to the owner (optional)', False, 'Start review',
                              'The owner is told someone is reviewing their bundle.'),
    Case.STAGE_CHANGES: ('Ask for changes', 'What needs to change', True, 'Ask for changes',
                         'The owner receives your message and sees the open liens on their bundle page.'),
    Case.STAGE_ACCEPTED: ('Accept this bundle', 'Message to the owner (optional)', False, 'Accept',
                          'The owner is told it was accepted. Next comes the release.'),
    Case.STAGE_NOT_ACCEPTED: ('Not accepted', 'Tell the owner why', True, 'Close as not accepted',
                              'This ends the review. The owner receives your message.'),
    'move_back': ('Move the review back', 'Why it is reopened (optional, sent to the owner)', False,
                  'Move back to Under review', 'The review is open again at Under review.'),
}


@console_view()
def case_detail(request, pk):
    case = get_object_or_404(Case.objects.select_related('requester', 'assignee', 'bundle', 'closed_by'),
                             pk=pk)
    seen_before = (CaseRead.objects.filter(case=case, user=request.user, role=CaseRead.STAFF)
                   .values_list('seen_at', flat=True).first())
    comms.mark_seen(case, request.user)
    from . import next_step
    checklist_open = review.checklist_open(case) if case.kind == Case.SUBMISSION else []
    open_release = (case.releases.filter(released_at__isnull=True).first()
                    if case.kind == Case.SUBMISSION else None)
    reachable = actions._reachable(case)
    unread = bool(case.last_user_message_at and (seen_before is None or case.last_user_message_at > seen_before))
    step = next_step.build(case, request.user, roles.has(request.user, roles.WORK_CASES), reachable,
                           unread=unread, checklist_open=checklist_open, open_release=open_release)
    # Already in the Next step box, so More actions doesn't repeat them.
    offered = set()
    for item in step['actions']:
        offered.add((item.get('post') or {}).get('action', ''))
        offered.add(item.get('modal', ''))
    final_stage = case.stage in (Case.STAGE_ACCEPTED, Case.STAGE_RELEASED, Case.STAGE_NOT_ACCEPTED)
    stage_windows = [(key, STAGE_WINDOWS[key]) for key in step['stages'] if key in STAGE_WINDOWS]
    if final_stage and case.kind == Case.SUBMISSION:
        stage_windows.append(('move_back', STAGE_WINDOWS['move_back']))
    rollout_context = {}
    if case.kind == Case.UPDATE:
        from . import rollout
        from .models import Broadcast
        rollout_context = {
            'rollout_items': rollout.checklist_for(case),
            'rollout_live': rollout.is_live(case),
            'rollout_notice': rollout.notice_for(case),
            'rollout_draft': Broadcast.objects.filter(pk=(case.data or {}).get('notice_broadcast') or 0).first(),
            'rollout_banner_url': rollout.banner_url(case),
        }
    return render(request, 'staff/case.html', _console_context(
        request, section='queue', help_topic='case', case=case, seen_before=seen_before, **rollout_context,
        page_title='#{0} {1}'.format(case.pk, case.title),
        next_step=step, offered=offered, stage_windows=stage_windows, final_stage=final_stage,
        # When this page was made: anything after it was not on screen.
        seen_version='{0:.6f}'.format(timezone.now().timestamp()),
        saved_replies=_case_replies(case, request.user),
        workers=_workers(),
        checklist=review.checklist_for(case) if case.kind == Case.SUBMISSION else [],
        checklist_open=checklist_open,
        requester_closed=bool(case.requester_id and not case.requester.is_active),
        reachable=reachable,
        releases=case.releases.all() if case.kind == Case.SUBMISSION else [],
        open_release=open_release,
        changes=review.changes_since_last(case.bundle) if case.bundle else None,
        has_account=case.requester_id is not None,
        status_modals=[
            ('waiting', 'Wait for the user', 'What do you need from them?', False, 'Mark as waiting',
             'If you write something, they are notified and emailed. Leave it empty to change the status quietly.'),
            ('resolved', 'Resolve this request', 'Message to the user', False, 'Resolve',
             'They are notified and emailed either way; your message is included if you write one.'),
            ('declined', 'Decline this request', 'Tell them why', True, 'Decline and tell them',
             'They are notified and emailed with your message. Nobody is declined without a reason.'),
        ],
        inbound=bool(comms.inbound_address()),
        notes=case.notes.select_related('author'),
        liens=case.liens.select_related('created_by', 'closed_by'),
        facts=_case_facts(case),
        data_rows=_data_rows(case),
        status_choices=Case.STATUS_CHOICES,
        stage_choices=Case.STAGE_CHOICES,
        agency_choices=UserProfile.AGENCY_CHOICES,
        other_cases=(Case.objects.filter(requester=case.requester).exclude(pk=case.pk)[:5]
                     if case.requester_id else []),
    ))


def _data_rows(case):
    """The facts ELSA recorded on a request, as label and value."""
    data = case.data or {}
    if case.kind == Case.UPDATE:
        from . import watchers
        source = watchers.SOURCES.get(data.get('source'))
        rows = [('What', source.label if source else data.get('source', ''))]
        if data.get('version'):
            rows.append(('New', data['version']))
        if data.get('current'):
            rows.append(('ELSA uses', data['current']))
        return rows
    return [(k.replace('_', ' ').capitalize(), v) for k, v in sorted(data.items())
            if k not in ('requested', 'current') and not isinstance(v, (dict, list))]


# If the page is older than the case, these could repeat what someone else just did, so they wait.
GUARDED_ACTIONS = ('reply', 'status', 'stage', 'approve_agency', 'decline_agency')


def changed_since(case, seen_version, user):
    """Describes a conflicting change made since ``seen_version`` was loaded, or ''.

    Conflicting means another staff member's reply or decision, or a new answer from the user.
    """
    from datetime import datetime, timezone as dt_timezone
    try:
        since = datetime.fromtimestamp(float(seen_version), tz=dt_timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return ''
    if case.last_user_message_at and case.last_user_message_at > since:
        return ('{0} answered after you opened this page. Read their answer above, then send again; '
                'your text is kept.').format(case.requester_label())
    clash = (AuditEntry.objects.filter(target_type='case', target_id=str(case.pk), created_at__gt=since,
                                       action__in=('case.reply', 'case.status', 'case.stage', 'user.agency'))
             .exclude(actor=user).order_by('-created_at').first())
    if clash is None:
        clash = (AuditEntry.objects.filter(target_type='user', target_id=str(case.requester_id or 0),
                                           action='user.agency', created_at__gt=since)
                 .exclude(actor=user).first())
    if clash is None:
        return ''
    what = {'case.reply': 'replied', 'case.status': 'changed the status', 'case.stage': 'moved the review',
            'user.agency': 'changed the agency'}.get(clash.action, 'changed this request')
    return '{0} {1} after you opened this page. Read the latest above, then send again; your text is kept.'.format(
        clash.actor_username, what)


@console_view()
@require_POST
def case_action(request, pk):
    case = get_object_or_404(Case, pk=pk)
    action = request.POST.get('action')
    if action in GUARDED_ACTIONS:
        clash = changed_since(case, request.POST.get('seen_version'), request.user)
        if clash:
            messages.warning(request, clash)
            return redirect('staff:case', pk=case.pk)
    try:
        if action == 'claim':
            actions.claim(request, case)
            messages.success(request, 'It is yours now.')
        elif action == 'unassign':
            actions.unassign(request, case)
            messages.success(request, 'Unassigned. It is back with the whole team.')
        elif action == 'note':
            actions.add_note(request, case, _text(request, 'body'))
            messages.success(request, 'Note added.')
        elif action == 'reply':
            actions.reply(request, case, _text(request, 'body'), waiting=request.POST.get('waiting') == '1',
                          then=request.POST.get('then') or None)
            messages.success(request, 'Reply sent to {0}.'.format(case.requester_email))
        elif action == 'status':
            actions.set_status(request, case, request.POST.get('status'), _text(request, 'reason', REASON_LIMIT),
                               message=_text(request, 'message'))
            messages.success(request, 'Status changed.')
        elif action == 'stage':
            result = actions.set_stage(request, case, request.POST.get('stage'), _text(request, 'body'),
                                       accept_anyway=request.POST.get('accept_anyway') == '1',
                                       move_back=request.POST.get('move_back') == '1')
            if result == 'proposed':
                messages.info(request, 'Acceptance proposed. A second reviewer has to confirm it.')
            else:
                messages.success(request, 'Review stage updated.')
        elif action == 'approve_agency':
            if case.kind != Case.AGENCY or case.requester is None or not case.is_active:
                raise actions.ActionError('This is not an open agency request.')
            actions.change_agency(request, case.requester, case.data.get('requested'),
                                  _text(request, 'reason', REASON_LIMIT), case=case, message=_text(request, 'message'))
            messages.success(request, 'Agency changed and {0} was told.'.format(case.requester.username))
        elif action == 'decline_agency':
            if case.kind != Case.AGENCY or not case.is_active:
                raise actions.ActionError('This is not an open agency request.')
            actions.decline_agency(request, case, _text(request, 'body'))
            messages.success(request, 'Declined and the user was told why.')
        elif action == 'add_lien':
            actions.add_lien(request, case, _text(request, 'description'), _text(request, 'file_path', 500),
                             line=request.POST.get('line'))
            messages.success(request, 'Lien added.')
        elif action == 'assign':
            if not request.POST.get('assignee'):
                raise actions.ActionError('Choose who should take it.')
            person = User.objects.filter(pk=_pk(request.POST.get('assignee'))).first()
            actions.assign(request, case, person)
            messages.success(request, 'Assigned to {0}.'.format(person.username))
        elif action == 'spam':
            actions.mark_spam(request, case, block=request.POST.get('block') == '1')
            messages.success(request, 'Marked as spam.')
        elif action == 'not_spam':
            actions.not_spam(request, case)
            messages.success(request, 'Back in the queue.')
        elif action == 'checklist':
            actions.set_checklist(request, case, set(request.POST.getlist('done')))
            messages.success(request, 'Checklist saved.')
        elif action == 'prepare_notice':
            from . import rollout
            draft = rollout.prepare_notice(request, case)
            messages.success(request, 'Draft email prepared. Read it, change anything, send yourself a test, then '
                                      'send it. Nothing has gone out.')
            return redirect('staff:broadcast', pk=draft.pk)
        elif action == 'lien_status':
            lien = get_object_or_404(Lien, pk=_pk(request.POST.get('lien')), case=case)
            actions.close_lien(request, lien, request.POST.get('status'))
            messages.success(request, 'Lien updated.')
        else:
            raise actions.ActionError('Unknown action.')
    except actions.ActionError as error:
        messages.error(request, str(error))
    if request.POST.get('return_to') == 'files' and case.bundle_id:
        path = (request.POST.get('file_path') or '')[:500]
        return HttpResponseRedirect('{0}?{1}'.format(reverse('staff:bundle_files', args=[case.bundle_id]),
                                                     urlencode({'path': path})))
    # Back to the part of the page the person was working in.
    anchor = '#rollout-heading' if action == 'checklist' and case.kind == Case.UPDATE else RETURN_ANCHORS.get(action, '')
    return HttpResponseRedirect(reverse('staff:case', args=[case.pk]) + anchor)


RETURN_ANCHORS = {
    'note': '#conversation-end', 'reply': '#conversation-end',
    'add_lien': '#liens-heading', 'lien_status': '#liens-heading',
    'checklist': '#review-heading', 'assign': '#handling-heading',
}


BULK_LIMIT = 100


@console_view()
@require_POST
def bulk_action(request):
    """One action on several requests. Anything that can't be done is skipped and reported."""
    action = request.POST.get('bulk')
    ids = [int(i) for i in request.POST.getlist('case') if i.isdecimal()][:BULK_LIMIT]
    # Rebuilt from parsed parameters so raw text never goes in a header.
    from django.http import QueryDict
    params = QueryDict(request.POST.get('return_query', '')[:500], mutable=True)
    params.pop('page', None)
    encoded = params.urlencode()
    back = reverse('staff:home') + ('?' + encoded if encoded else '')
    if not ids or action not in ('assign', 'resolve', 'spam'):
        messages.error(request, 'Tick some requests and choose what to do.')
        return HttpResponseRedirect(back)
    person = None
    if action == 'assign':
        person = User.objects.filter(pk=_pk(request.POST.get('assignee'))).first()
        if person is None:
            messages.error(request, 'Choose who should take them.')
            return HttpResponseRedirect(back)
    done, skipped = 0, []
    for case in Case.objects.filter(pk__in=ids).order_by('pk'):
        if changed_since(case, request.POST.get('seen_version'), request.user):
            skipped.append('#{0} changed after you loaded the list'.format(case.pk))
            continue
        try:
            if action == 'assign':
                actions.assign(request, case, person)
            elif action == 'resolve':
                actions.set_status(request, case, Case.RESOLVED)
            elif action == 'spam':
                if case.kind not in (Case.CONTACT, Case.FEEDBACK):
                    raise actions.ActionError('only contact messages and feedback can be spam')
                actions.mark_spam(request, case, block=False)
            done += 1
        except actions.ActionError as error:
            skipped.append('#{0}: {1}'.format(case.pk, str(error).rstrip('.')))
    if done:
        messages.success(request, '{0} request{1} updated.'.format(done, '' if done == 1 else 's'))
    if skipped:
        messages.warning(request, 'Skipped {0}: {1}.'.format(len(skipped), '; '.join(skipped[:8])
                                                            + ('; ...' if len(skipped) > 8 else '')))
    return HttpResponseRedirect(back)


# --- Users ---

@console_view()
def users(request):
    query = _query(request)
    state = request.GET.get('state', '')
    people = (User.objects.select_related('userprofile')
              .annotate(bundle_count=Count('bundle', distinct=True))
              .order_by('-last_login', 'username'))
    if query:
        people = people.filter(Q(username__icontains=query) | Q(email__icontains=query)
                               | Q(first_name__icontains=query) | Q(last_name__icontains=query))
    if state == 'disabled':
        people = people.filter(is_active=False)
    elif state == 'staff':
        people = people.filter(Q(is_superuser=True) | Q(groups__name__in=roles.GROUP_NAMES)).distinct()
    elif state == 'open_request':
        people = people.filter(staff_cases__status__in=Case.ACTIVE, staff_cases__is_spam=False).distinct()
    elif state == 'unconfirmed':
        people = people.filter(is_active=False, last_login__isnull=True,
                               userprofile__email_verified_at__isnull=True)
    elif state == 'locked':
        people = people.filter(userprofile__otp_locked_until__gt=timezone.now())
    else:
        state = ''
    return render(request, 'staff/users.html', _console_context(
        request, section='users', page=_page(request, people), query=query, state=state,
        saved_views=_saved_views(request, 'staff:users'), view_page='staff:users'))


@console_view()
def user_detail(request, pk):
    person = get_object_or_404(User.objects.select_related('userprofile'), pk=pk)
    profile = UserProfile.objects.filter(user=person).first()
    return render(request, 'staff/user.html', _console_context(
        request, section='users', help_topic='user', page_title=person.username, person=person, profile=profile,
        person_role=roles.role_label(person),
        bundles=Bundle.objects.filter(user=person).order_by('-updated_at'),
        passkey_count=person.passkeys.count(),
        passkeys=person.passkeys.all(),
        agency_request=Case.objects.filter(kind=Case.AGENCY, requester=person, status__in=Case.ACTIVE).first(),
        locked=bool(profile and profile.otp_locked_until and profile.otp_locked_until > timezone.now()),
        unconfirmed=bool(profile and not person.is_active and profile.email_verified_at is None
                         and person.last_login is None),
        can_view_as=(person.is_active and not person.is_superuser and not roles.is_member(person)
                     and person.pk != request.user.pk),
        sessions=UserSession.objects.filter(user=person)[:10],
        events=AccountEvent.objects.filter(user=person)[:15],
        cases=Case.objects.filter(requester=person)[:10],
        history=AuditEntry.objects.filter(target_type='user', target_id=str(person.pk))[:15],
        agency_choices=UserProfile.AGENCY_CHOICES,
        is_self=person.pk == request.user.pk,
        protected=(person.is_superuser or roles.is_member(person)) and not request.user.is_superuser,
    ))


@console_view()
@require_POST
def user_action(request, pk):
    person = get_object_or_404(User, pk=pk)
    action = request.POST.get('action')
    reason = _text(request, 'reason', REASON_LIMIT)
    try:
        if action == 'agency':
            agency = request.POST.get('agency')
            pending = Case.objects.filter(kind=Case.AGENCY, requester=person, status__in=Case.ACTIVE).first()
            if pending is not None and pending.data.get('requested') == agency:
                # Approve their request so it closes and they only hear once.
                actions.change_agency(request, person, agency, reason, case=pending)
                messages.success(request, 'Agency changed, and their request #{0} is resolved.'.format(pending.pk))
            else:
                actions.change_agency(request, person, agency, reason)
                if pending is not None:
                    messages.warning(request, 'Agency changed. Their request #{0} asked for {1}; it is still open '
                                              'for you to answer.'.format(pending.pk, pending.data.get('requested')))
                else:
                    messages.success(request, 'Agency changed.')
        elif action == 'disable':
            actions.set_active(request, person, False, reason)
            messages.success(request, '{0} is disabled and signed out everywhere.'.format(person.username))
        elif action == 'enable':
            actions.set_active(request, person, True, reason)
            messages.success(request, '{0} can sign in again.'.format(person.username))
        elif action == 'sign_out':
            actions.sign_out_everywhere(request, person, reason)
            messages.success(request, '{0} was signed out everywhere.'.format(person.username))
        elif action == 'unlock':
            actions.unlock_sign_in(request, person, reason)
            messages.success(request, '{0} can try signing in again.'.format(person.username))
        elif action == 'remind_confirm':
            actions.remind_to_confirm(request, person)
            messages.success(request, 'Reminder sent to {0}.'.format(person.email))
        elif action == 'remove_passkey':
            from friends.models import Passkey
            passkey = get_object_or_404(Passkey, pk=_pk(request.POST.get('passkey')), user=person)
            actions.remove_passkey(request, passkey, reason)
            messages.success(request, 'Passkey removed and {0} was told.'.format(person.username))
        elif action == 'message':
            case = actions.start_conversation(request, person, _text(request, 'subject', 150), _text(request, 'body'))
            messages.success(request, 'Sent to {0}. Their answer appears on this conversation.'.format(person.username))
            return redirect('staff:case', pk=case.pk)
        elif action == 'view_as':
            actions.start_view_as(request, person, reason)
            return redirect('friends:bundle_hub')
        else:
            raise actions.ActionError('Unknown action.')
    except actions.ActionError as error:
        messages.error(request, str(error))
    return redirect('staff:user', pk=person.pk)


# --- Bundles ---

@console_view()
def bundles(request):
    query = _query(request)
    bundle_type = request.GET.get('type', '')
    submitted = request.GET.get('submitted', '')
    found = Bundle.objects.select_related('user').order_by('-updated_at', '-pk')
    if query:
        found = found.filter(Q(name__icontains=query) | Q(user__username__icontains=query))
    if bundle_type in ('Archive', 'External'):
        found = found.filter(bundle_type=bundle_type)
    else:
        bundle_type = ''
    if submitted == '1':
        found = found.filter(submitted_at__isnull=False)
    else:
        submitted = ''
    labels = request.GET.get('labels', '')
    if labels == 'failed':
        found = label_upgrades.failed_bundles(found)
    elif labels == 'behind':
        found = found.filter(label_revision__lt=label_migrations.head())
    else:
        labels = ''
    page = _page(request, found)
    failed_here = set(label_upgrades.failed_bundles(
        Bundle.objects.filter(pk__in=[b.pk for b in page.object_list])).values_list('pk', flat=True))
    for item in page.object_list:
        item.label_update_failed = item.pk in failed_here
    return render(request, 'staff/bundles.html', _console_context(
        request, section='bundles', page=page, query=query, labels=labels,
        failed_count=label_upgrades.failed_bundles().count(),
        bundle_type=bundle_type, submitted=submitted,
        saved_views=_saved_views(request, 'staff:bundles'), view_page='staff:bundles'))


@console_view()
def bundle_detail(request, pk):
    bundle = get_object_or_404(Bundle.objects.select_related('user'), pk=pk)
    directory = bundle.directory()
    return render(request, 'staff/bundle.html', _console_context(
        request, section='bundles', page_title=bundle.name, bundle=bundle,
        bundle_status=bundle.get_status(),
        requirements=preflight.requirements(bundle),
        runs=ValidationRun.objects.filter(bundle=bundle).order_by('-requested_at')[:10],
        cases=Case.objects.filter(bundle=bundle),
        changes=review.changes_since_last(bundle),
        snapshots=bundle.snapshots.count(),
        releases=bundle.releases.all(),
        projects=bundle.projects.all(),
        runs_queued=ValidationRun.objects.filter(bundle=bundle, status=ValidationRun.STATUS_QUEUED),
        on_disk=os.path.isdir(directory),
        directory=directory,
        history=AuditEntry.objects.filter(target_type='bundle', target_id=str(bundle.pk))[:10],
        label_pending=[m for m in label_migrations.pending(bundle) if m.applies_to(bundle)],
        label_failed=label_upgrades.failed_update(bundle),
        label_upgrades=bundle.upgrades.all()[:20],
        label_undo=bundle.upgrades.filter(status='applied').first(),
    ))


@console_view(roles.REVIEW_BUNDLES)
def bundle_label_preview(request, pk):
    """What the pending label updates would change, worked out on a copy."""
    bundle = get_object_or_404(Bundle.objects.select_related('user'), pk=pk)
    results = label_upgrades.preview_bundle(bundle)
    for result in results:
        result['notes'] = label_upgrades.describe(result['notes'])
        result['lines'] = [(line, _diff_class(line)) for line in result['diff'].splitlines()]
    return render(request, 'staff/label_preview.html', _console_context(
        request, section='bundles', page_title='Label updates for {}'.format(bundle.name),
        bundle=bundle, results=results))


def _diff_class(line):
    if line.startswith(('+++', '---')):
        return 'file'
    if line.startswith('@@'):
        return 'hunk'
    return {'+': 'add', '-': 'del'}.get(line[:1], '')


def _inside_archive(path):
    root = os.path.realpath(settings.ARCHIVE_DIR)
    real = os.path.realpath(path)
    return real == root or real.startswith(root + os.sep)


@console_view()
@limited('download')
def bundle_download(request, pk):
    """Bundle as a .tar.gz for review, in a temp file that's deleted when the response closes."""
    bundle = get_object_or_404(Bundle, pk=pk)
    directory = bundle.directory()
    if not _inside_archive(directory) or not os.path.isdir(directory):
        raise Http404('The bundle folder is missing.')

    response = bundle_tarball_response(directory)
    audit.record(request, 'bundle.download', bundle)
    return response


def _raw_report(run):
    """Report file for a run, if it's still there and in the right place."""
    from build import validate_runner
    path = run.report_path or ''
    if not path:
        return None
    try:
        root = os.path.realpath(validate_runner.reports_dir())
    except Exception:
        return None
    real = os.path.realpath(path)
    if not real.startswith(root + os.sep) or not os.path.isfile(real):
        return None
    return real


def _with_file_links(bundle, findings):
    """Mark findings whose label is a file in the bundle so the console can open it."""
    from . import review
    folder = review.bundle_folder(bundle)
    rows = []
    for finding in findings:
        finding = dict(finding)
        label = (finding.get('label') or '').replace('\\', '/')
        link = ''
        if folder and label:
            if label.startswith(folder + '/'):
                label = label[len(folder) + 1:]
            if review.resolve(bundle, label):
                link = label
        finding['file_link'] = link
        rows.append(finding)
    return rows


@console_view()
def validation_report(request, pk_run):
    """Validate run in the console, with findings linked to the bundle's files."""
    run = get_object_or_404(ValidationRun.objects.select_related('bundle'), pk=pk_run)
    findings = run.findings or []
    return render(request, 'staff/validation_report.html', _console_context(
        request, section='bundles', page_title='Validate run {0}: {1}'.format(run.pk, run.bundle.name),
        run=run, bundle=run.bundle,
        sections=[('Errors', _with_file_links(run.bundle, validate_report.errors(findings))),
                  ('Warnings', _with_file_links(run.bundle, validate_report.warnings(findings)))],
        by_type=sorted(validate_report.summarise_types(findings).items(), key=lambda item: (-item[1], item[0])),
        raw_available=_raw_report(run) is not None))


@console_view()
@limited('download')
def validation_raw(request, pk_run):
    run = get_object_or_404(ValidationRun.objects.select_related('bundle'), pk=pk_run)
    path = _raw_report(run)
    if path is None:
        raise Http404('The report file is not available.')
    audit.record(request, 'bundle.file', run.bundle, changes={'validate_report': run.pk})
    return FileResponse(open(path, 'rb'), as_attachment=True,
                        filename='validate_run_{0}{1}'.format(run.pk, os.path.splitext(path)[1] or '.txt'))


# --- Audit and health ---

@console_view(roles.VIEW_AUDIT)
def audit_log(request):
    entries = AuditEntry.objects.all()
    actor = _query(request, 'actor', 150)
    action = request.GET.get('action', '')
    if actor:
        entries = entries.filter(actor_username__icontains=actor)
    if action in audit.ACTION_LABELS:
        entries = entries.filter(action=action)
    else:
        action = ''
    return render(request, 'staff/audit.html', _console_context(
        request, section='audit', page=_page(request, entries), actor=actor, action=action,
        action_choices=sorted(audit.ACTION_LABELS.items(), key=lambda item: item[1])))


def _disk(path):
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return {'total': usage.total, 'used': usage.used, 'free': usage.free,
            'percent': round(100.0 * usage.used / usage.total) if usage.total else 0}


@console_view()
def health(request):
    now = timezone.now()
    week = now - timedelta(days=7)
    active = Case.objects.filter(status__in=Case.ACTIVE)
    runs = ValidationRun.objects.all()

    validate_version = ''
    try:
        from build import validate_release
        validate_version = validate_release.release_version(getattr(settings, 'VALIDATE_HOME', '')) or ''
    except Exception:
        validate_version = ''

    security = None
    if roles.has(request.user, roles.MANAGE_ACCOUNTS):
        from . import selfcheck
        security = selfcheck.run()

    return render(request, 'staff/health.html', _console_context(
        request, section='health', security=security,
        archive_disk=_disk(settings.ARCHIVE_DIR),
        users_total=User.objects.count(),
        users_active_week=User.objects.filter(last_login__gte=week).count(),
        users_disabled=User.objects.filter(is_active=False).count(),
        bundles_total=Bundle.objects.count(),
        bundles_week=Bundle.objects.filter(created_at__gte=week).count(),
        bundles_submitted=Bundle.objects.filter(submitted_at__isnull=False).count(),
        cases_open=active.count(),
        cases_stale=active.filter(created_at__lte=now - timedelta(days=7)).count(),
        cases_oldest=active.aggregate(oldest=Min('created_at'))['oldest'],
        runs_queued=runs.filter(status=ValidationRun.STATUS_QUEUED).count(),
        runs_running=runs.filter(status=ValidationRun.STATUS_RUNNING).count(),
        runs_failed_week=runs.filter(status=ValidationRun.STATUS_FAILED, requested_at__gte=week).count(),
        runs_week=runs.filter(requested_at__gte=week).count(),
        validate_version=validate_version,
        validate_blocks=getattr(settings, 'VALIDATE_BLOCKS_SUBMISSION', False),
        debug=settings.DEBUG,
        admin_sessions=sessions.active_now(),
        assistant_on=actions.assistant_enabled(),
        assistant_installed=django_apps.is_installed('assistant'),
        queued_runs=runs.filter(status=ValidationRun.STATUS_QUEUED).select_related('bundle')[:20],
        inbound_on=bool(comms.inbound_address()),
        mail_logging=settings.EMAIL_BACKEND == 'staff.mail_log.LoggingBackend',
    ))
