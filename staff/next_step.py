# -*- coding: utf-8 -*-
"""The Next step box on a request: where it stands and the actions that make sense now.

Nothing here changes anything. An action is a dict with label, style ('primary' or ''),
and one of:
    modal: id of a window on the page,
    post: {field: value} for a one-click form,
    href: a link,
    stage: a review stage to move to.
"""
from __future__ import unicode_literals

from . import roles
from .models import Case, Lien

# Where a review can go from each stage, in button order.
STAGE_MOVES = {
    Case.STAGE_SUBMITTED: (Case.STAGE_UNDER_REVIEW, Case.STAGE_CHANGES),
    Case.STAGE_UNDER_REVIEW: (Case.STAGE_ACCEPTED, Case.STAGE_CHANGES),
    Case.STAGE_CHANGES: (Case.STAGE_UNDER_REVIEW, Case.STAGE_ACCEPTED),
    Case.STAGE_ACCEPTED: (),
    Case.STAGE_RELEASED: (),
    Case.STAGE_NOT_ACCEPTED: (),
}
# Can end a review without accepting while it's open, but it's not the main button.
LESS_COMMON = {stage: (Case.STAGE_NOT_ACCEPTED,) for stage in
               (Case.STAGE_SUBMITTED, Case.STAGE_UNDER_REVIEW, Case.STAGE_CHANGES)}
REOPEN_TO = Case.STAGE_UNDER_REVIEW

STAGE_BUTTONS = {
    Case.STAGE_UNDER_REVIEW: 'Start review',
    Case.STAGE_CHANGES: 'Ask for changes',
    Case.STAGE_ACCEPTED: 'Accept',
    Case.STAGE_NOT_ACCEPTED: 'Not accepted',
}


def _stage_action(stage, current, primary=False):
    label = STAGE_BUTTONS.get(stage, stage)
    if stage == Case.STAGE_UNDER_REVIEW and current == Case.STAGE_CHANGES:
        label = 'Resume review'
    return {'label': label, 'stage': stage, 'style': 'primary' if primary else ''}


def _reply(label='Write a reply', primary=True):
    return {'label': label, 'href': '#reply-body', 'style': 'primary' if primary else ''}


