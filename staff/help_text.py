# -*- coding: utf-8 -*-
"""The "?" help on each Admin Console page, keyed by section or help_topic."""
from __future__ import unicode_literals

GLOSSARY = (
    ('Request', 'Anything a user asked of the team: an agency change, a bundle submission, a contact '
                'form, a storage question, a message. Every request has a number, like #12.'),
    ('Stage', 'Where a submitted bundle is in review: Submitted, Under review, Changes requested, '
              'Accepted, Released or Not accepted. The owner sees the stage on their bundle page.'),
    ('Lien', 'A problem a reviewer found that must be fixed before the bundle can be accepted. '
             'It is the PDS term. The owner sees each lien and can mark it fixed; you check and close it.'),
    ('Waiting on user', 'The team has answered and the next move is the user\'s. Their reply brings '
                        'the request back to the top.'),
    ('Admin mode', 'The Admin Console opens with a passkey tap and closes by itself after 30 quiet minutes or '
                   '8 hours. The most serious actions ask for one more tap.'),
    ('View as user', 'See ELSA exactly as one user sees it, read-only, for 15 minutes. Every page you '
                     'open is recorded, and the developers are told.'),
)

HELP = {
    'today': ('Today', [
        'Your starting point: requests that need you, reviews you could pick up, and releases part way through.',
        'A request shows here when nobody has taken it, when it is yours and you have not opened it, or when the user answered.',
        'Opening a request clears it from this list.',
    ]),
    'queue': ('Requests', [
        'Every request users have sent the team. "For you" is what needs you; "All open" is everything still to do.',
        'Tick several requests to assign or resolve them at once.',
        'More filters has first replies due, spam and the rest. Save a filter you use often with "Save this view".',
    ]),
    'case': ('A request', [
        'The Next step box shows only what fits this request right now. Everything else is under More actions.',
        'Reply sends a message the user sees in ELSA and by email. A note is for the team only.',
        'For a bundle submission, moving the stage tells the owner. Changes requested and Not accepted need a message.',
        'A lien is one problem to fix before acceptance. The owner can mark it fixed; you close it after checking.',
    ]),
    'pipeline': ('Pipeline', [
        'Every submitted bundle by review stage. Cards that have not moved for a while are marked as stuck.',
        'Open a card to review it, or filter by project, type or reviewer.',
    ]),
    'projects': ('Projects', [
        'A project groups the bundles of one mission or data provider, with milestones and the staff who look after it.',
    ]),
    'campaigns': ('Peer reviews', [
        'Invite outside reviewers to a bundle. They get a private link, need no ELSA account, and their findings become liens.',
    ]),
    'users': ('Users', [
        'Find any ELSA account. Open one to see their bundles and requests, help with sign-in, or write to them.',
    ]),
    'user': ('An account', [
        'Changes to an account are recorded and the user is told. Signing out everywhere, removing a passkey and View as user ask for a passkey tap.',
        'Staff accounts cannot be changed here. The developers handle those on the server.',
    ]),
    'bundles': ('Bundles', [
        'Every bundle in ELSA. Open one to read its files, its labels and its PDS validate results.',
    ]),
    'stalled': ('Stalled bundles', [
        'Bundles that went quiet, are ready but never submitted, or are waiting on the owner after you asked for changes.',
        'Tick bundles and send a friendly nudge. Nobody is nudged twice in two weeks.',
    ]),
    'replies': ('Saved replies', [
        'Text the team sends often. Insert one into any reply box, then edit it before sending.',
    ]),
    'knowledge': ('Knowledge notes', [
        'How the node handles a mission, a format or a recurring question, written down once for the whole team.',
    ]),
    'workload': ('Workload', [
        'Who has what open, and how quickly first replies go out. Hand someone\'s requests over when they are away.',
    ]),
    'announcements': ('Announcements', [
        'A banner every ELSA user sees until it ends or they dismiss it. For outages and news.',
    ]),
    'broadcasts': ('Email users', [
        'Email a group of users. You see the list and a preview before anything is sent.',
    ]),
    'updates': ('Updates', [
        'ELSA checks every morning for new versions of PDS validate, the PDS4 information model, the AMA dictionary, Django and the Assistant\'s models.',
        'Something new opens an "Outside update" request: everyone sees it under the bell, and gets an email unless they switched that off under My preferences.',
        'The request has a checklist for rolling it out. Items ELSA can check tick themselves; "Live on the production site" is yours to tick.',
        'Once it is live, the request can prepare a draft email and banner for the users it affects. Nothing is sent until you send it.',
    ]),
    'reports': ('Reports', [
        'Numbers for the node: requests, reply times, submissions and releases over a period. Download them as CSV.',
    ]),
    'insights': ('Validation insights', [
        'The most common things PDS validate asks users to fix. Good topics for a saved reply or a knowledge note.',
    ]),
    'mail': ('Email log', [
        'Every email ELSA sent, so you can tell a user exactly when something went to them.',
    ]),
    'audit': ('Audit log', [
        'Every staff action, who did it and why. Entries are sealed: the nightly check finds any that were changed.',
    ]),
    'health': ('System health', [
        'How the server is doing: storage, PDS validate, who is in admin mode, email, and the security checks.',
    ]),
    'settings': ('Console settings', [
        'Team-wide rules, such as how many days a first reply may take. Every change is recorded and the developers are told.',
    ]),
    'release': ('A release', [
        'Releasing goes step by step: final validate, package, DOI, delivery, registry, then Released. Each step unlocks the next.',
        'The owner is told when the bundle is released, with its DOI.',
    ]),
    'preferences': ('My preferences', [
        'Which emails the Admin Console sends you, and when you are away (you are left out of the morning summary while away).',
    ]),
}


def for_topic(topic):
    item = HELP.get(topic)
    if item is None:
        return None
    return {'title': item[0], 'lines': item[1]}
