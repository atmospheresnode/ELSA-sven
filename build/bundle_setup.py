"""The setup page: what it shows, and the one save it does itself.

Every step on the setup page but one saves through a view the bundle page already
uses, in place, exactly as the bundle page's windows do (X-Elsa-Inplace, see
_inplace_form in views.py). The page adds no second way of saving an alias, a
modification history, a target, a document, a collection or a NetCDF file.

The exception is the citation. The citation used to be created in two steps: a form
asking how many authors and editors there would be, then a page with a blank for each
one. The counts could never be changed afterwards (the edit page has a fixed number of
blanks), so a wrong count meant deleting the citation and starting again. Here the
user lists the people instead, and the counts follow from the list.

The counts are still what the database stores, and the names still live only in the
labels, so nothing about the model or the bundle page's own citation editor changes.
A save writes the whole Citation_Information element into the bundle label, then
copies that element into every other label, the same way the existing edit path
keeps them in step (Citation_Information.sync_into_label and
mirror_citation_into_data_products).
"""

import copy
import re

from django import forms
from django.utils import timezone
from lxml import etree

from build import preflight, walkthrough
from build.chocolate import close_label, open_label_with_tree
from build.forms import (AdditionalCollectionForm, AliasForm, AnnexProductDocumentForm,
                         InvestigationForm, TargetFormAll,
                         clean_pds_person_name)
from build.models import (NAMESPACE, MAX_TEXT_FIELD, Citation_Information, Product_Bundle,
                          mirror_citation_into_data_products)


# The two ATM editors every ELSA citation names first. The same values
# Citation_Information.fill_label writes.
FIXED_EDITORS = [
    ('Lynn', 'Neakrase', '0000-0002-6370-5791'),
    ('Lyle', 'Huber', '0000-0002-1232-1982'),
]
FIXED_EDITOR_AFFILIATION = ('NASA Planetary Data System Atmospheres Node; '
                            'New Mexico State University, Las Cruces, NM')

# The fields of each kind of row, as posted. The names are the ones the bundle
# page's citation editor (EditCitationInformationForm) already uses, so an error
# keyed by one of them means the same thing on either page.
PERSON_FIELDS = ('given_name', 'family_name', 'orcid', 'affiliation')
ORG_FIELDS = ('name', 'rorid', 'sequence_number', 'parent_org_name')
GROUPS = {
    'author_people': ('author_person', PERSON_FIELDS),
    'author_orgs': ('author_org', ORG_FIELDS),
    'editor_people': ('editor_person', PERSON_FIELDS),
    'editor_orgs': ('editor_org', ORG_FIELDS),
}

# Which setup step settles each of build.preflight's requirements.
REQUIREMENT_STEPS = {
    'citation-missing': 'citation',
    'author-missing': 'citation',
    # Written at creation; if it is missing anyway, the bundle page's window adds it.
    'modification-history-missing': '',
    'target-missing': 'targets',
    'netcdf-missing': 'data',
}


def _blank(value):
    return not (value or '').strip()


ORCID_PREFIX = re.compile(r'^(https?://)?(www\.)?orcid\.org/|^orcid:?\s*', re.IGNORECASE)


def tidy_orcid(value):
    """An ORCID iD written the way PDS labels use it, from however it was typed.

    People paste the link, type the sixteen digits without dashes, add spaces, or end
    in a lower-case x. All of that is the same iD, so ELSA writes it the standard way
    (0000-0002-1825-0097) instead of refusing it. Returns the tidied iD, or what is
    left after tidying when it is not sixteen characters long.
    """
    value = ORCID_PREFIX.sub('', (value or '').strip()).strip().rstrip('/')
    characters = re.sub(r'[^0-9Xx]', '', value).upper()
    if len(characters) == 16:
        return '-'.join(characters[i:i + 4] for i in range(0, 16, 4))
    return characters


