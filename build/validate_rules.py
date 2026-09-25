
import os
import re

USER = 'user'
ADVISORY = 'advisory'
ELSA = 'elsa'

# The cards on the bundle page, and the modal each one opens. Used to group findings
# by where they are fixed and to point the Fix button at the right place.
# (name, modal to open, element to scroll to). Most things a user has to fix live
# behind a modal on the bundle page. The NetCDF files and the AMA panel do not:
# they are reached from the Collections card. Those used to carry no destination at
# all, so the panel told someone their file name was wrong and gave them no way to
# get to the file.
CARD_CITATION = ('Citation Information', 'citation_information_modal', None)
CARD_MOD_HISTORY = ('Modification History', 'mod_history_modal', None)
CARD_ALIAS = ('Alias', 'alias_modal', None)
CARD_CONTEXT = ('Context Products', 'context_modal', None)
CARD_DOCUMENT = ('Documents', 'document_modal', None)
CARD_DATA = ('Data Products', 'data_product_modal', None)
CARD_NETCDF = ('NetCDF Files', None, 'collections_card')
CARD_AMA = ('Model Metadata', None, 'collections_card')
CARD_NONE = (None, None, None)


class Rule(object):
    """One translation. Matched on the finding's type, where it is, and what it says.

    `collapse` is what makes several findings read as one problem, and it defaults
    to one item per rule for the whole bundle. PDS reports an empty collection four
    times over, and missing Citation Information once per label - four times on an
    Archive bundle. In both cases the person reading has exactly one thing to do, and
    a list that says it four times is a list they stop reading. Which labels were
    affected is kept on the item as detail rather than as repetition.

    'label' is available for a problem that genuinely differs from one label to the
    next, and 'finding' for unmapped findings, where the message is all we have.
    """

    def __init__(self, key, audience, title, detail, card=CARD_NONE,
                 types=(), path=(), message=(), collapse='rule', subject=None,
                 when=None):
        self.key = key
        self.audience = audience
        self.title = title
        self.detail = detail
        self.card, self.anchor, self.scroll_to = card
        self.types = types
        self.path = path
        self.message = message
        self.collapse = collapse
        # 'path' means the finding is about a named thing on disk rather than about
        # a place in a label, so the item has to say which thing. Without it a rule
        # like bad-filename can only say "a file name", which is not actionable.
        self.subject = subject
        # An extra predicate for the cases the type/path/message triple cannot
        # separate. Used where the same PDS message means two different things
        # depending on what it is reported against.
        self.when = when

    def matches(self, finding):
        if self.types and not any(finding['type'].endswith(t) for t in self.types):
            return False
        if self.path and not any(p in finding['element_path'] for p in self.path):
            return False
        if self.message and not any(
                re.search(m, finding['message'], re.I) for m in self.message):
            return False
        if self.when is not None and not self.when(finding):
            return False
        return bool(self.types or self.path or self.message or self.when)


def reported_path(finding):
    """The path validate says a finding is about, with its file: scheme removed."""
    return (finding.get('label_path') or finding.get('label') or '').replace('file:', '').strip()


def is_warning(finding):
    return (finding.get('severity') or '').upper() == 'WARNING'


def in_document_collection(finding):
    """Whether the label sits directly in the bundle's document collection.

    Compared on the directory's name rather than searched for in the path, which
    also matched a bundle or user whose name merely contains "document".
    """
    return os.path.basename(os.path.dirname(reported_path(finding))) == 'document'


def is_directory_finding(finding):
    """Whether validate reported this against a directory rather than a file.

    It appends a separator to directory paths, so the reported path ends in one and
    its final segment is empty. That empty segment is itself an invalid PDS name,
    which is why validate then reports a bad name for directories whose names are
    perfectly legal: it fires on 8 of 25 sample reports, always on a collection
    directory nobody named by hand.
    """
    return reported_path(finding).endswith('/')


# PDS quotes the offending value in the message: "Value 'Raul' is not facet-valid".
_QUOTED_VALUE = re.compile(r"Value '([^']*)'")


