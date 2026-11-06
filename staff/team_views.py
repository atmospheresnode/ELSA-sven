# -*- coding: utf-8 -*-
"""Admin Console pages for helping users and team work: stalled bundles, validation insights,
knowledge notes, targets and workload, settings, saved views, dossiers, updates, jump box.
"""
from __future__ import unicode_literals

import io
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.models import User
from django.db.models import Q
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from build.models import Bundle

from . import actions, audit, dbtext, outreach, roles, snapshots, targets
from .decorators import console_view, limited
from .models import (AuditEntry, Case, CaseNote, CasePresence, ConsoleSetting, KnowledgeNote, Lien, Release,
                     SavedView, StaffPreference)
from .views import _console_context, _pk, _query, _text, _workers


def _act(request, call, success, *args, **kwargs):
    try:
        result = call(request, *args, **kwargs)
    except actions.ActionError as error:
        messages.error(request, str(error))
        return None
    if success:
        messages.success(request, success)
    return result if result is not None else True


# --- Stalled bundles and insights ---

@console_view()
def stalled(request):
    from .models import SavedReply
    if request.method == 'POST':
        ids = [int(i) for i in request.POST.getlist('bundle') if i.isdecimal()]
        if not ids:
            messages.error(request, 'Tick at least one bundle first.')
            return redirect('staff:stalled')
        bundles = Bundle.objects.filter(pk__in=ids).select_related('user')
        result = _act(request, outreach.nudge, None, bundles, _text(request, 'subject', 150), _text(request, 'body'))
        if result and result is not True:
            sent, skipped = result
            if sent:
                messages.success(request, 'Sent to {0} owner{1}.'.format(sent, '' if sent == 1 else 's'))
            if skipped:
                messages.warning(request, 'Skipped: {0}.'.format('; '.join(skipped[:8])))
        return redirect('staff:stalled')
    lists = snapshots.stalled_lists()
    return render(request, 'staff/stalled.html', _console_context(
        request, section='stalled', lists=lists, computed_at=lists['computed_at'],
        saved_replies=SavedReply.objects.all(), quiet_days=outreach.NUDGE_QUIET_DAYS))


@console_view()
def insights(request):
    rows, computed_at = snapshots.insights()
    return render(request, 'staff/insights.html', _console_context(
        request, section='insights', rows=rows, computed_at=computed_at))


@console_view()
@require_POST
def refresh_snapshot(request, key):
    if key not in snapshots.COMPUTE:
        raise Http404
    if snapshots.refresh(request, key):
        messages.success(request, 'Refreshed.')
    else:
        messages.info(request, 'This list was refreshed a moment ago. Try again in a couple of minutes.')
    return redirect('staff:stalled' if key == 'stalled' else 'staff:insights')


# --- Knowledge notes ---

@console_view()
def knowledge(request):
    query = _query(request)
    notes = KnowledgeNote.objects.all()
    if query:
        notes = notes.filter(Q(title__icontains=query) | Q(body__icontains=query) | Q(topic__icontains=query))
    editing = KnowledgeNote.objects.filter(pk=_pk(request.GET.get('edit'))).first()
    if request.method == 'POST':
        note = KnowledgeNote.objects.filter(pk=_pk(request.POST.get('id'))).first()
        if request.POST.get('action') == 'delete' and note is not None:
            _act(request, actions.delete_knowledge, 'Deleted.', note)
        else:
            _act(request, actions.save_knowledge, 'Saved.', note, _text(request, 'title', 200),
                 _text(request, 'topic', 100), _text(request, 'body', 20000), request.POST.get('pinned') == '1')
        return redirect('staff:knowledge')
    return render(request, 'staff/knowledge.html', _console_context(
        request, section='knowledge', notes=notes, query=query, editing=editing,
        topics=sorted(set(KnowledgeNote.objects.exclude(topic='').values_list('topic', flat=True)))))


@console_view()
def knowledge_note(request, pk):
    note = get_object_or_404(KnowledgeNote, pk=pk)
    return render(request, 'staff/knowledge_note.html', _console_context(request, section='knowledge', page_title=note.title,
                                                                            note=note))


# --- Targets, workload, away ---