def build(case, user, can_work, reachable, unread=False, checklist_open=(), open_release=None):
    """Returns {'headline', 'detail', 'actions', 'stages'} for the box."""
    own = case.requester_id is not None and case.requester_id == user.pk
    take = {'label': 'Take it', 'post': {'action': 'claim'}, 'style': ''}
    actions, detail = [], ''

    if case.is_spam:
        return {'headline': 'Marked as spam', 'detail': 'Nobody is told and nothing is sent.',
                'actions': [{'label': 'Not spam', 'post': {'action': 'not_spam'}, 'style': ''}] if can_work else [],
                'stages': []}

    if case.kind == Case.SUBMISSION and case.bundle_id:
        return _submission(case, user, own, take, checklist_open, open_release)

    if not case.is_active:
        return {'headline': 'Closed: {0}'.format(case.get_status_display().lower()),
                'detail': 'Reopening puts it back in the open requests. The user is not told.',
                'actions': [{'label': 'Reopen', 'post': {'action': 'status', 'status': Case.OPEN},
                             'style': ''}] if can_work else [], 'stages': []}

    if case.kind == Case.UPDATE:
        return _update(case, can_work, take)

    if case.kind == Case.AGENCY:
        if own:
            return {'headline': 'Your own request', 'detail': 'A colleague has to decide it.',
                    'actions': [], 'stages': []}
        if roles.has(user, roles.DECIDE_AGENCY):
            actions = [{'label': 'Approve', 'modal': 'approveAgencyModal', 'style': 'primary'},
                       {'label': 'Decline', 'modal': 'declineAgencyModal', 'style': ''}]
        detail = 'Approving changes their agency and tells them. Declining needs a reason, which they receive.'
        if case.assignee_id is None and can_work:
            actions.append(take)
        return {'headline': 'Decide the agency change', 'detail': detail, 'actions': actions, 'stages': []}

    if not can_work:
        return {'headline': 'Read only', 'detail': '{0}. Your role can read this request but not act on it.'.format(
            _plain_state(case, unread)), 'actions': [], 'stages': []}

    resolve = {'label': 'Resolve', 'modal': 'statusResolvedModal', 'style': ''}
    if case.assignee_id is None:
        headline = 'Nobody has taken this yet'
        detail = 'Take it so the team knows it is yours, then reply.'
        actions = [dict(take, style='primary')]
        if reachable:
            actions.append(_reply(primary=False))
        actions.append(resolve)
    elif unread:
        headline = 'The user answered'
        detail = 'Their message is the newest in the conversation.'
        actions = ([_reply('Reply')] if reachable else []) + [resolve]
    elif case.status == Case.WAITING:
        headline = 'Waiting on the user'
        detail = 'Their answer brings this back to Today. Resolve it if they no longer need anything.'
        actions = [resolve] + ([_reply('Write again', primary=False)] if reachable else [])
    elif case.first_response_at is None:
        headline = 'Waiting for a first reply'
        detail = 'Nobody has answered yet.'
        actions = ([_reply()] if reachable else []) + [resolve]
    else:
        headline = 'In progress'
        detail = 'Reply, or resolve it when there is nothing left to do.'
        actions = ([_reply('Reply', primary=False)] if reachable else []) + [dict(resolve, style='primary')]
    if not reachable:
        detail = (detail + ' There is no way to reach the person who sent this, so only notes are possible.').strip()
    return {'headline': headline, 'detail': detail, 'actions': actions, 'stages': []}


def _update(case, can_work, take):
    """Outside update (staff.watchers): rollout progress and what to do next."""
    from django.urls import reverse
    from . import rollout
    resolve = {'label': 'Resolve', 'modal': 'statusResolvedModal', 'style': ''}
    actions = [dict(take, style='')] if case.assignee_id is None and can_work else []
    if (case.data or {}).get('unreadable'):
        return {'headline': 'A source could not be checked',
                'detail': 'The Updates page shows each check. Resolve this once checks work again.',
                'actions': [{'label': 'Open Updates', 'href': reverse('staff:updates'), 'style': 'primary'}]
                + actions + ([resolve] if can_work else []), 'stages': []}
    items = rollout.checklist_for(case)
    left = [item for item in items if not item['done']]
    notice = rollout.notice_for(case)
    prepared = bool((case.data or {}).get('notice_broadcast'))
    if not can_work:
        return {'headline': 'Rollout: {0} of {1} done'.format(len(items) - len(left), len(items)),
                'detail': '', 'actions': [], 'stages': []}
    if rollout.is_live(case) and notice and not prepared:
        return {'headline': 'Live: tell the users',
                'detail': 'ELSA prepares the email for {0}; you read it and send it.'.format(notice['audience']),
                'actions': [{'label': 'Prepare the email', 'post': {'action': 'prepare_notice'}, 'style': 'primary'},
                            {'label': 'Open the checklist', 'href': '#rollout-heading', 'style': ''}] + actions,
                'stages': []}
    if left:
        return {'headline': 'Roll it out: {0} of {1} steps done'.format(len(items) - len(left), len(items)),
                'detail': 'Next: {0}.'.format(left[0]['label'][0].lower() + left[0]['label'][1:]),
                'actions': [{'label': 'Open the checklist', 'href': '#rollout-heading', 'style': 'primary'}]
                + actions + [resolve], 'stages': []}
    return {'headline': 'Rolled out', 'detail': 'Every step is done.',
            'actions': [dict(resolve, style='primary')], 'stages': []}


def _plain_state(case, unread):
    if unread:
        return 'The user answered'
    if case.status == Case.WAITING:
        return 'Waiting on the user'
    return 'Open'