def offending_value(finding):
    """The value PDS rejected, pulled out of its own message.

    Some rules are about a value rather than a place, and the value is the only
    thing that identifies which of several authors needs editing.
    """
    match = _QUOTED_VALUE.search(finding.get('message') or '')
    return match.group(1) if match else ''


def subject_name(finding):
    """The thing on disk a finding is about, as short a name as still identifies it.

    PDS reports a bad name with the offending name nowhere in the message, so the
    only handle on it is the path validate was looking at. That path is absolute and
    can end in a separator when it is a directory, which is why it is stripped before
    the basename is taken.
    """
    raw = reported_path(finding).rstrip('/')
    if not raw:
        return ''
    return os.path.basename(raw) or raw


# Order matters: the first rule that matches wins, so the specific ones come first.
RULES = [

    # ---- things the person looking at the screen can fix -----------------------

    Rule('citation-missing', USER,
         'Add Citation Information, including a description',
         'PDS needs to know who produced this data and what it is, so it can be '
         'cited and found later. The description is the part reviewers read first.',
         card=CARD_CITATION,
         message=(r'Citation_Information and its description are required',)),

    # The document collection is created for every bundle and, unlike the ones the
    # user adds, cannot be deleted: delete_collection only handles
    # AdditionalCollections. Offering to remove it, as the general rule below does,
    # is advice nobody can follow. Measured on a bundle built from real EPIC model
    # output, this is the only thing left standing between an AMA delivery and a
    # clean validation: every other finding was ELSA's and is fixed.
    Rule('document-collection-empty', USER,
         'The document collection has nothing in it',
         'Every bundle gets a document collection, and PDS will not accept one that '
         'is empty. Add at least one document describing the data: how it was '
         'produced, what the variables mean, or how to read it.',
         card=CARD_DOCUMENT,
         path=('File_Area_Inventory/Inventory',),
         message=(r"Value '0' is not facet-valid", r"value '0' of element",
                  r"must have no element"),
         when=lambda finding: (finding.get('label') or '').endswith('_document.xml')),

    Rule('collection-empty', USER,
         'This collection has nothing in it yet',
         'Every collection has to list at least one product. Add a file to it, or '
         'remove the collection if you do not need it.',
         card=CARD_DOCUMENT,
         path=('File_Area_Inventory/Inventory',),
         message=(r"Value '0' is not facet-valid", r"value '0' of element",
                  r"must have no element")),

    # Everything about a context product's label is written by ELSA from the row the
    # crawler stored. The person reading the panel chose a target from a list; they
    # did not type its type, its reference_type, or its identifier, and no amount of
    # choosing differently in the UI will change what ELSA writes. These used to be
    # USER rules telling them to "choose one from the list rather than typing a
    # value" for a value they had never been offered, let alone typed.
    Rule('context-reference-type', ELSA,
         'ELSA wrote a reference type PDS does not accept',
         'The kind of link between this label and a context product is written by '
         'ELSA, not chosen by you.',
         path=('Target_Identification', 'Investigation_Area', 'Observing_System',
               'Reference_List'),
         message=(r'reference_type must be set to one of',)),

    Rule('context-vocabulary', ELSA,
         'ELSA wrote a context value PDS does not accept',
         'Target and investigation types come from the PDS registry through ELSA, '
         'not from anything you entered.',
         path=('Target_Identification', 'Investigation_Area'),
         message=(r'must be equal to one of the following values',)),

    Rule('context-area-blank', ELSA,
         'ELSA left part of the context area empty',
         'A collection label carries its own copy of the investigation, and ELSA '
         'shipped it blank.',
         path=('Investigation_Area', 'Target_Identification', 'Observing_System'),
         message=(r"The value '' of element", r"Value '' ", r"length = '0'")),

    Rule('ama-vocabulary', USER,
         'A model metadata value is not one PDS accepts',
         'Open the model metadata panel and choose a value from the dropdown rather '
         'than typing one. These fields are closed lists, and a value ELSA does not '
         'recognise is marked when the panel is next opened.',
         card=CARD_AMA,
         path=('AMA', 'Model_Metadata', 'Simulation_Configuration'),
         message=(r'must be equal to one of the following values',)),

    Rule('non-latin-name', USER,
         'A name uses a character PDS cannot store',
         'PDS records author and editor names in plain unaccented letters only, so '
         'an accent, an umlaut or a tilde is refused however correctly it is '
         'spelled. Open Citation Information and rename the author without it: '
         'Morales-Juberias for Morales-Juber\u00edas, Raul for Ra\u00fal. It is a '
         'limitation of the archive format rather than a judgement about the name.',
         card=CARD_CITATION,
         path=('given_name', 'family_name', 'Person', 'List_Author', 'List_Editor'),
         message=(r'IsBasicLatin',),
         # One row per name, because two authors are two edits.
         subject='value'),

    Rule('citation-author-blank', USER,
         'An author or editor on the citation is missing details',
         'Open Citation Information and fill in the name of each author and editor '
         'you have added, or remove the ones you do not need. A blank entry is not '
         'something PDS can record.',
         card=CARD_CITATION,
         path=('Citation_Information',),
         message=(r"Value '' ", r"The value '' of element", r"length = '0'")),

    # Reported against a directory, which means validate's own trailing separator
    # rather than anything in the bundle. Never shown to the user: it appears on
    # essentially every bundle and names a folder ELSA created, so the old wording
    # told people to rename and re-upload a file that does not exist.
    Rule('directory-name-artifact', ELSA,
         'PDS reported a bad name against a directory',
         'validate appends a separator to directory paths and then reads the empty '
         'final segment as a name. Nothing in the bundle is wrong.',
         types=('name_has_invalid_characters',),
         when=is_directory_finding),

    Rule('file-name-needs-extension', USER,
         'A file name in a document needs a file extension',
         'PDS file names must end in a dot and an extension, like guide.pdf or '
         'readme.txt. Open this document and rename its file so the name ends in '
         'the extension the file actually has.',
         card=CARD_DOCUMENT,
         path=('File_Area_External/File/file_name', 'File_Area_Text/File/file_name',
               'File_Area_Binary/File/file_name'),
         message=(r"is not facet-valid with respect to pattern",
                  r"of element 'file_name' is not valid"),
         # One row per bad name, because two badly named documents are two edits.
         subject='path'),

    # The bundle label's member list is written by ELSA alone: creating a collection
    # adds its entry, deleting one removes it. Nobody can edit it from the bundle
    # page. So a member listed twice, or listed but gone, is ELSA's bookkeeping, not
    # two documents with the same name, which is what the rule below used to tell
    # people when a recreated collection was listed three times.
    Rule('elsa-bundle-members', ELSA,
         'The bundle label lists a collection twice, or one that no longer exists',
         'Deleting a collection used to leave its entry in the bundle label, and '
         'creating one of the same name again added another. Fixed; bundles written '
         'before the fix are repaired with manage.py repair_labels --apply.',
         types=('duplicate_lidvid', 'member_not_found'),
         when=lambda finding: (finding.get('label') or '').startswith('bundle_')),

    Rule('duplicate-member', USER,
         'Two products in a collection have the same identifier',
         'A collection cannot list the same product twice. This usually means two '
         'documents were given the same name: rename one of them, or delete the '
         'copy you do not need.',
         card=CARD_DOCUMENT,
         message=(r'Inventory contains \d+ instances of LIDVID',)),

    Rule('bad-filename', USER,
         'A file name contains a character PDS does not allow',
         'PDS file names may use letters, digits, dots, dashes and underscores. '
         'Rename this file and upload it again; a space is the usual culprit.',
         card=CARD_NETCDF,
         types=('name_has_invalid_characters',),
         # PDS puts the name nowhere in the message and leaves the element path
         # empty, so the only handle on it is the path validate was looking at.
         # Without showing it the row said "a file name" and nothing more.
         subject='path'),

    # A document's file is missing because ELSA has no way to attach one: neither
    # document form carries a FileField and no template offers a file input, so a
    # document can be declared but its file never uploaded. Telling the submitter to
    # "re-upload it" names an action the product does not offer, and with the
    # submission gate on it would block every bundle containing a document. It is
    # ELSA's gap, so it is counted against ELSA and reported to the node rather than
    # to the person who cannot act on it.
    Rule('document-file-missing', ELSA,
         'A document has no file, because ELSA cannot attach one',
         'ELSA records a document and its file name but offers no way to upload the '
         'file itself, so the label names a file that is not in the bundle.',
         types=('missing_file',),
         when=in_document_collection,
         subject='path'),

    Rule('missing-file', USER,
         'A file this label describes is not there',
         'The label points at a file that is missing from the bundle. Upload it '
         'again, or remove the product that refers to it.',
         card=CARD_NETCDF,
         types=('missing_file',),
         subject='path'),

    # ---- ELSA's own output: never shown to a data provider ---------------------

    Rule('elsa-empty-element', ELSA,
         'An element ELSA wrote without a value',
         'Every user-facing rule is tried first, so an empty element reaching here is '
         'one ELSA emitted and never filled. Matched on the shape of the message '
         'rather than on a list of paths, because the paths kept being ones nobody '
         'had thought of and each new one arrived as unexplained noise in front of a '
         'user. Generated labels carry these until they are rebuilt.',
         message=(r"Value '' ", r"The value '' of element", r"with length = '0'",
                  r"'' is not a valid value", r"must have no element \[children\]",
                  r"is not complete")),

    Rule('elsa-version-stamp', ELSA,
         'Information model version does not match the schema referenced',
         'Written by the version stamping code, not by anything a user controls.',
         types=('schematron',),
         path=('information_model_version',),
         message=(r'information_model_version must be equal',)),

    Rule('elsa-empty-container', ELSA,
         'An empty container that ELSA wrote',
         'A container ELSA emits without filling. Fixed in the generator; labels '
         'written earlier still carry it until they are rebuilt.',
         path=('Time_Coordinates', 'Observing_System', 'Investigation_Area',
               'Modification_History'),
         message=(r"Value '' ", r"with length = '0'", r'is not complete',
                  r"The value '' of element")),

    Rule('elsa-inventory', ELSA,
         'The collection inventory does not describe a real file',
         'Written by the inventory generator. Fixed; older labels still carry it.',
         path=('File_Area_Inventory/File',),
         message=(r"Value '' ", r"'' is not a valid value", r"The value '' of element")),

    # Every logical identifier in an ELSA bundle is composed by ELSA, never typed.
    # The one a user could influence, through the bundle name or bundle ID, is run
    # through models.pds_lid_segment before it reaches a LID, as are the collection
    # and file name segments. So this firing means ELSA wrote an identifier PDS4 does
    # not permit, which is a bug here and not something to hand to a data provider.
    #
    # It was reaching people as "No plain-language wording yet" attached to a raw
    # cvc-pattern-valid dump quoting \p{Ll} and \p{Nd}, on a label whose only sin was
    # that the file had been copied on Windows and arrived called "- Copy".
    Rule('elsa-lid-pattern', ELSA,
         'ELSA built an identifier PDS does not allow',
         'A PDS4 identifier may contain only lowercase letters, digits, hyphen, dot '
         'and underscore. ELSA composes every identifier in this bundle from the '
         'bundle, collection and file names, so this is ours to correct; the file '
         'name itself is allowed to be mixed case and does not need renaming.',
         path=('Identification_Area/logical_identifier',),
         message=(r'cvc-pattern-valid', r"of element 'logical_identifier' is not valid")),

    Rule('elsa-context-lid', ELSA,
         'A context reference ELSA built is malformed',
         'The LID is composed by ELSA from stored context products, so a user '
         'cannot correct it from the bundle page.',
         path=('Investigation_Area/Internal_Reference',
               'Reference_List/Internal_Reference'),
         message=(r'number of colons found', r'must start with either',
                  r"Value '' is not facet-valid")),

    Rule('elsa-context-missing', ELSA,
         'A referenced context product is not registered with PDS',
         'Either the stored LID is wrong or the product needs registering. Neither '
         'is something the bundle owner can do from here.',
         types=('context_ref_not_found',)),

    Rule('elsa-internal', ELSA,
         'validate could not finish checking a product',
         'Usually a label describing a data file that is not where it says it is.',
         types=('internal_error', 'unknown_value')),

    # ---- worth seeing, never blocking ------------------------------------------

    # Every context product in a label is written by ELSA from a row its crawler
    # stored, so a name that disagrees with what PDS publishes is our data being
    # wrong, never the user's. It used to be shown to them as "PDS records a longer
    # official name than the one shown here", which named no action because there is
    # none they can take. ELSA now writes the published title, so this should not
    # occur for investigations at all; it stays as a net for the other context
    # products, counted against us rather than shown to them.
    Rule('context-name-mismatch', ELSA,
         'ELSA wrote a context name PDS does not publish',
         'Context names come from the PDS registry through ELSA, not from anything '
         'the user entered.',
         types=('context_ref_mismatch',)),

    Rule('advisory-warning', ADVISORY,
         'PDS noted something, but it does not block you',
         'There is nothing for you to do about this before submitting. PDS raised '
         'it as a warning rather than an error, which means your bundle is '
         'acceptable as it stands; node staff will decide during review whether it '
         'needs anything. The Validation output tab has the exact wording.',
         # On severity: validate's warning types all start with "warning." and none
         # ends with it, so matching the type never fired.
         when=is_warning),
]


