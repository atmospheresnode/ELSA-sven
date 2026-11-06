# -*- coding: utf-8 -*-
"""Who can do what in the Admin Console.

Roles are Django groups with the permissions on Case.Meta. Only `manage.py grant_staff`
can grant them. Superusers are the ELSA developers and have everything.
"""
from __future__ import unicode_literals

from collections import OrderedDict


APP = 'staff'

VIEW_CONSOLE = 'view_console'
WORK_CASES = 'work_cases'
DECIDE_AGENCY = 'decide_agency'
REVIEW_BUNDLES = 'review_bundles'
MANAGE_ACCOUNTS = 'manage_accounts'
VIEW_AUDIT = 'view_audit'

# Each role includes the one above it. Used by the migration and the grant command.
ROLES = OrderedDict([
    ('viewer', {
        'group': 'ELSA Viewer',
        'label': 'Viewer',
        'summary': 'Reads everything in the Admin Console; changes nothing.',
        'perms': (VIEW_CONSOLE,),
    }),
    ('curator', {
        'group': 'ELSA Curator',
        'label': 'Curator',
        'summary': 'Works the queue: answers requests, changes agencies, reviews bundles.',
        'perms': (VIEW_CONSOLE, WORK_CASES, DECIDE_AGENCY, REVIEW_BUNDLES, VIEW_AUDIT),
    }),
    ('account_admin', {
        'group': 'ELSA Account Admin',
        'label': 'Account admin',
        'summary': 'Everything a curator can do, plus disabling accounts and signing users out.',
        'perms': (VIEW_CONSOLE, WORK_CASES, DECIDE_AGENCY, REVIEW_BUNDLES, VIEW_AUDIT,
                  MANAGE_ACCOUNTS),
    }),
])

GROUP_NAMES = tuple(role['group'] for role in ROLES.values())


def perm(codename):
    return '{0}.{1}'.format(APP, codename)


def has(user, codename):
    """Whether an active, signed-in user holds a console permission."""
    if user is None or not user.is_authenticated or not user.is_active:
        return False
    return user.has_perm(perm(codename))


def is_member(user):
    """Whether this account may use the Admin Console at all."""
    return has(user, VIEW_CONSOLE)


def roles_for(user):
    """Role keys the user holds, plus 'superuser' for the developers."""
    if user is None or not user.is_authenticated:
        return []
    held = set(user.groups.filter(name__in=GROUP_NAMES).values_list('name', flat=True))
    keys = [key for key, role in ROLES.items() if role['group'] in held]
    if user.is_superuser:
        keys.append('superuser')
    return keys


def role_label(user):
    """The highest role, for display."""
    keys = roles_for(user)
    if 'superuser' in keys:
        return 'Superuser'
    for key in reversed(list(ROLES)):
        if key in keys:
            return ROLES[key]['label']
    return ''


def sync_groups(**kwargs):
    """Create the role groups and set their permissions to the table above. Runs after every migrate."""
    from django.contrib.auth.models import Group, Permission

    wanted = {codename for role in ROLES.values() for codename in role['perms']}
    found = {p.codename: p for p in Permission.objects.filter(content_type__app_label=APP,
                                                              codename__in=wanted)}
    if len(found) < len(wanted):
        # Permissions may not exist yet on this run, then there's nothing to assign.
        return
    for role in ROLES.values():
        group, _ = Group.objects.get_or_create(name=role['group'])
        group.permissions.set([found[codename] for codename in role['perms']])
