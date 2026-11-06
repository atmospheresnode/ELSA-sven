# -*- coding: utf-8 -*-
"""Pages for non-staff: a user's own requests, reviewer forms, leaving "view as",
dismissing announcements, and marking a lien fixed.

Each view checks the user, token or bundle owner itself.
"""
from __future__ import unicode_literals

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from friends import throttle

from . import audit, campaigns, comms, dbtext, view_as
from .models import (Announcement, AnnouncementDismissal, Case, CaseRead, Lien, Notification, ReviewerInvite,
                     UserNotificationPreference)

# By name so it includes the /elsa mount. Email links land here.
signed_in = login_required(login_url=reverse_lazy('friends:login'))

REPLIES_PER_HOUR = 20
NEW_CONVERSATIONS_PER_HOUR = 5
REVIEW_SAVES_PER_HOUR = 60


def _no_store(response):
    response['Cache-Control'] = 'no-store'
    return response


@signed_in
def requests(request):
    cases = list(comms.with_requester_unread(
        Case.objects.filter(requester=request.user, is_spam=False), request.user).order_by('-updated_at'))
    for case in cases:
        case.unread = case.requester_unread
    return _no_store(render(request, 'staff/public/requests.html', {'cases': cases}))


@signed_in
def request_detail(request, pk):
    # 404 instead of 403 so we don't confirm another person's case exists.
    case = Case.objects.filter(pk=pk, requester=request.user, is_spam=False).first()
    if case is None:
        raise Http404('No such request.')

    viewing_as = getattr(request, 'staff_viewing_as', None)
    # Show what they wrote again if it couldn't be sent.
    draft = ''
    if request.method == 'POST':
        body = (request.POST.get('body') or '').strip()
        if not body:
            messages.error(request, 'Write a message first.')
        elif dbtext.unstorable(body):
            messages.error(request, 'ELSA cannot save emoji such as {0} yet. Please take them out and send '
                                    'it again.'.format(dbtext.unstorable(body)))
            draft = body
        elif throttle.hit('user-reply-{0}'.format(request.user.pk), 3600) > REPLIES_PER_HOUR:
            messages.error(request, 'You have sent a lot of messages in the last hour. Please wait a little.')
            draft = body
        else:
            comms.record_user_message(case, body, via='elsa')
            messages.success(request, 'Sent. The ELSA team will reply here and by email.')
        if not draft:
            return redirect('staff_public:request', pk=case.pk)

    notes = list(comms.public_notes(case))
    if viewing_as is None:
        comms.mark_seen(case, request.user, CaseRead.USER)
    return _no_store(render(request, 'staff/public/request.html', {
        'case': case, 'notes': notes, 'draft': draft,
        'can_write': viewing_as is None,
        'email_replies': bool(comms.inbound_address()),
        'liens': case.liens.filter(status=Lien.OPEN) if case.kind == Case.SUBMISSION else [],
    }))


@signed_in
@require_POST
def lien_fixed(request, pk):
    """The bundle owner says a lien is dealt with.  Staff still confirm it."""
    lien = Lien.objects.select_related('case').filter(
        pk=pk, case__requester=request.user, case__kind=Case.SUBMISSION, status=Lien.OPEN).first()
    if lien is None:
        raise Http404('No such item.')
    if throttle.hit('user-reply-{0}'.format(request.user.pk), 3600) > REPLIES_PER_HOUR:
        messages.error(request, 'You have sent a lot of updates in the last hour. Please wait a little.')
        return redirect('staff_public:request', pk=lien.case_id)
    fixed = request.POST.get('fixed') == '1'
    # A double click, or a second tab, says the same thing once.
    already = bool(lien.owner_fixed_at)
    lien.owner_fixed_at = (lien.owner_fixed_at or timezone.now()) if fixed else None
    lien.save(update_fields=['owner_fixed_at'])
    if fixed and not already:
        comms.record_user_message(lien.case, 'Marked as fixed: {0}'.format(lien.description[:500]), via='elsa')
    back = request.POST.get('back')
    if back == 'bundle' and lien.case.bundle_id:
        return redirect('build:bundle', pk_bundle=lien.case.bundle_id)
    return redirect('staff_public:request', pk=lien.case_id)


@signed_in
@require_POST
def dismiss_announcement(request, pk):
    item = get_object_or_404(Announcement, pk=pk)
    AnnouncementDismissal.objects.get_or_create(announcement=item, user=request.user)
    target = request.POST.get('next') or ''
    from django.utils.http import url_has_allowed_host_and_scheme
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()},
                                           require_https=request.is_secure()):
        target = reverse('main:index')
    return HttpResponseRedirect(target)


@signed_in
@require_POST
def view_as_stop(request):
    data = view_as.end(request)
    if data:
        from django.contrib.auth.models import User
        target = User.objects.filter(pk=data.get('target')).first()
        if target is not None:
            audit.record(request, 'user.view_as_end', target)
            messages.info(request, 'You stopped viewing ELSA as {0}.'.format(target.username))
            return redirect('staff:user', pk=target.pk)
    return redirect('staff:today')


