# -*- coding: utf-8 -*-
"""Rollout checklist for a new outside version, from released to live.

Items ELSA can see tick themselves. Staff tick the rest, including "Live on the production
site". Once live it offers a draft email and banner. Nothing goes out until staff do it.
"""
from __future__ import unicode_literals

from django.db import transaction
from django.urls import reverse

from . import audit, roles
from .actions import ActionError, _require
from .models import Broadcast, Case, CaseNote

LIVE = 'prod'


def _offered(case):
    from build.forms import VERSION_CHOICES
    return case.data.get('version') in dict(VERSION_CHOICES)


def _templates_name(case):
    from . import watchers
    try:
        return '{0}_{1}'.format(*watchers.pinned_ama()) == case.data.get('version')
    except Exception:
        return False


def _validate_in_use(case):
    from build import validate_release
    try:
        return validate_release.in_use_release() == case.data.get('version')
    except Exception:
        return False


def _validate_installed(case):
    from build import validate_release
    try:
        return case.data.get('version') in validate_release.installed_releases()
    except Exception:
        return False


def _django_running(case):
    import django
    from .watchers import _version_key
    return _version_key(django.get_version()) >= _version_key(case.data.get('version', ''))


def _model_dropped(case):
    from .watchers import SOURCES
    try:
        return case.data.get('version') not in SOURCES['gemini'].models()
    except Exception:
        return False


def _notice_prepared(case):
    return bool(case.data.get('notice_broadcast'))


# (key, label, check or None). With a check it ticks itself, None means staff tick it.
CHECKLISTS = {
    'validate': [
        ('installed', 'Installed and compared over every bundle ("manage.py validate_release check")', _validate_installed),
        ('dev', 'Switched on this server ("manage.py validate_release switch")', _validate_in_use),
        ('reviewed', 'Comparison read: no new failures, or they are understood', None),
        (LIVE, 'Live on the production site', None),
        ('knowledge', 'Assistant help pages that name the validate version updated', None),
        ('told', 'Users told what changes for them', _notice_prepared),
    ],
    'pds4_im': [
        ('decided', 'Decided whether ELSA should offer it (the team\'s call)', None),
        ('offered', 'In ELSA\'s version list (VERSION_CHOICES in build/forms.py)', _offered),
        ('catalog', 'Schemas cached for validate ("manage.py build_schema_catalog")', None),
        ('archive_only', 'Understood: Archive bundles only; External stays with the AMA dictionary\'s version', None),
        (LIVE, 'Live on the production site', None),
        ('told', 'Archive bundle owners told', _notice_prepared),
    ],
    'ama_ldd': [
        ('templates', 'External templates name it (Template_PE.xml and the other three External templates)', _templates_name),
        ('core', 'Core information model in those templates matches it', None),
        ('catalog', 'Schemas cached for validate ("manage.py build_schema_catalog")', None),
        ('tested', 'A new External bundle passes PDS validate', None),
        (LIVE, 'Live on the production site', None),
        ('told', 'External bundle owners told', _notice_prepared),
    ],
    'django': [
        ('notes', 'Release notes read (security fixes; anything removed or changed)', None),
        ('pinned', 'Requirements updated to the new version', None),
        ('dev', 'Running on this server', _django_running),
        ('tests', 'Every test suite passes on it', None),
        (LIVE, 'Live on the production site', None),
    ],
    'gemini': [
        ('dropped', 'Model taken out of the Assistant\'s list', _model_dropped),
        (LIVE, 'Live on the production site', None),
    ],
}