def clean_orcid(value):
    """The ORCID iD PDS labels use, from what a person typed or pasted.

    PDS4 sets no format for person_orcid, so the validator accepts anything, and a
    mistyped digit then credits the wrong person or no one. Anything that tidies to
    sixteen characters is accepted in the standard form; what is refused is the wrong
    number of digits, or a check digit that does not match (ISO 7064 11,2, as ORCID
    itself checks), which means a typo.
    """
    if not (value or '').strip():
        return ''
    value = tidy_orcid(value)
    digits = value.replace('-', '')
    if len(digits) != 16 or not re.match(r'^\d{15}[\dX]$', digits):
        raise forms.ValidationError(
            'An ORCID iD has 16 digits (the last one can be an X), and this has %(count)s. '
            'You can type it with or without the dashes, or paste the link from orcid.org.',
            params={'count': len(re.sub(r'[^0-9X]', '', digits)) or 'none'})
    total = 0
    for character in digits[:-1]:
        total = (total + int(character)) * 2
    check = (12 - total % 11) % 11
    if digits[-1] != ('X' if check == 10 else str(check)):
        raise forms.ValidationError('This ORCID iD has a typo somewhere: its last digit does not match the rest. Please check it against orcid.org.')
    return value


class CitationListForm(forms.Form):
    """A citation as a list of people, however many rows were posted.

    Rows are numbered by the page; the numbers only group a row's fields and may have
    gaps where a row was removed. A row left entirely blank is ignored, so an unused
    "Add a person" costs nothing.
    """

    publication_year = forms.CharField(required=True, max_length=4, error_messages={
        'required': 'Enter the year you expect the bundle to be released.'})
    description = forms.CharField(required=True, max_length=MAX_TEXT_FIELD, error_messages={
        'required': 'Describe the data in a sentence or two. Reviewers read this first.'})
    keyword = forms.CharField(required=False, max_length=255)

    def __init__(self, *args, **kwargs):
        self.bundle = kwargs.pop('bundle')
        super().__init__(*args, **kwargs)
        self.indices = {}
        data = self.data or {}
        for group, (prefix, fields) in GROUPS.items():
            pattern = re.compile(r'^{}_(\d+)_({})$'.format(prefix, '|'.join(fields)))
            found = sorted({int(m.group(1)) for m in (pattern.match(k) for k in data) if m})
            self.indices[group] = found
            for i in found:
                for field in fields:
                    self.fields['{}_{}_{}'.format(prefix, i, field)] = forms.CharField(
                        required=False, max_length=255)

    def clean_publication_year(self):
        year = self.cleaned_data['publication_year'].strip()
        if not re.match(r'^\d{4}$', year):
            raise forms.ValidationError('Enter a four-digit year, for example {}.'.format(
                timezone.now().year))
        return year

    def clean(self):
        cleaned = super().clean()
        self.rows = {group: [] for group in GROUPS}

        for group, (prefix, fields) in GROUPS.items():
            people = fields is PERSON_FIELDS
            for i in self.indices[group]:
                name = lambda field: '{}_{}_{}'.format(prefix, i, field)
                row = {field: (cleaned.get(name(field)) or '').strip() for field in fields}
                if all(_blank(v) for v in row.values()):
                    continue
                if people:
                    try:
                        row['orcid'] = clean_orcid(row['orcid'])
                    except forms.ValidationError as error:
                        self.add_error(name('orcid'), error)
                    for field, label in (('given_name', 'given name'),
                                         ('family_name', 'family name')):
                        if _blank(row[field]):
                            self.add_error(name(field), 'Enter a {}, or remove this person.'.format(label))
                            continue
                        try:
                            row[field] = clean_pds_person_name(row[field], label)
                        except forms.ValidationError as error:
                            self.add_error(name(field), error)
                elif _blank(row['name']):
                    self.add_error(name('name'), 'Enter the organization\'s name, or remove it.')
                self.rows[group].append(row)

        # External bundles need an author (build.preflight). Asked for here because this
        # is where the user is looking at the list.
        if (self.bundle.bundle_type == 'External'
                and not self.rows['author_people'] and not self.rows['author_orgs']):
            raise forms.ValidationError(
                'Add at least one author: a person or an organization. A citation needs '
                'someone to credit.')
        return cleaned


