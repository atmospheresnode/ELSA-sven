# -*- coding: utf-8 -*-
"""More Admin Console pages: search, saved replies, preferences, spam, bundle inspector,
validate controls, peer reviews, announcements, broadcasts, email log, reports, exports.

Every change is one call into actions or campaigns.
"""
from __future__ import unicode_literals

import csv
import os
from collections import Counter, OrderedDict
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.models import User
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from build.models import Bundle, ValidationRun

from . import actions, audit, broadcasts, campaigns, review, roles
from .decorators import console_view, limited
from .models import (Announcement, AuditEntry, BlockedSender, Broadcast, Case, MailLog,
                     ReviewCampaign, ReviewerInvite, SavedReply, StaffPreference)
from .views import REASON_LIMIT, _console_context, _date, _page, _pk, _query, _text


def _act(request, call, success, *args, **kwargs):
    """Run one action and show the outcome as a message. Returns its result or None."""
    try:
        result = call(request, *args, **kwargs)
    except actions.ActionError as error:
        messages.error(request, str(error))
        return None
    if success:
        messages.success(request, success)
    return result if result is not None else True


# --- Search ---

@console_view()
@limited('lookup')
def search(request):
    query = _query(request)
    found = {'cases': [], 'users': [], 'bundles': []}
    if query:
        number = query.lstrip('#')
        if number.isdecimal():
            case = Case.objects.filter(pk=int(number)).first()
            if case is not None and query.startswith('#'):
                return redirect('staff:case', pk=case.pk)
        found['cases'] = list(Case.objects.filter(
            Q(title__icontains=query) | Q(requester_username__icontains=query)
            | Q(requester_name__icontains=query) | Q(requester_email__icontains=query)
            | Q(body__icontains=query) | (Q(pk=int(number)) if number.isdecimal() else Q(pk=None))
        ).order_by('-updated_at')[:20])
        found['users'] = list(User.objects.filter(
            Q(username__icontains=query) | Q(email__icontains=query)
            | Q(first_name__icontains=query) | Q(last_name__icontains=query)).order_by('username')[:20])
        found['bundles'] = list(Bundle.objects.select_related('user').filter(
            Q(name__icontains=query) | Q(user__username__icontains=query)).order_by('-updated_at')[:20])
    return render(request, 'staff/search.html', _console_context(
        request, section='search', query=query, found=found, search_query=query,
        total=sum(len(v) for v in found.values())))


# --- Saved replies and preferences ---

@console_view()
def saved_replies(request):
    editing = None
    if request.GET.get('edit'):
        editing = SavedReply.objects.filter(pk=_pk(request.GET.get('edit'))).first()
    if request.method == 'POST':
        target = SavedReply.objects.filter(pk=_pk(request.POST.get('id'))).first()
        if request.POST.get('action') == 'delete' and target is not None:
            _act(request, actions.delete_reply, 'Deleted.', target)
        else:
            _act(request, actions.save_reply, 'Saved.', target, _text(request, 'title', 120),
                 _text(request, 'body'))
        return redirect('staff:saved_replies')
    return render(request, 'staff/saved_replies.html', _console_context(
        request, section='replies', replies=SavedReply.objects.all(), editing=editing))


@console_view()
def preferences(request):
    pref = StaffPreference.for_user(request.user)
    if request.method == 'POST':
        pref.daily_digest = request.POST.get('daily_digest') == '1'
        pref.when_assigned = request.POST.get('when_assigned') == '1'
        pref.when_user_replies = request.POST.get('when_user_replies') == '1'
        pref.when_unassigned = request.POST.get('when_unassigned') == '1'
        pref.when_mentioned = request.POST.get('when_mentioned') == '1'
        pref.when_update = request.POST.get('when_update') == '1'
        away = _date(request.POST.get('away_until'))
        pref.away_until = away if away and away >= timezone.localdate() else None
        pref.save()
        messages.success(request, 'Preferences saved.')
        return redirect('staff:preferences')
    return render(request, 'staff/preferences.html', _console_context(
        request, section='preferences', pref=pref))