UNMAPPED = Rule('unmapped', ADVISORY,
                'Other notes from PDS',
                'There is nothing for you to do about these. None of them stop your '
                'bundle going for review: they are technical notes rather than '
                'things to fix, and node staff will look at them with you. The '
                'Validation output tab has them in full if you want to see them.',
                collapse='rule')


def classify(finding):
    """The rule that covers this finding. Never returns None."""
    # Only an error can be the user's to fix before submitting. PDS raises some
    # schema and schematron findings as warnings, whose message and path can look
    # exactly like a user rule's, and a warning does not stop PDS accepting a bundle.
    # A finding recorded without a severity is treated as an error.
    warning = (finding.get('severity') or 'ERROR').upper() != 'ERROR'
    for rule in RULES:
        if warning and rule.audience == USER:
            continue
        if rule.matches(finding):
            return rule
    return UNMAPPED


def translate(findings):
    """Group findings into the items a person should see, plus what to hide.

    Returns {'user': [...], 'advisory': [...], 'elsa': [...], 'unmapped': [...]}.
    Each item carries the plain-language wording, the card that fixes it, and the
    raw findings behind it so the technical detail is always one click away rather
    than gone.
    """
    buckets = {USER: {}, ADVISORY: {}, ELSA: {}}
    unmapped = []

    for finding in findings or []:
        rule = classify(finding)
        if rule is UNMAPPED:
            unmapped.append(finding)

        if rule.subject == 'path':
            named = subject_name(finding)
        elif rule.subject == 'value':
            named = offending_value(finding)
        else:
            named = ''

        if rule.subject:
            # One item per offending thing: two badly named files are two things to
            # rename, and collapsing them into one row hides the second.
            key = (rule.key, named)
        elif rule.collapse == 'rule':
            key = rule.key
        elif rule.collapse == 'finding':
            key = (rule.key, finding['message'])
        else:
            key = (rule.key, finding['label'])

        bucket = buckets[rule.audience]
        if key not in bucket:
            bucket[key] = {
                'key': rule.key,
                'title': rule.title,
                'detail': rule.detail,
                'card': rule.card,
                'anchor': rule.anchor,
                'scroll_to': rule.scroll_to,
                'audience': rule.audience,
                'subject': named,
                'labels': [],
                'findings': [],
            }
        item = bucket[key]
        item['findings'].append(finding)
        if finding['label'] and finding['label'] not in item['labels']:
            item['labels'].append(finding['label'])

    def ordered(bucket):
        # Most-reported first, so the biggest thing to fix is at the top.
        return sorted(bucket.values(), key=lambda i: (-len(i['findings']), i['title']))

    return {
        'user': ordered(buckets[USER]),
        'advisory': ordered(buckets[ADVISORY]),
        'elsa': ordered(buckets[ELSA]),
        'unmapped': unmapped,
    }