def _submission(case, user, own, take, checklist_open, open_release):
    stage = case.stage or Case.STAGE_SUBMITTED
    can_review = roles.has(user, roles.REVIEW_BUNDLES)
    if own:
        return {'headline': 'Your own bundle', 'detail': 'A colleague has to review it.', 'actions': [],
                'stages': []}
    if not can_review:
        return {'headline': 'Review: {0}'.format(case.get_stage_display() or 'Submitted'),
                'detail': 'Your role does not include reviewing bundles.', 'actions': [], 'stages': []}

    open_liens = case.liens.filter(status=Lien.OPEN).count()
    fixed = case.liens.filter(status=Lien.OPEN, owner_fixed_at__isnull=False).count()
    proposer = (case.data or {}).get('acceptance_proposed_by')
    actions = []
    if case.assignee_id is None and case.is_active:
        actions.append(take)

    if stage == Case.STAGE_ACCEPTED:
        headline = 'Accepted'
        detail = 'Next is the release: final validate, package, DOI, delivery, then Released.'
        actions.insert(0, {'label': 'Continue the release' if open_release else 'Start the release',
                           'release': True, 'style': 'primary'})
        return {'headline': headline, 'detail': detail, 'actions': actions, 'stages': []}
    if stage in (Case.STAGE_RELEASED, Case.STAGE_NOT_ACCEPTED):
        return {'headline': 'Review finished: {0}'.format(case.get_stage_display().lower()),
                'detail': 'To reopen it, use "Move the review back" under More actions.', 'actions': actions,
                'stages': []}

    moves = list(STAGE_MOVES.get(stage, ()))
    stages = moves + list(LESS_COMMON.get(stage, ()))
    if stage == Case.STAGE_SUBMITTED:
        headline = 'Waiting for review'
        detail = 'Start the review to tell the owner someone is looking at it.'
        primary = Case.STAGE_UNDER_REVIEW
    elif stage == Case.STAGE_CHANGES:
        headline = 'Waiting for the owner to make changes'
        detail = ('The owner says {0} of the open liens {1} fixed. '.format(fixed, 'is' if fixed == 1 else 'are')
                  if fixed else '') + 'Resume the review when they are ready.'
        primary = Case.STAGE_UNDER_REVIEW
    else:
        headline = 'Under review'
        primary = Case.STAGE_ACCEPTED
        if open_liens:
            detail = '{0} open lien{1}: resolve or waive {2} before accepting.'.format(
                open_liens, '' if open_liens == 1 else 's', 'it' if open_liens == 1 else 'them')
            primary = Case.STAGE_CHANGES
        elif checklist_open:
            detail = '{0} checklist item{1} not ticked yet.'.format(
                len(checklist_open), '' if len(checklist_open) == 1 else 's')
        else:
            detail = 'The checklist is done and no liens are open.'
    if proposer and proposer != user.pk:
        headline = 'Acceptance proposed'
        detail = 'A colleague proposed accepting this bundle. Accept to confirm it.'
        primary = Case.STAGE_ACCEPTED
    elif proposer:
        headline = 'Waiting for a second reviewer'
        detail = 'You proposed accepting this bundle; a colleague has to confirm it.'
        stages = [s for s in stages if s != Case.STAGE_ACCEPTED]
    if open_liens:
        stages = [s for s in stages if s != Case.STAGE_ACCEPTED]
    buttons = [_stage_action(s, stage, primary=(s == primary)) for s in stages if s in moves]
    buttons.sort(key=lambda b: 0 if b['style'] == 'primary' else 1)
    if open_liens:
        buttons.append({'label': 'See the liens', 'href': '#liens-heading', 'style': ''})
    return {'headline': headline, 'detail': detail, 'actions': buttons + actions,
            'stages': stages, 'less_common': [_stage_action(s, stage) for s in stages if s not in moves]}
