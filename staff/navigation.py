# -*- coding: utf-8 -*-
"""Admin Console navigation. The side menu, tabs, page titles and help all read this table.

Tabs with a permission are hidden from roles without it. The page still checks it too.
"""
from __future__ import unicode_literals

from django.urls import reverse

from . import roles

# (key, label, icon, [(section, tab label, url name, permission or None)])
DESTINATIONS = (
    ('today', 'Today', 'bi-sun', [
        ('today', 'Today', 'staff:today', None),
    ]),
    ('requests', 'Requests', 'bi-inbox', [
        ('queue', 'Requests', 'staff:home', None),
    ]),
    ('reviews', 'Reviews', 'bi-kanban', [
        ('pipeline', 'Pipeline', 'staff:pipeline', None),
        ('projects', 'Projects', 'staff:projects', None),
        ('campaigns', 'Peer reviews', 'staff:campaigns', None),
    ]),
    ('people', 'People and bundles', 'bi-people', [
        ('users', 'Users', 'staff:users', None),
        ('bundles', 'Bundles', 'staff:bundles', None),
        ('stalled', 'Stalled bundles', 'staff:stalled', None),
    ]),
    ('team', 'Team', 'bi-chat-square-text', [
        ('replies', 'Saved replies', 'staff:saved_replies', None),
        ('knowledge', 'Knowledge notes', 'staff:knowledge', None),
        ('workload', 'Workload', 'staff:workload', None),
        ('announcements', 'Announcements', 'staff:announcements', roles.MANAGE_ACCOUNTS),
        ('broadcasts', 'Email users', 'staff:broadcasts', roles.MANAGE_ACCOUNTS),
    ]),
    ('oversight', 'Oversight', 'bi-graph-up', [
        ('updates', 'Updates', 'staff:updates', None),
        ('reports', 'Reports', 'staff:reports', None),
        ('insights', 'Validation insights', 'staff:insights', None),
        ('mail', 'Email log', 'staff:mail_log', None),
        ('audit', 'Audit log', 'staff:audit', roles.VIEW_AUDIT),
        ('health', 'System health', 'staff:health', None),
        ('settings', 'Settings', 'staff:settings', roles.MANAGE_ACCOUNTS),
    ]),
)

# Pages that belong to a place without being one of its tabs.
EXTRA_SECTIONS = {'search': 'requests', 'spam': 'requests', 'help': None, 'preferences': None}


def destination_of(section):
    for key, _label, _icon, tabs in DESTINATIONS:
        if any(tab[0] == section for tab in tabs):
            return key
    return EXTRA_SECTIONS.get(section)


def build(user, section):
    """The side navigation, this page's tabs, and a default page title."""
    current = destination_of(section)
    places, tabs_here, title = [], [], ''
    for key, label, icon, tabs in DESTINATIONS:
        visible = [{'section': s, 'label': tab_label, 'url': reverse(name), 'active': s == section}
                   for s, tab_label, name, perm in tabs if perm is None or roles.has(user, perm)]
        if not visible:
            continue
        places.append({'key': key, 'label': label, 'icon': icon, 'url': visible[0]['url'],
                       'active': key == current})
        if key == current:
            tabs_here = visible if len(visible) > 1 else []
            title = next((tab['label'] for tab in visible if tab['active']), label)
    return {'places': places, 'tabs': tabs_here, 'title': title}