def raw_groups(findings):
    """PDS's own output, grouped by the thing each finding is about.

    The raw tab exists so nobody has to take the translation on trust. Printed one
    finding per line it does not serve that: PDS reports most defects twice, once as
    a pattern failure and once as a type failure, on the same element and the same
    line, so a bundle with four problems reads as eight errors and looks twice as
    bad as it is.

    Grouped by (label, element, line) instead, with every message PDS produced kept
    underneath, and each group labelled with the plain-language item it became. That
    is what makes the tab worth opening: it shows the translation is honest, and it
    shows what was folded into what.

    Returns [(label, [group, ...]), ...] with labels in a stable order.
    """
    grouped = {}

    for finding in findings or []:
        label = finding.get('label') or 'bundle'
        key = (label, finding.get('element_path') or '', finding.get('line'))
        group = grouped.get(key)
        if group is None:
            rule = classify(finding)
            group = grouped[key] = {
                'label': label,
                'element_path': finding.get('element_path') or '',
                'line': finding.get('line'),
                'severity': finding.get('severity') or '',
                'type': finding.get('type') or '',
                'messages': [],
                # What this became on the other tab, so the two can be compared.
                'audience': rule.audience,
                'shown_as': rule.title if rule is not UNMAPPED else '',
                'is_unmapped': rule is UNMAPPED,
            }
        if finding.get('message') and finding['message'] not in group['messages']:
            group['messages'].append(finding['message'])
        # An ERROR anywhere in the group outranks a WARNING for the badge.
        if finding.get('severity') == 'ERROR':
            group['severity'] = 'ERROR'

    by_label = {}
    for group in grouped.values():
        by_label.setdefault(group['label'], []).append(group)

    for groups in by_label.values():
        groups.sort(key=lambda g: (g['line'] or 0, g['element_path']))

    return sorted(by_label.items())