@console_view()
def spam(request):
    if request.method == 'POST':
        blocked = get_object_or_404(BlockedSender, pk=_pk(request.POST.get('id')))
        _act(request, actions.unblock, '{0} can write again.'.format(blocked.email), blocked)
        return redirect('staff:spam')
    return render(request, 'staff/spam.html', _console_context(
        request, section='queue', blocked=BlockedSender.objects.all(),
        cases=Case.objects.filter(is_spam=True).order_by('-updated_at')[:50]))


# --- Bundle inspector and validate ---

@console_view()
def bundle_files(request, pk):
    bundle = get_object_or_404(Bundle.objects.select_related('user'), pk=pk)
    path = request.GET.get('path', '')
    view = request.GET.get('view', '')
    opened = text = note = diff = None
    if path:
        target = review.resolve(bundle, path)
        if target is None and view != 'diff':
            raise Http404('No such file in this bundle.')
        opened = path
        if view == 'diff':
            diff = review.file_diff(bundle, path)
        elif target is not None:
            text, note = review.read_text(target)
    changes = review.changes_since_last(bundle)
    changed = set()
    if changes:
        changed = set(changes['added']) | set(changes['changed'])
    from . import hooks
    review_case = (Case.objects.filter(kind=Case.SUBMISSION, bundle=bundle, status__in=Case.ACTIVE)
                   .order_by('-created_at').first())
    can_raise = (review_case is not None and roles.has(request.user, roles.REVIEW_BUNDLES)
                 and review_case.requester_id != request.user.pk)
    line_liens = {}
    if opened:
        for lien in hooks.owner_liens(bundle):
            if lien.file_path == opened and lien.line:
                line_liens.setdefault(lien.line, []).append(lien)
    return render(request, 'staff/bundle_files.html', _console_context(
        request, section='bundles', page_title='Files of {0}'.format(bundle.name), bundle=bundle,
        tree=review.tree(bundle), opened=opened, view=view,
        text=text, note=note, lines=text.splitlines() if text else [], diff=diff, changed=changed,
        liens=hooks.owner_liens(bundle), line_liens=line_liens, review_case=review_case, can_raise=can_raise,
        folder_missing=review.bundle_folder(bundle) is None))


@console_view()
@limited('download')
def bundle_file_download(request, pk):
    bundle = get_object_or_404(Bundle, pk=pk)
    target = review.resolve(bundle, request.GET.get('path', ''))
    if target is None:
        raise Http404('No such file in this bundle.')
    try:
        # O_NOFOLLOW: if the file was swapped for a link after the check, refuse.
        handle = os.fdopen(os.open(target, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)), 'rb')
    except OSError:
        raise Http404('No such file in this bundle.')
    audit.record(request, 'bundle.file', bundle, changes={'path': request.GET.get('path', '')[:300]})
    return FileResponse(handle, as_attachment=True, filename=os.path.basename(target))


@console_view()
@require_POST
def bundle_validate(request, pk):
    bundle = get_object_or_404(Bundle, pk=pk)
    run = _act(request, actions.run_validation, None, bundle, request.POST.get('tier', 'structure'))
    if run:
        if getattr(run, 'pk', None):
            messages.success(request, 'PDS validate is running (run #{0}). Refresh in a minute.'.format(run.pk))
        else:
            messages.info(request, 'The server is already running as many checks as it allows. '
                                   'Try again in a few minutes.')
    return redirect('staff:bundle', pk=bundle.pk)


@console_view()
@require_POST
def bundle_label_upgrade(request, pk):
    bundle = get_object_or_404(Bundle, pk=pk)
    rows = _act(request, actions.apply_label_upgrades, None, bundle)
    if rows is None:
        pass
    elif not rows:
        messages.info(request, 'Nothing needed changing. The labels are up to date.')
    else:
        failed = [row for row in rows if row.status == 'failed']
        if failed:
            messages.error(request, 'Label update {:04d} failed and the files were put back: {}'.format(
                failed[0].number, failed[0].error[:300]))
        else:
            messages.success(request, 'Labels updated: {} change{}.'.format(
                len(rows), '' if len(rows) == 1 else 's'))
    return redirect('staff:bundle', pk=bundle.pk)