def review_invite(request, token):
    """An invited reviewer's form.  No account; the link is the only key."""
    item = campaigns.find(token)
    if item is None:
        return _no_store(render(request, 'staff/public/review_closed.html', status=404))
    if request.method == 'POST':
        if throttle.hit('review-save-{0}'.format(item.pk), 3600) > REVIEW_SAVES_PER_HOUR:
            messages.error(request, 'Too many saves in an hour. Please wait a little.')
        elif item.status == ReviewerInvite.SUBMITTED:
            messages.info(request, 'This review was already submitted.')
        else:
            submit = request.POST.get('action') == 'submit'
            campaigns.save(item, request.POST, submit)
            messages.success(request, 'Thank you. Your review was submitted.' if submit
                             else 'Draft saved. You can come back to this link later.')
        return redirect('staff_public:review_invite', token=token)
    if item.opened_at is None:
        item.opened_at = timezone.now()
        if item.status == ReviewerInvite.INVITED:
            item.status = ReviewerInvite.OPENED
        item.save(update_fields=['opened_at', 'status'])
    answers = item.answers or {}
    response = render(request, 'staff/public/review_form.html', {
        'item': item, 'campaign': item.campaign,
        'questions': [(key, label, answers.get(key, '')) for key, label in campaigns.QUESTIONS],
        'submitted': item.status == ReviewerInvite.SUBMITTED,
    })
    # Link is the reviewer's key, so don't leak it in Referer. Not no-referrer, that breaks CSRF.
    response['Referrer-Policy'] = 'same-origin'
    return _no_store(response)



@signed_in
def new_request(request):
    """A user writes to the team from ELSA: a new conversation."""
    viewing_as = getattr(request, 'staff_viewing_as', None)
    subject = ' '.join((request.POST.get('subject') or '').split())[:150]
    body = (request.POST.get('body') or '').strip()[:comms.MESSAGE_LIMIT]
    if request.method == 'POST' and viewing_as is None:
        if not subject or not body:
            messages.error(request, 'Write a subject and a message.')
        elif dbtext.unstorable(subject, body):
            messages.error(request, 'ELSA cannot save emoji such as {0} yet. Please take them out and send '
                                    'it again.'.format(dbtext.unstorable(subject, body)))
        elif throttle.hit('user-new-conv-{0}'.format(request.user.pk), 3600) > NEW_CONVERSATIONS_PER_HOUR:
            messages.error(request, 'You have started several conversations in the last hour. Please wait a little, '
                                    'or add to one you already have.')
        else:
            from . import hooks
            case = hooks.open_case(Case.MESSAGE, subject, requester=request.user, body=body)
            if case is not None:
                Case.objects.filter(pk=case.pk).update(last_user_message_at=timezone.now())
                comms.notify_staff_new_conversation(case)
                comms.mark_seen(case, request.user, CaseRead.USER)
                messages.success(request, 'Sent. The ELSA team will reply here, and you will be notified.')
                return redirect('staff_public:request', pk=case.pk)
            messages.error(request, 'Your message could not be saved. Please try again.')
    return _no_store(render(request, 'staff/public/new_request.html', {'subject': subject, 'body': body}))


@signed_in
def notifications(request):
    viewing_as = getattr(request, 'staff_viewing_as', None)
    if request.method == 'POST' and viewing_as is None:
        Notification.objects.filter(user=request.user, read_at__isnull=True).update(read_at=timezone.now())
        return redirect('staff_public:notifications')
    from django.core.paginator import Paginator
    page = Paginator(Notification.objects.filter(user=request.user), 30).get_page(request.GET.get('page'))
    return _no_store(render(request, 'staff/public/notifications.html', {
        'page': page, 'unread': Notification.objects.filter(user=request.user, read_at__isnull=True).count(),
        'can_write': viewing_as is None}))


@signed_in
def open_notification(request, pk):
    item = Notification.objects.filter(pk=pk, user=request.user).first()
    if item is None:
        raise Http404('No such notification.')
    if item.read_at is None:
        Notification.objects.filter(pk=item.pk).update(read_at=timezone.now())
    # Only ever a path inside ELSA.
    target = item.url if item.url.startswith('/') and not item.url.startswith('//') else ''
    return HttpResponseRedirect(target or reverse('staff_public:notifications'))


@signed_in
@require_POST
def notification_email(request):
    """The Account page switch: email as well as the bell, or the bell only."""
    UserNotificationPreference.objects.update_or_create(
        user=request.user, defaults={'email': request.POST.get('email') == '1'})
    messages.success(request, 'Saved.')
    return HttpResponseRedirect(reverse('friends:useraccount') + '#email')


@signed_in
@require_POST
def notification_news(request):
    """Account page switch for news and updates."""
    UserNotificationPreference.set_news(request.user, request.POST.get('news') == '1')
    messages.success(request, 'Saved.')
    return HttpResponseRedirect(reverse('friends:useraccount') + '#email')


@csrf_exempt
def unsubscribe(request, token):
    """Unsubscribe link in news emails. No sign-in needed, the signed token names the account.

    GET only shows the page because mail scanners open links. CSRF exempt because the RFC 8058
    one-click POST comes from the mail program.
    """
    person = comms.user_from_unsubscribe(token)
    if person is None:
        return _no_store(render(request, 'staff/public/unsubscribe.html', {'invalid': True}, status=404))
    changed = None
    if request.method == 'POST':
        one_click = request.POST.get('List-Unsubscribe') == 'One-Click'
        subscribe = request.POST.get('action') == 'subscribe' and not one_click
        UserNotificationPreference.set_news(person, subscribe)
        changed = 'subscribed' if subscribe else 'unsubscribed'
    response = render(request, 'staff/public/unsubscribe.html', {
        'person': person, 'wants_news': UserNotificationPreference.wants_news(person), 'changed': changed,
        'token': token})
    # The link is the key: keep it out of Referer headers.
    response['Referrer-Policy'] = 'same-origin'
    return _no_store(response)
