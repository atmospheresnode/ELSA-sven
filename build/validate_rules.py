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
                 types=(), path=(), message=(), collapse='rule'):
        self.key = key
        self.audience = audience
        self.title = title
        self.detail = detail
        self.card, self.anchor = card
        self.types = types
        self.path = path
        self.message = message
        self.collapse = collapse

    def matches(self, finding):
        if self.types and not any(finding['type'].endswith(t) for t in self.types):
            return False
        if self.path and not any(p in finding['element_path'] for p in self.path):
            return False
        if self.message and not any(
                re.search(m, finding['message'], re.I) for m in self.message):
            return False
        return bool(self.types or self.path or self.message)


# Order matters: the first rule that matches wins, so the specific ones come first.
RULES = [

    # ---- things the person looking at the screen can fix -----------------------

    Rule('citation-missing', USER,
         'Add Citation Information, including a description',
         'PDS needs to know who produced this data and what it is, so it can be '
         'cited and found later. The description is the part reviewers read first.',
         card=CARD_CITATION,
         message=(r'Citation_Information and its description are required',)),

    Rule('collection-empty', USER,
         'This collection has nothing in it yet',
         'Every collection has to list at least one product. Add a file to it, or '
         'remove the collection if you do not need it.',
         card=CARD_DOCUMENT,
         path=('File_Area_Inventory/Inventory',),
         message=(r"Value '0' is not facet-valid", r"value '0' of element",
                  r"must have no element")),

    Rule('target-type', USER,
         'A target is missing its type, or the type is not one PDS recognises',
         'Targets carry a fixed set of types, such as Planet, Satellite or '
         'Laboratory Analog. Choose one from the list rather than typing a value.',
         card=CARD_CONTEXT,
         path=('Target_Identification',),
         message=(r'must be equal to one of the following values',
                  r'reference_type must be set to one of')),

    Rule('investigation-type', USER,
         'The investigation type is not one PDS recognises',
         'PDS accepts Individual Investigation, Mission, Observing Campaign or '
         'Field Campaign here. Pick the closest match from the list.',
         card=CARD_CONTEXT,
         path=('Investigation_Area',),
         message=(r'must be equal to one of the following values',)),

    Rule('ama-vocabulary', USER,
         'A model metadata value is not one PDS accepts',
         'Open the model metadata panel and choose a value from the dropdown rather '
         'than typing one. These fields are closed lists, and a value ELSA does not '
         'recognise is marked when the panel is next opened.',
         card=CARD_AMA,
         path=('AMA', 'Model_Metadata', 'Simulation_Configuration'),
         message=(r'must be equal to one of the following values',)),

    Rule('bad-filename', USER,
         'A file name contains a character PDS does not allow',
         'PDS file names may use letters, digits, dots, dashes and underscores. '
         'Rename the file and upload it again; spaces are the usual culprit.',
         card=CARD_NETCDF,
         types=('name_has_invalid_characters',)),

    Rule('missing-file', USER,
         'A file this label describes is not there',
         'The label points at a file that is missing from the bundle. Re-upload it, '
         'or remove the product that refers to it.',
         card=CARD_NETCDF,
         types=('missing_file',)),

    # ---- ELSA's own output: never shown to a data provider ---------------------

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
                'Something PDS flagged that ELSA does not have wording for yet',
                'Shown in full so it is not hidden. Reported to the ELSA team so a '
                'plainer explanation can be written.',
                collapse='finding')


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

        if rule.collapse == 'rule':
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