@console_view()
@require_POST
def bundle_label_upgrade_undo(request, pk, pk_upgrade):
    from build.models import BundleUpgrade
    row = get_object_or_404(BundleUpgrade.objects.select_related('bundle'), pk=pk_upgrade, bundle_id=pk)
    _act(request, actions.undo_label_upgrade, 'Put back the files from before that change.', row)
    return redirect('staff:bundle', pk=pk)


@console_view()
@require_POST
def run_cancel(request, pk_run):
    run = get_object_or_404(ValidationRun.objects.select_related('bundle'), pk=pk_run)
    _act(request, actions.cancel_validation, 'Cancelled.', run)
    if request.POST.get('back') == 'health':
        return redirect('staff:health')
    return redirect('staff:bundle', pk=run.bundle_id)


@console_view()
@require_POST
def runs_reap(request):
    cleared = _act(request, actions.clear_stuck_validations, None)
    if cleared is not None:
        messages.success(request, 'Checked for stuck runs; any that could not still be working were closed.')
    return redirect('staff:health')


@console_view()
@require_POST
def assistant_switch(request):
    enabled = request.POST.get('enabled') == '1'
    _act(request, actions.set_assistant, 'The Assistant is {0}.'.format('on' if enabled else 'off'),
         enabled, _text(request, 'reason', REASON_LIMIT))
    return redirect('staff:health')


# --- Peer review campaigns ---

@console_view()
def campaign_list(request):
    if request.method == 'POST':
        campaign = _act(request, campaigns.create, 'Review created. Now invite reviewers.',
                        _text(request, 'title', 200), _text(request, 'data_set', 255),
                        _text(request, 'description'), _date(request.POST.get('deadline')))
        if campaign and campaign is not True:
            return redirect('staff:campaign', pk=campaign.pk)
        return redirect('staff:campaigns')
    items = ReviewCampaign.objects.annotate(
        invited=Count('invites', filter=~Q(invites__status=ReviewerInvite.REVOKED)),
        done=Count('invites', filter=Q(invites__status=ReviewerInvite.SUBMITTED)))
    return render(request, 'staff/campaigns.html', _console_context(
        request, section='campaigns', campaigns=items))


@console_view()
def campaign_detail(request, pk):
    campaign = get_object_or_404(ReviewCampaign.objects.select_related('case'), pk=pk)
    return render(request, 'staff/campaign.html', _console_context(
        request, section='campaigns', page_title=str(campaign), campaign=campaign, invites=campaign.invites.all(),
        questions=campaigns.QUESTIONS))


@console_view()
@require_POST
def campaign_action(request, pk):
    campaign = get_object_or_404(ReviewCampaign, pk=pk)
    action = request.POST.get('action')
    if action == 'invite':
        _act(request, campaigns.invite, 'Invitation sent.', campaign,
             _text(request, 'name', 150), _text(request, 'email', 254))
    elif action in ('resend', 'revoke'):
        item = get_object_or_404(ReviewerInvite, pk=_pk(request.POST.get('invite')), campaign=campaign)
        if action == 'resend':
            _act(request, campaigns.resend, 'A new link was sent; the old one no longer works.', item)
        else:
            _act(request, campaigns.revoke, 'Invitation withdrawn.', item)
    elif action == 'close':
        _act(request, campaigns.close, 'Review closed.', campaign)
    else:
        messages.error(request, 'Unknown action.')
    return redirect('staff:campaign', pk=campaign.pk)