@console_view()
def workload(request):
    if request.method == 'POST' and request.POST.get('action') == 'hand_over':
        person = User.objects.filter(pk=_pk(request.POST.get('to'))).first()
        result = _act(request, actions.hand_over, None, person)
        if result and result is not True:
            messages.success(request, '{0} request{1} handed to {2}.'.format(result, '' if result == 1 else 's',
                                                                             person.username))
        return redirect('staff:workload')
    month = timezone.now() - timedelta(days=30)
    rows = []
    for person in _workers():
        mine = Case.objects.filter(assignee=person, status__in=Case.ACTIVE, is_spam=False)
        answered = Case.objects.filter(assignee=person, first_response_at__gte=month)
        hours = [(c.first_response_at - c.created_at).total_seconds() / 3600 for c in answered]
        pref = StaffPreference.for_user(person)
        rows.append({'person': person, 'open': mine.count(), 'waiting': mine.filter(status=Case.WAITING).count(),
                     'late': sum(1 for c in mine if targets.state(c) == 'late'),
                     'median': round(sorted(hours)[len(hours) // 2], 1) if hours else None,
                     'away': pref.away_until if pref.away_until and pref.away_until >= timezone.localdate() else None})
    unassigned = Case.objects.filter(assignee__isnull=True, status__in=Case.ACTIVE, is_spam=False)
    return render(request, 'staff/workload.html', _console_context(
        request, section='workload', rows=rows, unassigned=unassigned.count(),
        unassigned_late=sum(1 for c in unassigned if targets.state(c) == 'late'),
        target_days=ConsoleSetting.get('first_reply_days'), workers=_workers()))


@console_view(roles.MANAGE_ACCOUNTS)
def console_settings(request):
    if request.method == 'POST':
        changed, failed = 0, False
        for key, (kind, _low, _high) in actions.SETTING_RULES.items():
            current = ConsoleSetting.get(key)
            if kind == 'bool':
                raw = '1' if request.POST.get(key) == '1' else '0'
                if (raw == '1') == bool(current):
                    continue
            else:
                raw = (request.POST.get(key) or '').strip()
                if not raw or raw == str(current):
                    continue
            if _act(request, actions.change_setting, None, key, raw) is None:
                failed = True
            else:
                changed += 1
        if changed and not failed:
            messages.success(request, 'Settings saved.')
        elif not changed and not failed:
            messages.info(request, 'Nothing changed.')
        return redirect('staff:settings')
    return render(request, 'staff/settings.html', _console_context(
        request, section='settings', values={key: ConsoleSetting.get(key) for key in actions.SETTING_RULES}))


# --- Saved views ---

@console_view()
@require_POST
def saved_views(request):
    if request.POST.get('action') == 'delete':
        view = get_object_or_404(SavedView, pk=_pk(request.POST.get('id')), user=request.user)
        view.delete()
        messages.success(request, 'Saved view removed.')
        return HttpResponseRedirect(reverse(view.page))
    page = request.POST.get('page')
    if page not in dict(SavedView.PAGES):
        raise Http404('Unknown page.')
    name = ' '.join((request.POST.get('name') or '').split())[:80]
    # Parsed and re-encoded: a saved view is only ever a query string.
    params = QueryDict((request.POST.get('query') or '')[:500], mutable=True)
    params.pop('page', None)
    shared = request.POST.get('shared') == '1' and roles.has(request.user, roles.WORK_CASES)
    if not name:
        messages.error(request, 'Give the view a name.')
    elif dbtext.unstorable(name):
        messages.error(request, 'ELSA cannot save emoji such as {0} yet. Please choose a name without them.'.format(
            dbtext.unstorable(name)))
    elif SavedView.objects.filter(user=request.user).count() >= 50:
        messages.error(request, 'You have 50 saved views already; remove one first.')
    else:
        SavedView.objects.create(user=request.user, name=name, page=page, query=params.urlencode(), shared=shared)
        messages.success(request, 'Saved "{0}".'.format(name))
    return HttpResponseRedirect(reverse(page) + ('?' + params.urlencode() if params else ''))


def views_for(request, page):
    return SavedView.objects.filter(page=page).filter(Q(user=request.user) | Q(shared=True)).select_related('user')


# --- Dossiers ---

def _pdf(template, context, filename):
    from xhtml2pdf import pisa
    buffer = io.BytesIO()
    pisa.CreatePDF(src=render_to_string(template, dict(context, generated=timezone.now())), dest=buffer)
    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="{0}"'.format(filename)
    return response


@console_view()
@limited('download')
def case_dossier(request, pk):
    case = get_object_or_404(Case.objects.select_related('requester', 'assignee', 'bundle'), pk=pk)
    entries = AuditEntry.objects.filter(target_type='case', target_id=str(case.pk)).order_by('created_at')
    audit.record(request, 'dossier.export', case)
    from .templatetags.staff_tags import audit_details
    return _pdf('staff/dossier_case.html', {
        'case': case, 'notes': case.notes.select_related('author'), 'liens': case.liens.all(),
        'checklist': case.checklist or {}, 'releases': case.releases.all(),
        'entries': [(e, audit_details(e.changes)) for e in entries],
    }, 'request_{0}.pdf'.format(case.pk))


@console_view()
@limited('download')
def bundle_dossier(request, pk):
    bundle = get_object_or_404(Bundle.objects.select_related('user'), pk=pk)
    from build.models import ValidationRun
    audit.record(request, 'dossier.export', bundle)
    return _pdf('staff/dossier_bundle.html', {
        'bundle': bundle, 'cases': Case.objects.filter(bundle=bundle).order_by('created_at'),
        'liens': Lien.objects.filter(case__bundle=bundle).order_by('created_at'),
        'releases': Release.objects.filter(bundle=bundle), 'runs': ValidationRun.objects.filter(bundle=bundle)
        .order_by('-requested_at')[:10], 'projects': bundle.projects.all(),
    }, 'bundle_{0}.pdf'.format(bundle.pk))


# --- Live updates, presence and the jump box ---

PRESENCE_WINDOW = timedelta(seconds=75)


@console_view(touch=False)
def case_pulse(request, pk):
    """Polled by an open case page. Doesn't count as activity, so a tab can't keep admin mode alive."""
    case = get_object_or_404(Case, pk=pk)
    now = timezone.now()
    CasePresence.objects.update_or_create(case=case, user=request.user, defaults={'seen_at': now})
    try:
        since = timezone.datetime.fromtimestamp(float(request.GET.get('since', '0')), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        since = now
    newer = case.notes.filter(created_at__gt=since).exclude(author=request.user).exclude(kind=CaseNote.EVENT)
    viewers = (CasePresence.objects.filter(case=case, seen_at__gte=now - PRESENCE_WINDOW)
               .exclude(user=request.user).select_related('user'))
    return JsonResponse({
        'new': newer.count(),
        'from_user': newer.filter(kind=CaseNote.MESSAGE).exists(),
        'viewers': [v.user.get_full_name() or v.user.username for v in viewers],
    })


@console_view()
@limited('lookup', json=True)
def jump(request):
    """Jump box suggestions: requests, users and bundles, newest first, up to eight."""
    query = _query(request)
    results = []
    if query:
        number = query.lstrip('#')
        if number.isdecimal():
            case = Case.objects.filter(pk=int(number)).first()
            if case:
                results.append({'label': '#{0} {1}'.format(case.pk, case.title), 'kind': 'Request',
                                'url': reverse('staff:case', args=[case.pk])})
        for case in Case.objects.filter(Q(title__icontains=query) | Q(requester_username__icontains=query)
                                        ).order_by('-updated_at')[:3]:
            results.append({'label': '#{0} {1}'.format(case.pk, case.title), 'kind': 'Request',
                            'url': reverse('staff:case', args=[case.pk])})
        for person in User.objects.filter(Q(username__icontains=query) | Q(email__icontains=query)
                                          | Q(last_name__icontains=query))[:3]:
            results.append({'label': '{0} {1}'.format(person.username, person.get_full_name()).strip(),
                            'kind': 'User', 'url': reverse('staff:user', args=[person.pk])})
        for bundle in Bundle.objects.filter(name__icontains=query).order_by('-updated_at')[:3]:
            results.append({'label': bundle.name, 'kind': 'Bundle', 'url': reverse('staff:bundle', args=[bundle.pk])})
    seen, unique = set(), []
    for item in results:
        if item['url'] not in seen:
            seen.add(item['url'])
            unique.append(item)
    from django.utils.http import urlencode
    return JsonResponse({'results': unique[:8], 'search': reverse('staff:search') + '?' + urlencode({'q': query})})


# --- Outside updates (staff.watchers) ---

CHECK_EVERY = 10 * 60  # limit on "Check now" since each check calls outside sites


@console_view()
def updates(request):
    from friends import throttle
    from . import watchers
    from .models import WatchedSource
    if request.method == 'POST':
        if not roles.has(request.user, roles.WORK_CASES):
            messages.error(request, 'Your role does not allow this.')
        elif throttle.hit('staff-updates-check', CHECK_EVERY) > 1:
            messages.info(request, 'A check ran a few minutes ago. Try again in ten minutes.')
        elif watchers.launch():
            audit.record(request, 'updates.check')
            messages.success(request, 'Checking now. Refresh this page in a minute.')
        else:
            messages.error(request, 'The check could not be started on the server.')
        return redirect('staff:updates')
    stored = {row.key: row for row in WatchedSource.objects.all()}
    cases = Case.objects.filter(kind=Case.UPDATE).order_by('-created_at')
    rows = []
    for key, source in watchers.SOURCES.items():
        row = stored.get(key)
        mine = [c for c in cases if (c.data or {}).get('source') == key]
        open_cases = [c for c in mine if c.status in Case.ACTIVE]
        if row is not None and (row.details or {}).get('supported') is False:
            state = 'unsupported'
        elif any(not (c.data or {}).get('unreadable') for c in open_cases):
            state = 'new'
        elif row is not None and row.error:
            state = 'failing'
        elif row is None or row.checked_at is None:
            state = 'never'
        else:
            state = 'current'
        rows.append({'source': source, 'row': row, 'state': state, 'open': open_cases,
                     'recent': [c for c in mine if c.status not in Case.ACTIVE][:3]})
    return render(request, 'staff/updates.html', _console_context(
        request, section='updates', rows=rows))