def fixed_editor_names():
    return ['{} {}'.format(given, family) for given, family, _ in FIXED_EDITORS]


def citation_rows(bundle):
    """What the citation step shows: the saved citation, or a blank one to start."""
    citation = bundle.citation_information_set.first()
    if citation is None:
        return {
            'exists': False,
            'publication_year': str(timezone.now().year),
            'description': '',
            'keyword': '',
            # No blank person to start with: a form open on arrival read as
            # something already half done. The Add buttons open one.
            'author_people': [],
            'author_orgs': [],
            'editor_people': [],
            'editor_orgs': [],
        }

    recorded = citation._recorded_people()

    def person(values):
        return {'given_name': values.get('given_name') or '',
                'family_name': values.get('family_name') or '',
                'orcid': values.get('person_orcid') or '',
                'affiliation': values.get('organization_name') or ''}

    def organization(values):
        return {'name': values.get('organization_name') or '',
                'rorid': values.get('organization_rorid') or '',
                'sequence_number': values.get('sequence_number') or '',
                'parent_org_name': values.get('parent_organization_name') or ''}

    return {
        'exists': True,
        'publication_year': citation.publication_year,
        'description': citation.description,
        'keyword': citation.keyword,
        'author_people': [person(p) for p in recorded['author_people']],
        'author_orgs': [organization(o) for o in recorded['author_orgs']],
        'editor_people': [person(p) for p in recorded['editor_people']],
        'editor_orgs': [organization(o) for o in recorded['editor_orgs']],
    }


def _element(parent, tag, text=None):
    element = etree.SubElement(parent, '{}{}'.format(NAMESPACE, tag))
    if text is not None:
        element.text = text
    return element


def _optional(parent, tag, text):
    # Every optional field left blank is left out. An empty element is a validation
    # error (PDS4 values have a minimum length); a missing optional one is not.
    if not _blank(text):
        _element(parent, tag, text.strip())


def _person(parent, given, family, orcid, affiliation):
    person = _element(parent, 'Person')
    _element(person, 'given_name', given)
    _element(person, 'family_name', family)
    _optional(person, 'person_orcid', orcid)
    if not _blank(affiliation):
        _element(_element(person, 'Affiliation'), 'organization_name', affiliation.strip())


def _organization(parent, row):
    organization = _element(parent, 'Organization')
    _element(organization, 'organization_name', row['name'])
    _optional(organization, 'organization_rorid', row['rorid'])
    _optional(organization, 'sequence_number', row['sequence_number'])
    if not _blank(row['parent_org_name']):
        _element(_element(organization, 'Parent_Organization'),
                 'parent_organization_name', row['parent_org_name'].strip())


def citation_element(citation, rows):
    """The whole Citation_Information element, in the order the PDS4 schema sets."""
    root = etree.Element('{}Citation_Information'.format(NAMESPACE))
    _element(root, 'publication_year', citation.publication_year)
    _optional(root, 'keyword', citation.keyword)
    _element(root, 'description', citation.description)

    # List_Author and List_Editor are a sequence in the schema: every Organization
    # comes before every Person.
    if rows['author_people'] or rows['author_orgs']:
        authors = _element(root, 'List_Author')
        for row in rows['author_orgs']:
            _organization(authors, row)
        for row in rows['author_people']:
            _person(authors, row['given_name'], row['family_name'], row['orcid'], row['affiliation'])

    editors = _element(root, 'List_Editor')
    for row in rows['editor_orgs']:
        _organization(editors, row)
    for given, family, orcid in FIXED_EDITORS:
        _person(editors, given, family, orcid, FIXED_EDITOR_AFFILIATION)
    for row in rows['editor_people']:
        _person(editors, row['given_name'], row['family_name'], row['orcid'], row['affiliation'])
    return root