def merge_user(user_items, extra):
    """Fold ELSA's own requirement items in with the ones PDS reported.

    Deduplicated on key: an unfilled citation reaches the validator as blank elements
    *and* is absent from the database, so without this it would be listed twice, once
    from each source, saying the same thing. Requirements sort first because they are
    the coarser problem: telling someone their citation has a blank author is noise
    while they have no citation at all.
    """
    seen = set(item['key'] for item in user_items)
    requirements = [item for item in extra if item['key'] not in seen]
    return requirements + list(user_items)


def summarise(findings, extra=()):
    """Counts for the panel: what blocks submission, what to review, what passed.

    `extra` is build.preflight's requirement items. They count as blocking, because
    they are: the Review & Submit button has always refused a bundle without them.
    Leaving them out is what let the panel say "passed" on a bundle ELSA would not
    accept.
    """
    translated = translate(findings)
    user = merge_user(translated['user'], extra)
    return {
        'blocking': len(user),
        'advisory': len(translated['advisory']),
        'hidden': len(translated['elsa']),
        'unmapped': len(translated['unmapped']),
        'can_submit': not user,
    }


def cards(findings, extra=()):
    """User-facing items grouped by the card that fixes them, in page order."""
    order = [CARD_CITATION[0], CARD_MOD_HISTORY[0], CARD_CONTEXT[0],
             CARD_DOCUMENT[0], CARD_NETCDF[0], CARD_AMA[0], CARD_DATA[0],
             CARD_ALIAS[0], None]

    grouped = {}
    for item in merge_user(translate(findings)['user'], extra):
        grouped.setdefault(item['card'], []).append(item)

    return [(card, grouped[card]) for card in order if card in grouped]
