
import os
import re

USER = 'user'
ADVISORY = 'advisory'
ELSA = 'elsa'

# Cards on the bundle page, as (name, modal to open, element to scroll to).
# Used to group findings and aim the Fix button. NetCDF files and the AMA panel are
# reached from the Collections card.
CARD_CITATION = ('Citation Information', 'citation_information_modal', None)
CARD_MOD_HISTORY = ('Modification History', 'mod_history_modal', None)
CARD_ALIAS = ('Alias', 'alias_modal', None)
CARD_CONTEXT = ('Context Products', 'context_modal', None)
CARD_DOCUMENT = ('Documents', 'document_modal', None)
CARD_DATA = ('Data Products', 'data_product_modal', None)
CARD_NETCDF = ('NetCDF Files', None, 'collections_card')
# An existing document is fixed from its edit page, through the Collections card.
CARD_DOCUMENT_FILES = ('Documents', None, 'collections_card')
CARD_AMA = ('Model Metadata', None, 'collections_card')
CARD_NONE = (None, None, None)
# For what only the team can act on: no form to open, so no Fix button.
CARD_SUPPORT = ('ELSA team', None, None)


class Rule(object):
    """One translation, matched on the finding's type, path and message.

    `collapse` defaults to one item per rule for the whole bundle, since PDS repeats itself.
    Use 'label' if it differs per label, and 'finding' for unmapped ones.
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
        # 'path' means the finding is about something on disk, so the item names it.
        self.subject = subject
        # Extra check for when the same PDS message means different things.
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


def _is_error(finding):
    """True for ERROR or FATAL_ERROR. validate uses FATAL_ERROR for schematron failures."""
    return (finding.get('severity') or 'ERROR').upper() in ('ERROR', 'FATAL_ERROR')


def in_document_collection(finding):
    """Whether the label is directly in the document collection, by directory name."""
    return os.path.basename(os.path.dirname(reported_path(finding))) == 'document'


def is_directory_finding(finding):
    """Whether validate reported this against a directory (the path ends in a separator)."""
    return reported_path(finding).endswith('/')


# PDS quotes the offending value in the message: "Value 'Raul' is not facet-valid".
_QUOTED_VALUE = re.compile(r"Value '([^']*)'")


def offending_value(finding):
    """The value PDS rejected, from its message."""
    match = _QUOTED_VALUE.search(finding.get('message') or '')
    return match.group(1) if match else ''


def subject_name(finding):
    """The file or folder a finding is about, from the path validate was checking."""
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

    # The document collection can't be deleted, so don't suggest removing it.
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

    # ELSA writes all of a context product's label from the crawler's row, so these aren't the user's to fix.
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

    # Reported against a directory ELSA made, from validate's trailing separator. Not shown to the user.
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

    # Only ELSA writes the member list, so a repeat or missing member is ours.
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
         # PDS doesn't give the name, so get it from the path validate was checking.
         subject='path'),

    # Label names a file that isn't in the bundle. Fixed from the document's edit page.
    Rule('document-file-missing', USER,
         'Attach the file for this document',
         'The document is described, but its file is not in the bundle yet. In the '
         'Collections card, open the document with Edit and upload its file: a PDF/A-1 '
         '(.pdf) or a plain text (.txt) file.',
         card=CARD_DOCUMENT_FILES,
         types=('missing_file',),
         when=in_document_collection,
         subject='path'),

    # validate runs veraPDF on PDF/A documents and only takes PDF/A-1.
    Rule('document-not-pdfa', USER,
         "A document's PDF is not PDF/A-1",
         'PDS archives documents as PDF/A-1, and the validation tool found this PDF does '
         'not conform. Export it again as PDF/A-1b (Word on Windows: File > Export > '
         'Create PDF/XPS, tick "ISO 19005-1 compliant"; LibreOffice: Export as PDF, '
         'Archive (PDF/A), PDF/A-1b), then open the document with Edit and upload it.',
         card=CARD_DOCUMENT_FILES,
         types=('not_pdfa_compliant',),
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

    # ELSA builds every LID, and names go through models.pds_lid_segment, so this one is our bug.
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

    # ELSA writes context products from the crawler's row, so a wrong name is ours.
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
         # Match on severity. validate's warning types start with "warning.".
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
    # Only errors can be the user's to fix. No severity counts as an error.
    warning = not _is_error(finding)
    for rule in RULES:
        if warning and rule.audience == USER:
            continue
        if rule.matches(finding):
            return rule
    return UNMAPPED


def translate(findings):
    """Group findings into what a person should see.

    Returns {'user': [...], 'advisory': [...], 'elsa': [...], 'unmapped': [...]}. Each item
    has the wording, the card that fixes it and the raw findings.
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
            # One item per thing, so two badly named files are two rows.
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
    """PDS's output, grouped by what each finding is about.

    PDS reports most defects twice, so group by (label, element, line) and keep every message.
    Returns [(label, [group, ...]), ...].
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
        if _is_error(finding):
            group['severity'] = 'ERROR'

    by_label = {}
    for group in grouped.values():
        by_label.setdefault(group['label'], []).append(group)

    for groups in by_label.values():
        groups.sort(key=lambda g: (g['line'] or 0, g['element_path']))

    return sorted(by_label.items())


def merge_user(user_items, extra):
    """Merge ELSA's requirements with what PDS reported.

    De-duped on key, since a blank citation shows up in both. Requirements go first.
    """
    seen = set(item['key'] for item in user_items)
    requirements = [item for item in extra if item['key'] not in seen]
    return requirements + list(user_items)


def unresolved_items(findings, bundle):
    """Errors the user can't fix, as one blocking item.

    External only, since Archive can't pass validation yet. Staff can still get past this
    if a rule turns out to be wrong.
    """
    if bundle is None or bundle.bundle_type != 'External':
        return []
    errors = [finding for finding in findings or []
              if _is_error(finding) and classify(finding).audience != USER]
    if not errors:
        return []
    return [{
        'key': 'unresolved-errors',
        'title': 'PDS reports {} problem{} ELSA could not repair'.format(
            len(errors), '' if len(errors) == 1 else 's'),
        'detail': 'This is not something you entered and there is nothing to change on '
                  'this page. Please contact the ELSA team through the Contact page and '
                  'name this bundle. The Validation output tab has the details.',
        'card': CARD_SUPPORT[0],
        'anchor': CARD_SUPPORT[1],
        'scroll_to': CARD_SUPPORT[2],
        'audience': USER,
        'subject': '',
        'labels': sorted({finding['label'] for finding in errors if finding.get('label')}),
        'findings': errors,
    }]


def without_unresolved(findings, extra):
    """Findings not already counted by an unresolved-errors item.

    Those block the bundle, so they shouldn't also show under "Other notes".
    """
    counted = [finding for item in extra if item['key'] == 'unresolved-errors'
               for finding in item['findings']]
    return [finding for finding in findings or []
            if not any(finding is other for other in counted)]


def summarise(findings, extra=()):
    """Counts for the panel: what blocks, what to review, what passed.

    `extra` is the requirement items from build.preflight, which count as blocking.
    """
    translated = translate(findings)
    user = merge_user(translated['user'], extra)
    return {
        'blocking': len(user),
        'advisory': len(translate(without_unresolved(findings, extra))['advisory']),
        'hidden': len(translated['elsa']),
        'unmapped': len(translated['unmapped']),
        'can_submit': not user,
    }


def cards(findings, extra=()):
    """User-facing items grouped by the card that fixes them, in page order."""
    order = [CARD_CITATION[0], CARD_MOD_HISTORY[0], CARD_CONTEXT[0],
             CARD_DOCUMENT[0], CARD_NETCDF[0], CARD_AMA[0], CARD_DATA[0],
             CARD_ALIAS[0], CARD_SUPPORT[0], None]

    grouped = {}
    for item in merge_user(translate(findings)['user'], extra):
        grouped.setdefault(item['card'], []).append(item)

    return [(card, grouped[card]) for card in order if card in grouped]
