# -*- coding: utf-8 -*-
"""Writes the audit log and checks it hasn't been altered.

Each entry's seal has the SHA-256 of the entry and the previous seal. verify() walks the
chain and reports the first seal that doesn't match.
"""
from __future__ import unicode_literals

import hashlib
import json

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

from friends.devices import client_ip, user_agent

from .dbtext import narrow
from .models import AuditChain, AuditEntry, AuditSeal

GENESIS = '0' * 64


def record(request, action, target=None, reason='', changes=None, actor=None, actor_name=''):
    """One entry for one thing a staff member did.

    The type, key and label of ``target`` are copied so the entry still reads right if the
    target is renamed or deleted. ``actor_name`` is for management commands run on the server.
    """
    actor = actor or getattr(request, 'user', None)
    target_type = target_id = target_label = ''
    if target is not None:
        target_type = target._meta.model_name
        target_id = str(target.pk)
        target_label = narrow(_label(target))

    with transaction.atomic():
        chain = _locked_chain()
        entry = AuditEntry.objects.create(
            actor=actor if actor is not None and actor.is_authenticated else None,
            actor_username=actor_name or getattr(actor, 'username', '') or 'system',
            action=action,
            target_type=target_type,
            target_id=target_id,
            target_label=target_label[:255],
            reason=narrow(reason or ''),
            changes=changes or {},
            ip=client_ip(request) if request is not None else None,
            user_agent=narrow((user_agent(request) if request is not None else '')[:300]),
        )
        _seal(chain, AuditEntry.objects.get(pk=entry.pk))
    return entry


# --- The seal chain ---

def _locked_chain():
    chain = AuditChain.objects.select_for_update().filter(pk=1).first()
    if chain is None:
        AuditChain.objects.get_or_create(pk=1)
        chain = AuditChain.objects.select_for_update().get(pk=1)
    return chain


def entry_digest(values, prev_hash):
    """The SHA-256 of one entry's stored values and the previous seal."""
    changes = json.loads(json.dumps(values['changes'] or {}, cls=DjangoJSONEncoder))
    created = values['created_at']
    if created is not None:
        created = created.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')
    canonical = json.dumps([
        prev_hash, values['id'], created, values['actor_id'], values['actor_username'],
        values['action'], values['target_type'], values['target_id'], values['target_label'],
        values['reason'], changes, values['ip'], values['user_agent'],
    ], sort_keys=True, ensure_ascii=False, cls=DjangoJSONEncoder)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


SEALED_FIELDS = ('id', 'created_at', 'actor_id', 'actor_username', 'action', 'target_type',
                 'target_id', 'target_label', 'reason', 'changes', 'ip', 'user_agent')


def _values(entry):
    return {name: getattr(entry, name) for name in SEALED_FIELDS}


def _seal(chain, entry):
    prev = chain.head_hash or GENESIS
    digest = entry_digest(_values(entry), prev)
    AuditSeal.objects.create(entry=entry, prev_hash=prev, hash=digest)
    AuditChain.objects.filter(pk=chain.pk).update(head_hash=digest, count=chain.count + 1)


def verify(record_result=True):
    """Returns (True, '') if every seal matches, else (False, first problem found)."""
    problem = ''
    prev = GENESIS
    seen = 0
    seals = AuditSeal.objects.order_by('pk').values('pk', 'entry_id', 'prev_hash', 'hash')
    last_pk = 0
    while not problem:
        batch = list(seals.filter(pk__gt=last_pk)[:2000])
        if not batch:
            break
        entries = {row['id']: row for row in AuditEntry.objects.filter(
            pk__in=[s['entry_id'] for s in batch]).values(*SEALED_FIELDS)}
        for seal in batch:
            last_pk = seal['pk']
            seen += 1
            entry = entries.get(seal['entry_id'])
            if seal['prev_hash'] != prev:
                problem = 'Seal {0} does not follow the one before it (an entry was removed or reordered).'.format(seal['pk'])
            elif entry is None:
                problem = 'Audit entry {0} is missing.'.format(seal['entry_id'])
            elif entry_digest(entry, prev) != seal['hash']:
                problem = 'Audit entry {0} was changed after it was written.'.format(seal['entry_id'])
            if problem:
                break
            prev = seal['hash']
    if not problem:
        chain = AuditChain.objects.filter(pk=1).first()
        unsealed = AuditEntry.objects.filter(seal__isnull=True).count()
        if unsealed:
            problem = '{0} audit entr{1} without a seal (written around the log).'.format(
                unsealed, 'y' if unsealed == 1 else 'ies')
        elif chain is not None and (chain.count != seen or (seen and chain.head_hash != prev)):
            problem = 'The newest audit entries were removed.'
    if record_result:
        AuditChain.objects.get_or_create(pk=1)
        AuditChain.objects.filter(pk=1).update(verified_at=timezone.now(), verified_ok=not problem,
                                               problem=problem[:500])
    return not problem, problem


