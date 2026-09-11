"""Turn validate findings into things a data provider can act on.

This is the part of the feature that earns it. Running validate is plumbing; the
work is deciding, for each finding, who it is for, what it says in words someone
who does not read PDS4 can act on, and which part of ELSA fixes it.

Three audiences:

USER      Shown, grouped by the card that fixes it, and counted against submission.
ADVISORY  Shown, never blocking. Warnings a reviewer may waive, and anything the
          table does not recognise.
ELSA      Hidden from the data provider and collected for staff. A finding nobody
          outside the team can act on is not feedback, it is noise, and showing it
          teaches people the panel is not worth reading.

Anything unmatched falls to ADVISORY and is recorded as unmapped. That is
deliberate: a new PDS release, or a code path nobody anticipated, should add a line
to a staff report rather than put a wall in front of a user.

The rules are written from findings actually observed across real and generated
bundles, not from the message catalogue. Several match defects ELSA has since
fixed, because labels written before those fixes are still on disk and will report
them until they are rebuilt.
"""
import os
import re

USER = 'user'
ADVISORY = 'advisory'
ELSA = 'elsa'

# The cards on the bundle page, and the modal each one opens. Used to group findings
# by where they are fixed and to point the Fix button at the right place.
CARD_CITATION = ('Citation Information', 'citation_information_modal')
CARD_MOD_HISTORY = ('Modification History', 'mod_history_modal')
CARD_ALIAS = ('Alias', 'alias_modal')
CARD_CONTEXT = ('Context Products', 'context_modal')
CARD_DOCUMENT = ('Documents', 'document_modal')
CARD_DATA = ('Data Products', 'data_product_modal')
CARD_NETCDF = ('NetCDF Files', None)
CARD_AMA = ('Model Metadata', None)
CARD_NONE = (None, None)


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
        self.card, self.anchor = card
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
        return bool(self.types or self.path or self.message)


def reported_path(finding):
    """The path validate says a finding is about, with its file: scheme removed."""
    return (finding.get('label_path') or finding.get('label') or '').replace('file:', '').strip()


def is_directory_finding(finding):
    """Whether validate reported this against a directory rather than a file.

    It appends a separator to directory paths, so the reported path ends in one and
    its final segment is empty. That empty segment is itself an invalid PDS name,
    which is why validate then reports a bad name for directories whose names are
    perfectly legal: it fires on 8 of 25 sample reports, always on a collection
    directory nobody named by hand.
    """
    return reported_path(finding).endswith('/')


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

    Rule('missing-file', USER,
         'A file this label describes is not there',
         'The label points at a file that is missing from the bundle. Re-upload it, '
         'or remove the product that refers to it.',
         card=CARD_NETCDF,
         types=('missing_file',)),

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

    Rule('context-name-mismatch', ADVISORY,
         'A context product name differs from the one PDS publishes',
         'The reference resolves correctly, so this does not stop a submission. '
         'PDS records a longer official name than the one shown here.',
         types=('context_ref_mismatch',)),

    Rule('advisory-warning', ADVISORY,
         'Worth reviewing before submission',
         'Reported as a warning rather than an error, so a reviewer may accept it.',
         types=('warning',)),
]


UNMAPPED = Rule('unmapped', ADVISORY,
                'Other notes from PDS',
                'None of these stop your bundle going for review. They are technical '
                'notes rather than things to fix, and node staff will look at them '
                'with you. The Validation output tab has them in full.',
                collapse='rule')


def classify(finding):
    """The rule that covers this finding. Never returns None."""
    for rule in RULES:
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

        named = subject_name(finding) if rule.subject == 'path' else ''

        if rule.subject == 'path':
            # One item per offending name: two badly named files are two things to
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


def summarise(findings):
    """Counts for the panel: what blocks submission, what to review, what passed."""
    translated = translate(findings)
    return {
        'blocking': len(translated['user']),
        'advisory': len(translated['advisory']),
        'hidden': len(translated['elsa']),
        'unmapped': len(translated['unmapped']),
        'can_submit': not translated['user'],
    }


def cards(findings):
    """User-facing items grouped by the card that fixes them, in page order."""
    order = [CARD_CITATION[0], CARD_MOD_HISTORY[0], CARD_CONTEXT[0],
             CARD_DOCUMENT[0], CARD_NETCDF[0], CARD_AMA[0], CARD_DATA[0],
             CARD_ALIAS[0], None]

    grouped = {}
    for item in translate(findings)['user']:
        grouped.setdefault(item['card'], []).append(item)

    return [(card, grouped[card]) for card in order if card in grouped]