def save_citation(bundle, form):
    """Create or replace the bundle's citation from a valid CitationListForm."""
    from build.views import bundle_label_targets  # views imports this module

    rows = form.rows
    citation = bundle.citation_information_set.first() or Citation_Information(bundle=bundle)
    citation.number_of_authors_people = len(rows['author_people'])
    citation.number_of_authors_organization = len(rows['author_orgs'])
    citation.number_of_editors_people = len(rows['editor_people'])
    citation.number_of_editors_organization = len(rows['editor_orgs'])
    citation.publication_year = form.cleaned_data['publication_year']
    citation.description = form.cleaned_data['description'].strip()
    citation.keyword = (form.cleaned_data.get('keyword') or '').strip()
    citation.save()

    # The bundle label first: it is the source every other label is copied from.
    product_bundle = Product_Bundle.objects.get(bundle=bundle)
    label_list = open_label_with_tree(product_bundle.label())
    label_root = label_list[1]
    identification = label_root.find('{}Identification_Area'.format(NAMESPACE))
    replacement = citation_element(citation, rows)
    existing = identification.find('{}Citation_Information'.format(NAMESPACE))
    if existing is not None:
        identification.replace(existing, replacement)
    else:
        # Citation_Information precedes Modification_History in the PDS4 content model.
        modification = identification.find('{}Modification_History'.format(NAMESPACE))
        if modification is not None:
            identification.insert(identification.index(modification), replacement)
        else:
            identification.append(replacement)
    close_label(product_bundle.label(), label_root, label_list[2])

    for collection in bundle_label_targets(bundle):
        citation.sync_into_label(collection.label())
    mirror_citation_into_data_products(bundle)
    return citation


def finish(bundle):
    """The finish step: each thing ELSA requires before review, met or not."""
    items = []
    for requirement in preflight.REQUIREMENTS:
        if not requirement.relevant(bundle):
            continue
        ok = requirement.satisfied(bundle)
        step = REQUIREMENT_STEPS.get(requirement.key, '')
        # A requirement no step asks for is one ELSA meets itself (the first
        # modification history entry). A tick for it read as something the user had
        # done; it is listed only if it is somehow missing.
        if ok and not step:
            continue
        items.append({'key': requirement.key, 'title': requirement.title, 'ok': ok, 'step': step})
    left = sum(1 for item in items if not item['ok'])
    return {'items': items, 'ready': not left, 'left': left}


def collections_with_files(bundle):
    collections = []
    for collection in bundle.additionalcollections_set.order_by('pk'):
        collections.append({'collection': collection,
                            'files': list(collection.netcdf_files.order_by('pk'))})
    return collections


def context(bundle):
    """Everything the setup page renders."""
    summary = walkthrough.summary(bundle)
    form_target = TargetFormAll(pk_bundle=bundle.pk)
    form_target.fields['target'].widget.attrs.update({'class': 'form-select', 'id': 'setup_target'})
    form_target.fields['target'].empty_label = 'Choose a target'
    ctx = {
        'bundle': bundle,
        'setup': summary,
        'finish': finish(bundle),
        'initial_version': bundle.modification_history_set.order_by('pk').first(),
        'citation': citation_rows(bundle),
        'targets': bundle.targets.all(),
        'form_target': form_target,
        'fixed_editors': fixed_editor_names(),
    }
    if bundle.bundle_type == 'External':
        ctx.update({
            'documents': bundle.product_document_set.order_by('pk'),
            'form_document': AnnexProductDocumentForm(bundle=bundle),
            'collections': collections_with_files(bundle),
            'form_collection': AdditionalCollectionForm(bundle=bundle),
        })
    else:
        ctx.update({
            'aliases': bundle.alias_set.order_by('pk'),
            'form_alias': AliasForm(),
            'investigations': bundle.investigations.all(),
            'form_investigation': InvestigationForm(),
        })
    return ctx
