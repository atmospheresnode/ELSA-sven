"""The setup page: what it shows and the one save it does itself.

Every step but one saves through the same view as the bundle page (X-Elsa-Inplace, see
_inplace_form in views.py).

The citation is the exception. The user lists the people and the counts come from the list.
A save stores the people in Citation_Contributor, then writes the whole Citation_Information into
the bundle label and copies it to the others (write_citation).
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
                         clean_pds_person_name, clean_ror_id)
from build.models import (NAMESPACE, MAX_TEXT_FIELD, Citation_Information, Product_Bundle,
                          mirror_citation_into_data_products)


# The two ATM editors. Same values as Citation_Information.fill_label.
FIXED_EDITORS = [
    ('Lynn', 'Neakrase', '0000-0002-6370-5791'),
    ('Lyle', 'Huber', '0000-0002-1232-1982'),
]
FIXED_EDITOR_AFFILIATION = ('NASA Planetary Data System Atmospheres Node; '
                            'New Mexico State University, Las Cruces, NM')

# Fields per row. Same names as EditCitationInformationForm.
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
    'document-collection-empty': 'documents',
}


def _blank(value):
    return not (value or '').strip()


ORCID_PREFIX = re.compile(r'^(https?://)?(www\.)?orcid\.org/|^orcid:?\s*', re.IGNORECASE)


def tidy_orcid(value):
    """Tidy an ORCID iD to the standard form (0000-0002-1825-0097).

    Handles pasted links, missing dashes, spaces and a lower-case x.
    """
    value = ORCID_PREFIX.sub('', (value or '').strip()).strip().rstrip('/')
    characters = re.sub(r'[^0-9Xx]', '', value).upper()
    if len(characters) == 16:
        return '-'.join(characters[i:i + 4] for i in range(0, 16, 4))
    return characters


def clean_orcid(value):
    """Check an ORCID iD. PDS4 has no format for person_orcid so validate won't catch a typo.

    Refuses the wrong number of digits or a bad check digit (ISO 7064 11,2).
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

    Row numbers can have gaps. Rows left blank are ignored.
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
                else:
                    if _blank(row['name']):
                        self.add_error(name('name'), 'Enter the organization\'s name, or remove it.')
                    try:
                        row['rorid'] = clean_ror_id(row['rorid'])
                    except forms.ValidationError as error:
                        self.add_error(name('rorid'), error)
                self.rows[group].append(row)

        # External bundles need an author (build.preflight).
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
            # No blank person to start with. The Add buttons open one.
            'author_people': [],
            'author_orgs': [],
            'editor_people': [],
            'editor_orgs': [],
        }

    if citation.contributors_saved:
        rows = stored_rows(citation)
        return dict(rows, exists=True, publication_year=citation.publication_year,
                    description=citation.description, keyword=citation.keyword)

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
    # Blank optional fields are left out since PDS4 rejects empty elements.
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

    # Every Organization comes before every Person in List_Author and List_Editor.
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


def stored_rows(citation):
    """The citation's people from the database, in the CitationListForm.rows shape."""
    rows = {group: [] for group in GROUPS}
    for contributor in citation.contributors.all():
        rows[contributor.group()].append(contributor.as_row())
    return rows


def store_rows(citation, rows):
    """Replace the citation's people with these rows, and keep the counts in step."""
    from build.models import Citation_Contributor

    citation.contributors.all().delete()
    made = []
    for group, (_prefix, fields) in GROUPS.items():
        role, plural = group.split('_')
        kind = 'person' if plural == 'people' else 'organization'
        for position, row in enumerate(rows.get(group) or []):
            made.append(Citation_Contributor(
                citation=citation, role=role, kind=kind, position=position,
                **{field: (row.get(field) or '').strip() for field in fields}))
    Citation_Contributor.objects.bulk_create(made)
    citation.number_of_authors_people = len(rows.get('author_people') or [])
    citation.number_of_authors_organization = len(rows.get('author_orgs') or [])
    citation.number_of_editors_people = len(rows.get('editor_people') or [])
    citation.number_of_editors_organization = len(rows.get('editor_orgs') or [])
    citation.contributors_saved = True
    citation.save()