@console_view()
@limited('download')
def campaign_pdf(request, pk):
    campaign = get_object_or_404(ReviewCampaign.objects.select_related('case'), pk=pk)
    response = HttpResponse(campaigns.report_pdf(campaign), content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="peer_review_{0}.pdf"'.format(campaign.pk)
    return response


# --- Announcements and broadcasts ---

def _when(value, fallback):
    try:
        parsed = parse_datetime(value or '')
    except ValueError:      # well formed but impossible, such as February 31
        return fallback
    if parsed is None:
        return fallback
    return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed


@console_view()
def announcements(request):
    now = timezone.now()
    if request.method == 'POST':
        if request.POST.get('action') == 'end':
            item = get_object_or_404(Announcement, pk=_pk(request.POST.get('id')))
            _act(request, actions.end_announcement, 'Announcement ended.', item)
        else:
            _act(request, actions.post_announcement, 'Announcement posted.',
                 _text(request, 'title', 150), _text(request, 'body', 2000), request.POST.get('level'),
                 _when(request.POST.get('starts_at'), now), _when(request.POST.get('ends_at'), None))
        return redirect('staff:announcements')
    return render(request, 'staff/announcements.html', _console_context(
        request, section='announcements',
        live=Announcement.objects.filter(starts_at__lte=now, ends_at__gt=now),
        upcoming=Announcement.objects.filter(starts_at__gt=now),
        past=Announcement.objects.filter(ends_at__lte=now)[:20],
        levels=Announcement.LEVEL_CHOICES,
        default_start=timezone.localtime(now).strftime('%Y-%m-%dT%H:%M'),
        default_end=timezone.localtime(now + timedelta(days=3)).strftime('%Y-%m-%dT%H:%M'),
        # Filled in from an outside update's rollout (staff.rollout.banner_url).
        prefill_title=(request.GET.get('title') or '')[:150], prefill_body=(request.GET.get('body') or '')[:2000]))


@console_view()
def broadcast_list(request):
    return render(request, 'staff/broadcasts.html', _console_context(
        request, section='broadcasts', items=Broadcast.objects.all()[:50]))


@console_view()
def broadcast_edit(request, pk=None):
    item = get_object_or_404(Broadcast, pk=pk) if pk else None
    if request.method == 'POST':
        action = request.POST.get('action', 'save')
        if item is not None and action == 'test':
            _act(request, actions.send_broadcast_test, 'A test copy went to {0}.'.format(request.user.email), item)
            return redirect('staff:broadcast', pk=item.pk)
        if item is not None and action == 'send':
            total = _act(request, actions.send_broadcast, None, item)
            if total is not None:
                messages.success(request, 'Sending to {0} people in the background. Refresh this page to see '
                                          'how far it has got.'.format(0 if total is True else total))
            return redirect('staff:broadcast', pk=item.pk)
        if item is not None and action == 'resume':
            _act(request, actions.resume_broadcast, 'Sending again. Nobody who already has it gets it twice.', item)
            return redirect('staff:broadcast', pk=item.pk)
        saved = _act(request, actions.save_broadcast, 'Draft saved.', item, _text(request, 'subject', 150),
                     _text(request, 'body'), request.POST.get('segment'))
        if saved and saved is not True:
            return redirect('staff:broadcast', pk=saved.pk)
        return redirect('staff:broadcast_new') if item is None else redirect('staff:broadcast', pk=item.pk)
    counts = OrderedDict((key, broadcasts.audience(key).count()) for key, _ in Broadcast.SEGMENT_CHOICES)
    return render(request, 'staff/broadcast.html', _console_context(
        request, section='broadcasts', page_title=(item.subject if item else '') or 'Email users', item=item,
        segments=Broadcast.SEGMENT_CHOICES, counts=counts,
        dev_limited=broadcasts.dev_limited(), opted_out=broadcasts.opted_out()))


# --- Email log, reports, exports ---

@console_view()
def mail_log(request):
    query = _query(request)
    failed = request.GET.get('failed') == '1'
    rows = MailLog.objects.all()
    if query:
        rows = rows.filter(Q(subject__icontains=query) | Q(to__icontains=query))
    if failed:
        rows = rows.filter(sent=False)
    from django.conf import settings
    return render(request, 'staff/mail_log.html', _console_context(
        request, section='mail', page=_page(request, rows), query=query, failed=failed,
        logging_on=settings.EMAIL_BACKEND == 'staff.mail_log.LoggingBackend'))


def _hours(delta):
    return round(delta.total_seconds() / 3600.0, 1)


def _median(values):
    values = sorted(values)
    if not values:
        return None
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2.0


def _by_month(moments):
    """[(first day of the month, count)] in the current time zone, oldest first.

    Don't use TruncMonth here. It needs CONVERT_TZ, and our MariaDB has no time zone tables.
    """
    counts = Counter(timezone.localtime(moment).date().replace(day=1) for moment in moments if moment)
    return sorted(counts.items())


def _report_data(since):
    cases = Case.objects.filter(created_at__gte=since, is_spam=False)
    first = [_hours(c.first_response_at - c.created_at) for c in cases.filter(first_response_at__isnull=False)]
    closed = [_hours(c.closed_at - c.created_at) for c in cases.filter(closed_at__isnull=False)]
    submitted = Bundle.objects.filter(submitted_at__gte=since).values_list('submitted_at', flat=True)
    started = Bundle.objects.filter(created_at__gte=since).values_list('created_at', flat=True)
    by_kind = dict(cases.values_list('kind').annotate(n=Count('pk')))
    return {
        'cases_total': cases.count(),
        'by_kind': [(label, by_kind.get(key, 0)) for key, label in Case.KIND_CHOICES],
        'first_median': _median(first), 'first_count': len(first),
        'close_median': _median(closed), 'close_count': len(closed),
        'submitted': _by_month(submitted),
        'started': _by_month(started),
        'new_users': User.objects.filter(date_joined__gte=since).count(),
    }


@console_view()
@limited('download', when=lambda request: request.GET.get('format') == 'csv')
def reports(request):
    try:
        days = max(7, min(int(request.GET.get('days', 365)), 3650))
    except ValueError:
        days = 365
    since = timezone.now() - timedelta(days=days)
    data = _report_data(since)
    if request.GET.get('format') == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="elsa_report_{0}d.csv"'.format(days)
        writer = csv.writer(response)
        writer.writerow(['ELSA report, last {0} days, generated {1:%Y-%m-%d}'.format(days, timezone.now())])
        writer.writerow([])
        writer.writerow(['Measure', 'Value'])
        writer.writerow(['Requests received', data['cases_total']])
        writer.writerow(['New accounts', data['new_users']])
        writer.writerow(['Median hours to first response', data['first_median']])
        writer.writerow(['Median hours to close', data['close_median']])
        writer.writerow([])
        writer.writerow(['Request type', 'Count'])
        writer.writerows(data['by_kind'])
        writer.writerow([])
        writer.writerow(['Month', 'Bundles started', 'Bundles submitted'])
        started = {m: n for m, n in data['started']}
        submitted = {m: n for m, n in data['submitted']}
        for month in sorted(set(started) | set(submitted)):
            writer.writerow(['{0:%Y-%m}'.format(month), started.get(month, 0), submitted.get(month, 0)])
        return response
    peak = max([n for _, n in data['started']] + [n for _, n in data['submitted']] + [1])
    started = {m: n for m, n in data['started']}
    submitted = {m: n for m, n in data['submitted']}
    months = [{'month': m, 'started': started.get(m, 0), 'submitted': submitted.get(m, 0),
               'started_pct': round(100.0 * started.get(m, 0) / peak),
               'submitted_pct': round(100.0 * submitted.get(m, 0) / peak)}
              for m in sorted(set(started) | set(submitted))]
    return render(request, 'staff/reports.html', _console_context(
        request, section='reports', data=data, days=days, months=months))


def _cell(value):
    """Make text safe for spreadsheets. A cell starting with = + - @ runs as a formula in Excel."""
    text = str(value)
    if text[:1] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + text
    return text


@console_view(roles.VIEW_AUDIT)
@limited('download')
def audit_csv(request):
    entries = AuditEntry.objects.all()
    actor = _query(request, 'actor', 150)
    if actor:
        entries = entries.filter(actor_username__icontains=actor)
    if request.GET.get('action') in audit.ACTION_LABELS:
        entries = entries.filter(action=request.GET['action'])
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="elsa_audit_{0:%Y%m%d}.csv"'.format(timezone.now())
    writer = csv.writer(response)
    writer.writerow(['When (UTC)', 'Who', 'Action', 'Target type', 'Target id', 'Target', 'Reason',
                     'Details', 'IP'])
    for entry in entries.iterator():
        writer.writerow([_cell(v) for v in (
            entry.created_at.strftime('%Y-%m-%d %H:%M:%S'), entry.actor_username, entry.action,
            entry.target_type, entry.target_id, entry.target_label, entry.reason,
            entry.changes, entry.ip or '')])
    audit.record(request, 'audit.export')
    return response