# Who gets told once it's live. Sources not listed don't change anything for users.
NOTICES = {
    'validate': {
        'segment': 'all',
        'audience': 'everyone with an ELSA account',
        'subject': 'ELSA now checks bundles with PDS validate {version}',
        'banner': 'Bundle checks now use PDS validate {version}',
        'body': ('ELSA now checks bundles with PDS validate {version}, the newest release of the tool PDS uses '
                 'to check archive data.\n\nA check may report something it did not report before. If your bundle '
                 'page shows new findings, each one says what to change. Nothing about your bundles changed on '
                 'its own.'),
    },
    'pds4_im': {
        'segment': 'archive',
        'audience': 'owners of Archive bundles',
        'subject': 'PDS4 information model {dotted} is available in ELSA',
        'banner': 'Archive bundles can now use PDS4 information model {dotted}',
        'body': ('Archive bundles can now use PDS4 information model {dotted} ({version}). You can choose it '
                 'when you create a bundle.\n\nYour existing bundles keep the version they have; nothing about '
                 'them changed.'),
    },
    'ama_ldd': {
        'segment': 'external',
        'audience': 'owners of External bundles',
        'subject': 'External bundles now use the AMA dictionary {dotted}',
        'banner': 'External bundles now use the AMA dictionary {dotted}',
        'body': ('External (Atmospheric Modeling Annex) bundles now use the AMA dictionary {dotted}. Labels ELSA '
                 'writes from now on use it.\n\nIf a check on your bundle reports something new, the bundle page '
                 'says what to change.'),
    },
}


def source_of(case):
    return (case.data or {}).get('source', '')


def checklist_for(case):
    """[{'key', 'label', 'done', 'auto'}] for an outside update request, [] otherwise."""
    if case.kind != Case.UPDATE or (case.data or {}).get('unreadable'):
        return []
    ticked = case.checklist or {}
    items = []
    for key, label, check in CHECKLISTS.get(source_of(case), []):
        done = bool(check(case)) if check else bool(ticked.get(key))
        items.append({'key': key, 'label': label, 'done': done, 'auto': check is not None})
    return items


def is_live(case):
    return bool((case.checklist or {}).get(LIVE))


def notice_for(case):
    """The prepared words for this update's users, or None when it has none."""
    template = NOTICES.get(source_of(case))
    if template is None:
        return None
    version = case.data.get('version', '')
    dotted = version
    if source_of(case) == 'pds4_im':
        from .watchers import im_dotted
        dotted = im_dotted(version)
    elif source_of(case) == 'ama_ldd' and '_' in version:
        from .watchers import ldd_dotted
        dotted = ldd_dotted(version.split('_')[1])
    words = {'version': version, 'dotted': dotted}
    return {'segment': template['segment'], 'audience': template['audience'],
            'subject': template['subject'].format(**words),
            'banner': template['banner'].format(**words), 'body': template['body'].format(**words)}


def prepare_notice(request, case):
    """Draft email to the affected users, for staff to edit and send. Returns the draft."""
    _require(request, roles.WORK_CASES)
    if case.kind != Case.UPDATE:
        raise ActionError('Only outside updates have a user notice.')
    notice = notice_for(case)
    if notice is None:
        raise ActionError('This update changes nothing users do, so it has no notice.')
    if not is_live(case):
        raise ActionError('Tick "Live on the production site" first: users hear about it once it is live.')
    existing = Broadcast.objects.filter(pk=(case.data or {}).get('notice_broadcast') or 0).first()
    if existing is not None:
        return existing
    with transaction.atomic():
        draft = Broadcast.objects.create(subject=notice['subject'][:150], body=notice['body'],
                                         segment=notice['segment'], created_by=request.user)
        Case.objects.filter(pk=case.pk).update(data=dict(case.data, notice_broadcast=draft.pk))
        CaseNote.objects.create(case=case, kind=CaseNote.EVENT, author=request.user,
                                body='{0} prepared a draft email to users ("{1}"). Nothing is sent until someone '
                                     'sends it from Email users.'.format(request.user.username, draft.subject))
        audit.record(request, 'broadcast.save', changes={'id': draft.pk, 'subject': draft.subject,
                                                         'prepared_for': case.pk})
    case.refresh_from_db()
    return draft


def banner_url(case):
    """Announcements page with this update's banner filled in."""
    from django.utils.http import urlencode
    notice = notice_for(case)
    if notice is None:
        return ''
    return '{0}?{1}'.format(reverse('staff:announcements'), urlencode({'title': notice['banner'], 'body': ''}))