def rows_from_label(citation):
    """The people in the bundle label's citation, as rows. For citations saved before
    Citation_Contributor existed. The fixed ATM editors are left out, they're always added."""
    rows = {group: [] for group in GROUPS}
    try:
        path = Product_Bundle.objects.get(bundle=citation.bundle).label()
        root = etree.parse(path).getroot()
    except (Product_Bundle.DoesNotExist, AttributeError, TypeError, OSError, etree.XMLSyntaxError):
        return rows
    element = root.find('{0}Identification_Area/{0}Citation_Information'.format(NAMESPACE))
    if element is None:
        return rows

    def text(parent, path):
        return (parent.findtext(path.format(NAMESPACE)) or '').strip()

    fixed = {(given, family) for given, family, _orcid in FIXED_EDITORS}
    for role, tag in (('author', 'List_Author'), ('editor', 'List_Editor')):
        container = element.find(NAMESPACE + tag)
        if container is None:
            continue
        for organization in container.findall(NAMESPACE + 'Organization'):
            rows[role + '_orgs'].append({
                'name': text(organization, '{0}organization_name'),
                'rorid': text(organization, '{0}organization_rorid'),
                'sequence_number': text(organization, '{0}sequence_number'),
                'parent_org_name': text(organization, '{0}Parent_Organization/{0}parent_organization_name')})
        for person in container.findall(NAMESPACE + 'Person'):
            row = {'given_name': text(person, '{0}given_name'),
                   'family_name': text(person, '{0}family_name'),
                   'orcid': text(person, '{0}person_orcid'),
                   'affiliation': text(person, '{0}Affiliation/{0}organization_name')}
            if role == 'editor' and (row['given_name'], row['family_name']) in fixed:
                continue
            rows[role + '_people'].append(row)
    return rows


def rows_from_edit_form(citation, cleaned):
    """Rows from the older edit page, which has one set of fields per counted person."""
    counts = {'author_people': citation.number_of_authors_people,
              'author_orgs': citation.number_of_authors_organization,
              'editor_people': citation.number_of_editors_people,
              'editor_orgs': citation.number_of_editors_organization}
    rows = {}
    for group, (prefix, fields) in GROUPS.items():
        rows[group] = [{field: str(cleaned.get('{}_{}_{}'.format(prefix, index, field)) or '').strip()
                        for field in fields}
                       for index in range(counts[group] or 0)]
    return rows


def write_citation(citation):
    """Write the citation, from the database, into every label of the bundle."""
    from build.views import bundle_label_targets  # views imports this module

    bundle = citation.bundle
    rows = stored_rows(citation)

    # The bundle label first: the other labels are copied from it.
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


def rewrite_from_label(citation):
    """For the older citation pages that still edit the labels directly: save what they
    wrote to the database, then write every label from that."""
    store_rows(citation, rows_from_label(citation))
    write_citation(citation)


def save_citation(bundle, form):
    """Create or replace the bundle's citation from a valid CitationListForm."""
    citation = bundle.citation_information_set.first() or Citation_Information(bundle=bundle)
    citation.publication_year = form.cleaned_data['publication_year']
    citation.description = form.cleaned_data['description'].strip()
    citation.keyword = (form.cleaned_data.get('keyword') or '').strip()
    citation.save()
    store_rows(citation, form.rows)
    write_citation(citation)
    return citation


def finish(bundle):
    """The finish step: each thing ELSA requires before review, met or not."""
    items = []
    for requirement in preflight.REQUIREMENTS:
        if not requirement.relevant(bundle):
            continue
        ok = requirement.satisfied(bundle)
        step = REQUIREMENT_STEPS.get(requirement.key, '')
        # ELSA does this one itself (the first modification history), so only list it if it's missing.
        if ok and not step:
            continue
        items.append({'key': requirement.key, 'title': requirement.title, 'ok': ok, 'step': step,
                      'source': requirement.source})
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