def _label(target):
    model = target._meta.model_name
    if model == 'user':
        return target.username
    if model == 'bundle':
        return '{0} ({1})'.format(target.name, target.user.username)
    return str(target)


ACTION_LABELS = {
    'console.enter': 'Entered the Admin Console',
    'console.exit': 'Left the Admin Console',
    'case.claim': 'Took a request',
    'case.unassign': 'Unassigned a request',
    'case.note': 'Added an internal note',
    'case.reply': 'Replied to the user',
    'case.status': 'Changed case status',
    'case.stage': 'Moved a bundle through review',
    'lien.add': 'Added a lien',
    'lien.close': 'Closed a lien',
    'user.agency': 'Changed agency',
    'user.disable': 'Disabled an account',
    'user.enable': 'Enabled an account',
    'user.sign_out': 'Signed a user out everywhere',
    'bundle.download': 'Downloaded a bundle',
    'role.grant': 'Granted a role',
    'role.revoke': 'Revoked a role',
    'case.assign': 'Assigned a request',
    'case.start': 'Started a conversation with a user',
    'case.propose_accept': 'Proposed accepting a bundle',
    'setting.change': 'Changed a console setting',
    'release.start': 'Started a release',
    'outreach.nudge': 'Nudged bundle owners',
    'knowledge.create': 'Wrote a knowledge note',
    'knowledge.update': 'Edited a knowledge note',
    'knowledge.delete': 'Deleted a knowledge note',
    'dossier.export': 'Exported a dossier',
    'release.validate': 'Ran the final validate',
    'release.package': 'Built a delivery package',
    'release.doi': 'Saved a DOI',
    'release.delivered': 'Recorded a delivery',
    'release.registry': 'Updated registry status',
    'release.released': 'Released a bundle',
    'project.create': 'Created a project',
    'project.update': 'Updated a project',
    'project.bundle': 'Changed a project\'s bundles',
    'project.provider': 'Changed a project\'s providers',
    'project.milestone': 'Changed a project milestone',
    'case.spam': 'Marked as spam',
    'case.not_spam': 'Said it is not spam',
    'case.checklist': 'Updated the review checklist',
    'sender.unblock': 'Unblocked a sender',
    'saved_reply.save': 'Saved a reply template',
    'saved_reply.delete': 'Deleted a reply template',
    'user.unlock': 'Cleared sign-in lockouts',
    'user.remind_confirm': 'Sent a confirm-your-email reminder',
    'user.passkey_remove': 'Removed a passkey',
    'user.passkey_reset': 'Reset every passkey (on the server)',
    'security.alert': 'Security alert sent to the developers',
    'security.access_review': 'Monthly access review sent',
    'user.view_as': 'Started viewing as a user',
    'user.view_as_end': 'Stopped viewing as a user',
    'user.view_as_page': 'Viewed a page as a user',
    'announcement.post': 'Posted an announcement',
    'announcement.end': 'Ended an announcement',
    'broadcast.save': 'Saved an email to users',
    'broadcast.send': 'Sent an email to users',
    'broadcast.resume': 'Resumed sending an email to users',
    'broadcast.done': 'Finished sending an email to users',
    'validate.start': 'Started PDS validate',
    'bundle.label_upgrade': 'Applied label updates to a bundle',
    'bundle.label_upgrade_undo': 'Undid a label update',
    'validate.cancel': 'Cancelled a validate run',
    'validate.reap': 'Cleared stuck validate runs',
    'assistant.on': 'Switched the Assistant on',
    'assistant.off': 'Switched the Assistant off',
    'campaign.create': 'Started a peer review',
    'campaign.invite': 'Invited a reviewer',
    'campaign.resend': 'Sent a reviewer a new link',
    'campaign.revoke': 'Withdrew a reviewer invitation',
    'campaign.close': 'Closed a peer review',
    'bundle.file': 'Downloaded a bundle file',
    'audit.export': 'Exported the audit log',
    'updates.check': 'Checked for outside updates',
}


def action_label(action):
    return ACTION_LABELS.get(action, action)
