from builtins import object
from django import forms
from django.contrib.auth.models import User
from .chocolate import replace_all
from django.utils.safestring import mark_safe

#from django.forms import modelformset_factory

from lxml import etree
import json
import urllib.request
import urllib.error
import urllib.parse
import urllib.request
import urllib.parse
import urllib.error
import datetime

from .models import *
#from context.models import *
# ------------------------------------------------------------------------------------------------------ #
# ------------------------------------------------------------------------------------------------------ #
#
#                                           FORMS
#
#    The following forms are mostly associated with models.  The first form, ConfirmForm, is an example
# of a form that is not associated with any models.  The specification for the PDS4 components
# (ex: Alias, Bundle, ...) can be found in models.py with the corresponding model object.  The comments
# for the following forms should include the input format rules.  This information may or may not need
# to be in models over forms.  I'm not too sure where we will decide to do our data checking as of yet.
# Some models listed below that have choices do include the specification as a part of data checking.
#
# TASK:  Add data checking/cleaning to fit ELSA standard.
#
# ------------------------------------------------------------------------------------------------------ #

# Hello Tommy How do I change the port
"""
    Confirm
"""


class ConfirmForm(forms.Form):
    CHOICES = [('Yes', 'Yes'), ('No', 'No')]
    decision = forms.ChoiceField(choices=CHOICES, widget=forms.RadioSelect())


"""
    Alias
"""


class AliasForm(forms.ModelForm):
    alternate_id = forms.CharField(required=False, max_length=100, widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'alt_id'
        })
    )

    alternate_title = forms.CharField(required=False, max_length=100, widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'alt_title'
        })
    )

    comment = forms.CharField(required=False, widget=forms.Textarea(attrs={
            'class': 'form-control',
            'placeholder': 'Comment',
            'id': 'comment',
            'rows': '1'
        })
    )

    class Meta(object):
        model = Alias
        exclude = ('bundle',)

    def clean(self):
        cleaned_data = super().clean()
        alternate_id = cleaned_data.get("alternate_id")
        alternate_title = cleaned_data.get("alternate_title")

        if not alternate_id and not alternate_title:
            raise forms.ValidationError("Please provide an Alternate ID or an Alternate Title")

class AliasDelete(forms.ModelForm):

    class Meta(object):
        model = Alias
        exclude = ('bundle',)


"""
    Array
"""


class ArrayForm(forms.ModelForm):
    class Meta(object):
        model = Array
        exclude = ('product_observational', 'local_identifier')


"""
    Bundle
"""

BUNDLE_TYPE_CHOICES = (
    ('Archive', 'Archive'),
    ('External', 'External'),
)

VERSION_CHOICES = (
    ('1O00', '1O00'),
    ('1N00', '1N00'),
    ('1K00', '1K00'),
    ('1J00', '1J00'),
    ('1I00', '1I00'),
    ('1H00', '1H00'),
    ('1G00', '1G00'),
    ('1F00', '1F00'),
    ('1E00', '1E00'),
    ('1D00', '1D00'),
)

# PDS4 caps a logical identifier at 255 characters. A bundle's name becomes the
# bundle_id segment of every LID ELSA generates, and collection and product
# segments are appended after it:
#
#     urn:<agency>:<bundle_id>:<collection>:<product>
#
# so the name cannot spend the whole budget. These bound it.
MAX_LID_LENGTH = 255
# Longest prefix in use: External bundles use 'urn:nasa:pds-ama:' (17), Archive
# uses 'urn:' plus the agency ('nasa:pds', 'esa:psa', 'jaxa:darts') plus ':'.
LONGEST_LID_PREFIX = len('urn:nasa:pds-ama:')
# Left over for ':<collection>:<product>' on the deepest products.
LID_SEGMENT_RESERVE = 64
# Ceiling the name and bundleID fields are allowed to reach.
MAX_BUNDLE_NAME = 150


class BundleForm(forms.ModelForm):
    name = forms.CharField(required=True, max_length=MAX_BUNDLE_NAME, widget = forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'bundle_name',
            'placeholder': 'Bundle Name'
        })
    )

    bundle_type = forms.ChoiceField(required=True, choices=BUNDLE_TYPE_CHOICES, widget = forms.Select(attrs={
            'class': 'form-control',
            'id': 'bundle_type',
            'placeholder': 'Bundle Type'
        })
    )

    version = forms.ChoiceField(required=True, choices=VERSION_CHOICES, widget = forms.Select(attrs={
            'class': 'form-select',
            'id': 'bundle_version',
        })
    )

    bundleID = forms.CharField(required=False, max_length=MAX_BUNDLE_NAME, widget=forms.TextInput(attrs={
            'class' : 'form-control',
            'id': 'bundleID',
            'placeholder': 'Bundle ID (Optional - Leave blank to auto generate)'
        })
    )

    class Meta(object):
        model = Bundle
        fields = ('name', 'bundle_type', 'version', 'bundleID')

    """
        clean should ensure the following:
            - The name of the bundle can fit within a given lid length
                - Unfortunately, not enough data has been collected for us to have a good boundary  
                  idea.  A lid must be no more than 255 characters (double check).
            - The name should be ready for lid case. <-- I feel like this should be removed now that
              there is a model that puts name into lid case.  The cleaner should be a minimal 
              cleansing of data.
            - The user should not append bundle to the end of the bundle name.
    """

    def clean(self):
        cleaned_data = self.cleaned_data
        name = cleaned_data.get('name')

        # The field's own max_length already rejected an over-long name, and a
        # missing name is reported by the required check, so there is nothing
        # left to validate here.
        if not name:
            return cleaned_data

        name_edit = replace_all(name.lower(), ' ', '_')

        # Check for the colon before trimming anything. This test used to run
        # after the trailing "bundle" was stripped, so "my:bundle" lost its
        # colon along with the suffix and was accepted, even though
        # Bundle.save() derives bundleID from the raw name and the colon then
        # became a stray delimiter in the LID.
        if name_edit.find(':') != -1:
            raise forms.ValidationError(
                "The colon (:) is used to delimit segments of a urn and thus is not permitted within a bundle name.")

        if name_edit.endswith("bundle"):
            # seven because there is probably an underscore by now
            name_edit = name_edit[:-7]

        # Guard the assembled identifier, not just the raw name. This used to
        # compare the name against 255 directly, which both ignored the LID
        # prefix and the appended collection/product segments, and could never
        # fire anyway because the field was capped at 50.
        bundle_id = (cleaned_data.get('bundleID') or '').strip().lower().replace(' ', '_')
        if not bundle_id:
            bundle_id = name_edit

        budget = MAX_LID_LENGTH - LONGEST_LID_PREFIX - LID_SEGMENT_RESERVE
        if len(bundle_id) > budget:
            raise forms.ValidationError(
                "This name makes the bundle's logical identifier too long. A PDS4 "
                "logical identifier is limited to {} characters, and collection and "
                "product names are appended after the bundle name. Please use {} "
                "characters or fewer.".format(MAX_LID_LENGTH, budget))

        return cleaned_data


"""
    Citation_Information
"""


class CitationInformationForm(forms.ModelForm):
    # modify author and editor list for future to format like 
    # last name, first name; last name, f; etc (both versions work)

    # author_list = forms.CharField(required=False, widget = forms.TextInput(attrs={
    #     'class': 'form-control form-outline',
    #     'id': 'author_list'
    # }))
    number_of_authors_people = forms.IntegerField(required=True, min_value=0, initial=0, widget= forms.NumberInput(attrs={
        'class': 'form-control form-outline',
        'id': 'id_number_of_authors_people'
    }))

    number_of_authors_organization = forms.IntegerField(required=True, min_value=0, initial=0, widget= forms.NumberInput(attrs={
        'class': 'form-control form-outline',
        'id': 'id_number_of_authors_organization'
    }))

    # User-added editors, in ADDITION to the two fixed ATM editors (Lynn Neakrase
    # and Lyle Huber) that ELSA always includes automatically.
    number_of_editors_people = forms.IntegerField(required=False, min_value=0, initial=0, widget= forms.NumberInput(attrs={
        'class': 'form-control form-outline',
        'id': 'id_number_of_editors_people'
    }))

    number_of_editors_organization = forms.IntegerField(required=False, min_value=0, initial=0, widget= forms.NumberInput(attrs={
        'class': 'form-control form-outline',
        'id': 'id_number_of_editors_organization'
    }))

    ##

    description = forms.CharField(required=True, widget = forms.TextInput(attrs={
        'class': 'form-control form-outline',
        'id': 'cite_desc'
    }))

    keyword = forms.CharField(required=False, widget = forms.TextInput(attrs={
        'class': 'form-control form-outline',
        'id': 'keyword'
    }))

    publication_year = forms.CharField(required=True, widget = forms.TextInput(attrs={
        'class': 'form-control form-outline',
        'id': 'publication_year'
    }))

    # validators=[RegexValidator(r'^\d{1,10}$')])

    class Meta:
        model = Citation_Information
        exclude = ('bundle',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Add fields for authors (people)
        self._add_person_fields('author', self.initial.get('number_of_authors_people', 0))

        # Add fields for authors (organizations)
        self._add_organization_fields('author', self.initial.get('number_of_authors_organization', 0))

    def _add_person_fields(self, prefix, count):
        """Helper method to add fields for a person (author or editor)."""
        for i in range(count):
            self.fields[f'{prefix}_person_{i}_given_name'] = forms.CharField(
                required=False,
                widget=forms.TextInput(attrs={'class': 'form-control form-outline'})
            )
            self.fields[f'{prefix}_person_{i}_family_name'] = forms.CharField(
                required=False,
                widget=forms.TextInput(attrs={'class': 'form-control form-outline'})
            )
            self.fields[f'{prefix}_person_{i}_orcid'] = forms.CharField(
                required=False,
                widget=forms.TextInput(attrs={'class': 'form-control form-outline'})
            )
            self.fields[f'{prefix}_person_{i}_affiliation'] = forms.CharField(
                required=False,
                widget=forms.TextInput(attrs={'class': 'form-control form-outline'})
            )

    def _add_organization_fields(self, prefix, count):
        """Helper method to add fields for an organization (author or editor)."""
        for i in range(count):
            self.fields[f'{prefix}_org_{i}_name'] = forms.CharField(
                required=False,
                widget=forms.TextInput(attrs={'class': 'form-control form-outline'})
            )
            self.fields[f'{prefix}_org_{i}_rorid'] = forms.CharField(
                required=False,
                widget=forms.TextInput(attrs={'class': 'form-control form-outline'})
            )
            self.fields[f'{prefix}_org_{i}_sequence_number'] = forms.IntegerField(
                required=False,
                widget=forms.NumberInput(attrs={'class': 'form-control form-outline'})
            )
            self.fields[f'{prefix}_org_{i}_parent_org_name'] = forms.CharField(
                required=False,
                widget=forms.TextInput(attrs={'class': 'form-control form-outline'})
            )

    def clean(self):
        cleaned_data = super().clean()
        # Editor counts are optional inputs; treat blank as 0 so the NOT NULL
        # model columns always receive a value.
        for f in ('number_of_editors_people', 'number_of_editors_organization'):
            if cleaned_data.get(f) is None:
                cleaned_data[f] = 0
        return cleaned_data
    


class EditCitationInformationForm(forms.Form):

    def __init__(self, *args, **kwargs):
        self.pk_cit = kwargs.pop('pk_cit')
        super(EditCitationInformationForm, self).__init__(*args, **kwargs)

        self.citation_information = Citation_Information.objects.get(pk=self.pk_cit)

        # Add fields for authors (people)
        self._add_person_fields('author', self.citation_information.number_of_authors_people)

        # Add fields for authors (organizations)
        self._add_organization_fields('author', self.citation_information.number_of_authors_organization)

        # Add fields for user-added editors. The two fixed ATM editors (Lynn
        # Neakrase, Lyle Huber) are never editable and get no form fields.
        self._add_person_fields('editor', self.citation_information.number_of_editors_people)
        self._add_organization_fields('editor', self.citation_information.number_of_editors_organization)

    def _add_person_fields(self, prefix, count):
        """Helper method to add fields for a person (author or editor)."""
        for i in range(count):
            # Creating labels so that the words are capitalized
            given_name_label = f"{prefix.capitalize()} Person {i+1} Given Name"
            family_name_label = f"{prefix.capitalize()} Person {i+1} Family Name"

            # Adding ORCID field with lookup link -RUPAK
            orcid_label = mark_safe(
                f'{prefix.capitalize()} Person {i+1} ORCID '
                f'<a href="https://orcid.org/orcid-search/search?searchQuery=" '
                f'target="_blank" class="ms-2" '
                f'title="Click to search for your ORCID ID">'
                f'Lookup ORCID <i class="bi bi-search"></i></a>'
            )

            affiliation_label = f"{prefix.capitalize()} Person {i+1} Affiliation"
            
            self.fields[f'{prefix}_person_{i}_given_name'] = forms.CharField(
                required=False,
                label=given_name_label,
                widget=forms.TextInput(attrs={'class': 'form-control form-outline'})
            )
            self.fields[f'{prefix}_person_{i}_family_name'] = forms.CharField(
                required=False,
                label=family_name_label,
                widget=forms.TextInput(attrs={'class': 'form-control form-outline'})
            )
            self.fields[f'{prefix}_person_{i}_orcid'] = forms.CharField(
                required=False,
                label=orcid_label,
                widget=forms.TextInput(attrs={
                    'class': 'form-control form-outline',
                    'data-bs-toggle': 'tooltip',
                    'data-bs-placement': 'right',
                    'title': 'Find your ORCID at https://orcid.org/orcid-search/search'
                })
            )
            self.fields[f'{prefix}_person_{i}_affiliation'] = forms.CharField(
                required=False,
                label=affiliation_label,
                widget=forms.TextInput(attrs={'class': 'form-control form-outline'})
            )

    def _add_organization_fields(self, prefix, count):
        """Helper method to add fields for an organization (author or editor)."""
        for i in range(count):
            # Creating labels so that the words are capitalized
            name_label = f"{prefix.capitalize()} Organization {i+1} Name"
            
            # Adding RORID field with lookup link
            rorid_label = mark_safe(
                f'{prefix.capitalize()} Organization {i+1} RORID '
                f'<a href="https://ror.org/search" '
                f'target="_blank" class="ms-2" '
                f'title="Click to search for your ROR ID">'
                f'Lookup RORID <i class="bi bi-search"></i></a>'
            )

            sequence_label = f"{prefix.capitalize()} Organization {i+1} Sequence Number"
            parent_org_label = f"{prefix.capitalize()} Organization {i+1} Parent Organization Name"
            
            self.fields[f'{prefix}_org_{i}_name'] = forms.CharField(
                required=False,
                label=name_label,
                widget=forms.TextInput(attrs={'class': 'form-control form-outline'})
            )
            self.fields[f'{prefix}_org_{i}_rorid'] = forms.CharField(
                required=False,
                label=rorid_label,
                widget=forms.TextInput(attrs={'class': 'form-control form-outline'})
            )
            self.fields[f'{prefix}_org_{i}_sequence_number'] = forms.IntegerField(
                required=False,
                label=sequence_label,
                widget=forms.NumberInput(attrs={'class': 'form-control form-outline'})
            )
            self.fields[f'{prefix}_org_{i}_parent_org_name'] = forms.CharField(
                required=False,
                label=parent_org_label,
                widget=forms.TextInput(attrs={'class': 'form-control form-outline'})
            )




"""
Test
"""


"""
    Modification History 
"""


class ModificationHistoryForm(forms.ModelForm):
    # figure out how to add defaults
    description = forms.CharField(required=False, widget=forms.TextInput(attrs={
        'class': 'form-control',
        'id': 'mod_desc'
    }))

    modification_date = forms.DateField(
        required=True,
        initial=datetime.date.today().strftime('%Y-%m-%d'),
        input_formats=['%Y-%m-%d'],
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'id': 'mod_date',
            'placeholder': 'YYYY-MM-DD'
    }))

    version_id = forms.CharField(required=True, initial='1.0', widget=forms.TextInput(attrs={
        'class': 'form-control',
        'id': 'version_id'
    }))

    # validators=[RegexValidator(r'^\d{1,10}$')])

    class Meta(object):
        model = Modification_History
        exclude = ('bundle',)

    """
        clean should do nothing to the description.  For publication_year, CitationInformationForm uses Django's DateField form field.  Django's DateField form field (https://docs.djangoproject.com/en/2.0/_modules/django/forms/fields/#DateField) simply sees if the input could be converted to a date time object.  Therefore, values like 6020 can be input.  We need to decide if we want to prevent user errors such as this, raise warnings to the user, do nothing, etc...
    """

    def clean(self):
        pass


"""
    Collections
"""


class CollectionsForm(forms.ModelForm):

  #  has_document = forms.BooleanField(initial=True)
    # has_data = forms.BooleanField(required=False, initial=False)
    has_document = True
  #  has_context = forms.BooleanField(initial=True)
    has_context = True
    has_xml_schema = True
       
   # has_raw_data = forms.BooleanField(required=False, initial=False)

    #has_calibrated_data = forms.BooleanField(required=False, initial=False)
    #has_derived_data = forms.BooleanField(required=False, initial=False)
    #data_enum = forms.IntegerField(required=False, min_value = 0, max_value=25)

    class Meta(object):
        model = Collections
        exclude = ('bundle',)


class AdditionalCollectionForm(forms.ModelForm):
    collection_name = forms.CharField(required=True, max_length=100, widget=forms.TextInput(attrs={
        'class': 'form-control form-outline',
        'id': 'col_name'
    }))

    def __init__(self, *args, **kwargs):
        bundle = kwargs.pop("bundle", None)
        super().__init__(*args, **kwargs)

        if bundle and bundle.bundle_type == "External":
            self.fields['collection_type'].choices = [
                ('External', 'External'),
            ]
        else:
            self.fields['collection_type'].choices = [
                ('Data', 'Data'),
                ('Browse', 'Browse'),
                ('Geometry', 'Geometry'),
                ('Calibration', 'Calibration'),
            ]

    class Meta(object):
        model = AdditionalCollections
        exclude = ('bundle', 'collection')



"""
    Data Prep
"""
"""
class DataObjectForm(forms.ModelForm):
    class Meta:
        model = Data_Object
        fields = ('name', 'data_type')
        exclude = ('bundle',)

"""
"""
    Data Emun
"""
"""
class DataEnum(forms.ModelForm):
    class Meta:
        model = Data
        exclude = ('bundle','processing_level',)

"""


"""
    Data
"""


class DataForm(forms.ModelForm):
    name = forms.CharField(required=True)
    class Meta(object):
        model = Data
        # exclude = ('bundle',)
        # fields = ['name', 'processing_level', 'data_type', 'header', 'collection']
        # widgets = {
        #     'collection': forms.HiddenInput(),  # This makes the field hidden
        # }
        exclude = ('bundle','collection')
        #exclude = ('bundle','data_enum',)

    def __init__(self, *args, **kwargs):
        self.pk_bun = kwargs.pop('pk_bun')
        super(DataForm, self).__init__(*args, **kwargs)
        self.fields['collection'] = forms.ModelChoiceField(queryset=AdditionalCollections.objects.filter(bundle=self.pk_bun), required = True)

"""
    Facility
"""


class FacilityForm(forms.Form):
    facility = forms.ModelChoiceField(
        queryset=Facility.objects.all(), required=True)


"""
    Telescope
"""


class TelescopeForm(forms.Form):
    telescope = forms.ModelChoiceField(
        queryset=Telescope.objects.all(), required=True)


"""
    Facility Instrument
    
    HERE : https://medium.com/@MicroPyramid/understanding-djangos-model-fromsets-in-detail-and-their-advanced-usage-131dfe66853d
"""


class FacilityInstrumentForm(forms.Form):

    instrument = forms.ModelChoiceField(
        queryset=Instrument.objects.all(), required=True)

    def __init__(self, *args, **kwargs):
        self.pk_fac = kwargs.pop('pk_fac')
        super(FacilityInstrumentForm, self).__init__(*args, **kwargs)
        self.fields['instrument'] = forms.ModelChoiceField(
            queryset=Instrument.objects.filter(facility=self.pk_fac), required=True)


"""
    Investigation
"""


class InvestigationForm(forms.Form):
    investigation = forms.ModelChoiceField(queryset=Investigation.objects.all(
    ), required=True, help_text="Note: Investigations contain: individual investigations, missions, observing campaigns, or other investigations</br>")


"""
    Instrument Host
    
    HERE : https://medium.com/@MicroPyramid/understanding-djangos-model-fromsets-in-detail-and-their-advanced-usage-131dfe66853d
"""


class InstrumentHostForm(forms.Form):

    instrument_host = forms.ModelChoiceField(
        queryset=Instrument_Host.objects.all(), required=True)

    def __init__(self, *args, **kwargs):
        self.pk_inv = kwargs.pop('pk_inv')
        super(InstrumentHostForm, self).__init__(*args, **kwargs)
        print(Instrument_Host.objects.filter(investigations=self.pk_inv))
        self.fields['instrument_host'] = forms.ModelChoiceField(
            queryset=Instrument_Host.objects.filter(investigations=self.pk_inv), required=True)


"""
    Target
    
    HERE : https://medium.com/@MicroPyramid/understanding-djangos-model-fromsets-in-detail-and-their-advanced-usage-131dfe66853d
"""


class TargetForm(forms.Form):

    target = forms.ModelChoiceField(
        queryset=Target.objects.all(), required=True)
    # target = forms.CharField(max_length=100, label='Search')

    def __init__(self, *args, **kwargs):
        self.pk_ins = kwargs.pop('pk_ins')
        self.pk_bundle = kwargs.pop('pk_bundle')

        self.bundle = Bundle.objects.get(pk=self.pk_bundle)
        
        # self.bundle_type = kwargs.pop('bundle_type', None)

        super(TargetForm, self).__init__(*args, **kwargs)

        self.related_pks = set()
        self.related_investigation_names = []

        # If the bundle type is 'External', we filter targets that start with 'urn:nasa:pds:context:target:laboratory_analog' -Rupak
        if self.bundle.bundle_type == 'External':
            self.fields['target'] = forms.ModelChoiceField(
                queryset=Target.objects.filter(lid__startswith='urn:nasa:pds:context:target:laboratory_analog'),
                required=True
            )
        else:
            # This list used to be restricted to the investigation's own targets,
            # which made an observation of anything the mission is not formally
            # listed against impossible to record. PDS4 does not tie a target to
            # an investigation, so the list is grouped rather than filtered:
            # related targets first, everything else still selectable underneath.
            investigation = Investigation.objects.filter(pk=self.pk_ins).first()
            if investigation is not None:
                self.related_investigation_names = [
                    investigation_display_name(investigation)
                ]
                self.related_pks = set(
                    investigation.targets.values_list('pk', flat=True)
                )

            related_groups = []
            if self.related_pks:
                related_groups.append((
                    'Listed for {}'.format(self.related_investigation_names[0]),
                    self.related_pks,
                ))

            self.fields['target'] = GroupedTargetChoiceField(
                queryset=Target.objects.all(),
                required=True,
                related_groups=related_groups,
            )

    @property
    def related_investigation_label(self):
        names = self.related_investigation_names
        return names[0] if names else ''

    @property
    def related_pks_json(self):
        return json.dumps(sorted(self.related_pks))
    
def investigation_display_name(investigation):
    """Best available label for an investigation.

    Nine context investigations came out of the crawl with a blank name (DART
    among them), which would render as an empty optgroup heading. Fall back to
    the LID's trailing segment, so 'mission.double_asteroid_redirection_test'
    reads as 'Double Asteroid Redirection Test'.
    """
    if investigation is None:
        return 'this investigation'

    name = (investigation.name or '').strip()
    if name:
        return name

    lid = (investigation.lid or '').strip()
    if lid:
        segment = lid.split(':')[-1]
        # Drop the leading type qualifier, e.g. 'mission.' or 'individual.'.
        if '.' in segment:
            segment = segment.split('.', 1)[1]
        segment = segment.replace('_', ' ').strip()
        if segment:
            return segment.title()

    return 'this investigation'


class GroupedTargetIterator(forms.models.ModelChoiceIterator):
    """Groups the target list by investigation, then lists everything else.

    One optgroup per investigation rather than a single merged "related" group,
    because merging cannot say which investigation a target came from, and a
    target may legitimately be listed for several of them (the Moon is listed
    for both Apollo 11 and Apollo 17, so it appears under each).

    PDS4 does not tie a target to an investigation: Target is a standalone
    context product, and Target_Identification sits alongside Investigation_Area
    in a label with no link between them. So this grouping is a navigation aid,
    never a restriction. Every target stays selectable.
    """

    def __iter__(self):
        field = self.field

        if field.empty_label is not None:
            yield ('', field.empty_label)

        objects = list(self.queryset)
        grouped = set()

        for label, pks in field.related_groups:
            if not pks:
                # An investigation PDS publishes no targets for gets no empty
                # heading, which would read as "this mission has no targets".
                continue
            members = [self.choice(o) for o in objects if o.pk in pks]
            if members:
                yield (label, members)
                grouped |= pks

        others = [self.choice(o) for o in objects if o.pk not in grouped]

        if grouped:
            yield ('All other targets', others)
        else:
            # Nothing published for this bundle's investigations, so a flat list
            # is more honest than an empty "related" group.
            for choice in others:
                yield choice


class GroupedTargetChoiceField(forms.ModelChoiceField):
    iterator = GroupedTargetIterator

    def __init__(self, *args, **kwargs):
        # List of (optgroup label, set of target pks), one entry per investigation.
        self.related_groups = list(kwargs.pop('related_groups', ()) or ())
        super(GroupedTargetChoiceField, self).__init__(*args, **kwargs)


class TargetFormAll(forms.Form):

    target = forms.ModelChoiceField(
        queryset=Target.objects.all(), required=True)
    # target = forms.CharField(max_length=100, label='Search')

    def __init__(self, *args, **kwargs):
        self.pk_bundle = kwargs.pop('pk_bundle')

        self.bundle = Bundle.objects.get(pk=self.pk_bundle)

        super(TargetFormAll, self).__init__(*args, **kwargs)

        self.related_pks = set()
        self.related_investigation_names = []

        if self.bundle.bundle_type == 'External':
            self.fields['target'] = forms.ModelChoiceField(
                queryset=Target.objects.filter(lid__startswith='urn:nasa:pds:context:target:laboratory_analog'),
                required=True
            )
        else:
            investigations = list(self.bundle.investigations.all())
            self.related_investigation_names = [
                investigation_display_name(i) for i in investigations
            ]

            # One group per investigation, so the menu says which investigation
            # each target is listed for instead of merging them into one heading.
            related_groups = []
            for investigation in investigations:
                pks = set(
                    investigation.targets.values_list('pk', flat=True)
                )
                if not pks:
                    continue
                related_groups.append((
                    'Listed for {}'.format(investigation_display_name(investigation)),
                    pks,
                ))
                self.related_pks |= pks

            self.fields['target'] = GroupedTargetChoiceField(
                queryset=Target.objects.all(),
                required=True,
                related_groups=related_groups,
            )

    @property
    def related_investigation_label(self):
        """Human-readable investigation list, for the caution message."""
        names = self.related_investigation_names
        if not names:
            return ''
        if len(names) == 1:
            return names[0]
        if len(names) <= 3:
            return '{} and {}'.format(', '.join(names[:-1]), names[-1])
        return '{} and {} others'.format(', '.join(names[:3]), len(names) - 3)

    @property
    def related_pks_json(self):
        """Target pks PDS lists for this bundle's investigations.

        Consumed by the template so it can raise a caution when the user picks
        something outside the list. Empty means we have nothing to compare
        against and no caution should fire.
        """
        return json.dumps(sorted(self.related_pks))



"""
    Instrument
    
    HERE : https://medium.com/@MicroPyramid/understanding-djangos-model-fromsets-in-detail-and-their-advanced-usage-131dfe66853d
"""


class InstrumentForm(forms.Form):

    instrument = forms.ModelChoiceField(
        queryset=Instrument.objects.all(), required=True)

    def __init__(self, *args, **kwargs):
        self.pk_ins = kwargs.pop('pk_ins')
        super(InstrumentForm, self).__init__(*args, **kwargs)
        self.fields['instrument'] = forms.ModelChoiceField(
            queryset=Instrument.objects.filter(instrument_host=self.pk_ins), required=True)


"""
    ProductBundle
"""


class ProductBundleForm(forms.ModelForm):

    class Meta(object):
        model = Product_Bundle
        exclude = ('bundle',)


"""
    ProductCollection
"""


class ProductCollectionForm(forms.ModelForm):

    class Meta(object):
        model = Product_Collection
        exclude = ('bundle', 'collection')



"""
12.1  Document

Root Class:Tagged_NonDigital_Object
Role:Concrete

Class Description:The Document class describes a document.

Steward:pds
Namespace Id:pds
Version Id:2.0.0.0
          Entity         Card         Value/Class         Ind

Hierarchy        Tagged_NonDigital_Object                           
                . TNDO_Supplemental                           
                 . . Document                           

Subclass        none                           

Attribute
        acknowledgement_text        0..1                  
         author_list             0..1                  
         copyright               0..1                  
         description                0..1                  
         document_editions        0..1                  
         document_name                0..1  An exec decision has been made to make document_name required
         doi                        0..1                  
         editor_list                0..1                  
         publication_date        1                  
         revision_id                0..1                  

Inherited Attribute        none                           
Association                data_object                1        Digital_Object         
                         has_document_edition        1..*        Document_Edition         
Inherited Association        none                           
Referenced from        Product_Document                           
"""

# List of STD_ID types for file types - deric
STD_ID = [
    ('PDF/A', 'PDF/A'),
    ('ASCII', '7-Bit ASCII'),
    ('Encapsulated Postscript', 'Encapsulated Postscript'),
    ('GIF', 'GIF'),
    # Older versions of HTML are now deprecated and is now just HTML
    # ('HTML v2.0', 'HTML v2.0'),
    # ('HTML v3.2', 'HTML v3.2'),
    # ('HTML v4.0', 'HTML v4.0'),
    # ('HTML v4.01', 'HTML v4.01'),
    ('HTML', 'HTML'),
    ('JPEG', 'JPEG'),
    ('LaTEX', 'LaTEX'),
    ('MPEG', 'MPEG-4'),
    ('Excel', 'MS Excel'),
    ('Word', 'MS Word'),
    ('PDF', 'PDF'),
    ('PNG', 'PNG'),
    ('Postscript', 'Postscript'),
    ('Rich Text', 'Rich Text'),
    ('TIFF', 'TIFF'),
    ('UTF-8', 'UTF-8 Text')
]

# PE = Product External
PE_STD_ID = [
    ('PDF/A', 'PDF/A'),
    ('ASCII', '7-Bit ASCII')
]

# Nov. 24, 2025 -- External Bundles are supposed to have some different fields for document collections.
class AnnexProductDocumentForm(forms.ModelForm):

    document_name = forms.CharField(
        required = True,
        max_length=100,
        label='Document Title',
        label_suffix = '',
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Enter document title"
        })
    )

    document_id = forms.CharField(
        required = True,
        max_length = 100,
        label='Document ID',
        label_suffix = '',
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "Enter a document_id for the URN"
        })
    )

    comment = forms.CharField(
        required=False,
        label_suffix = '',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            "placeholder": "Enter a comment for the file"
        })
    )

    file_name = forms.CharField(
        required = True,
        max_length=100,
        label='File Name',
        label_suffix='',
        widget=forms.TextInput(attrs={
            "class":"form-control",
            "placeholder": "Enter file name (e.g. User_Guide.pdf)",
        })
    )

    document_std_id = forms.ChoiceField(
        required=False,
        choices=PE_STD_ID,
        label='File Format',
        label_suffix = '',
        widget=forms.Select(attrs={
            'class': 'form-control custom-select'
        })
    )
    class Meta:
        model = Product_Document
        fields = [
            "document_name",
            "document_id",
            "file_name",
            "comment",
            "document_std_id",
        ]



class ProductDocumentForm(forms.ModelForm):
    document_name = forms.CharField(
        required=True,
        max_length=100,
        label_suffix = '',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            # 'placeholder': 'Document Name'
        })
        
    )
    publication_date = forms.CharField(
        required=True,
        max_length=100,
        label_suffix = '',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
           # 'placeholder': 'Publication Date'
        })
    )
    author_list = forms.CharField(
        required=False,
        max_length=100,
        label_suffix = '',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
          #  'placeholder': 'Author List'
        })
    )
    copyright = forms.CharField(
        required=False,
        max_length=100,
        label_suffix = '',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
          #  'placeholder': 'Copyright'
        })
    )
    description = forms.CharField(
        required=False,
        label_suffix = '',
        widget=forms.Textarea(attrs={
            'class': 'form-control',
          #  'placeholder': 'Description',
            'rows': 3
        })
    )

    revision_id = forms.CharField(
        required=False,
        max_length=100,
        label_suffix = '',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
           # 'placeholder': 'Revision ID'
        })
    )

    document_editions = forms.IntegerField(
        required=False,
        label_suffix = '',
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
           # 'placeholder': 'Document Editions'
        })
    )
    
    edition_name = forms.CharField(
        required=False,
        max_length=100,
        label_suffix = '',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            #'placeholder': 'Edition Name'
        })
    )
    language = forms.CharField(
        required=False,
        max_length=100,
        label_suffix = '',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            #'placeholder': 'Language'
        })
    )
    files = forms.IntegerField(
        required=False,
        label_suffix = '',
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
           # 'placeholder': 'Number of Files'
        })
    )
    file_name = forms.CharField(
        required=False,
        max_length=100,
        label_suffix = '',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            #'placeholder': 'File Name'
        })
    )
    local_id = forms.CharField(
        required=False,
        max_length=100,
        label_suffix = '',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            #'placeholder': 'Local ID'
        })
    )
    document_std_id = forms.ChoiceField(
        required=False,
        choices=STD_ID,
        label_suffix = '',
        widget=forms.Select(attrs={
            'class': 'form-control custom-select'
        })
    )

    class Meta:
        model = Product_Document
        #exclude = ('bundle',)
        fields = [
            "document_name",
            "publication_date",
            "author_list",
            "copyright",
            "description",
            "revision_id",
            "document_editions",
            "edition_name",
            "language",
            "files",
            "file_name",
            "local_id",
            "document_std_id",
        ]


"""
    ProductObservational
"""


class ProductObservationalForm(forms.ModelForm):
    OBSERVATIONAL_TYPES = [

        ('Array', 'Array'),
        ('Table', 'Table'),
        #('Table Binary','Table Binary'),
        #('Table Character','Table Character'),
        #('Table Delimited','Table Delimited'),
    ]
    PURPOSE_TYPES = [
        ('Calibration', 'Calibration'),
        ('Checkout', 'Checkout'),
        ('Engineering', 'Engineering'),
        ('Navigation', 'Navigation'),
        ('Observation Geometry', 'Observation Geometry'),
        ('Science', 'Science'),
    ]
    purpose = forms.ChoiceField(required=True, choices=PURPOSE_TYPES)
    title = forms.CharField(required=True)
    type_of = forms.ChoiceField(required=True, choices=OBSERVATIONAL_TYPES)

    class Meta(object):
        model = Product_Observational
        exclude = ('bundle', 'data', 'processing_level')


# An experimental attempt to get forms of existing objects to populate their data when we look at them. -J
'''
class Table_Delimited_Form(forms.Form):
    table_delimited = forms.ModelChoiceField(queryset=Table_Delimited.objects.none())

    def __init__(self, item_id):
        super(Table_Delimited_Form, self).__init__()
        self.fields['table_delimited'].queryset = Table_Delimited.objects.filter(id=item_id)
'''


class Table_Delimited_Form(forms.ModelForm):

    # data = forms.ModelChoiceField(queryset=Data.objects.all(), required=False)

    class Meta(object):
        model = Table_Delimited
        exclude = ('bundle',)

    def __init__(self, *args, **kwargs):
        self.pk_data = kwargs.pop('pk_data')
        self.pk_ins = kwargs.pop('pk_ins')
        self.pk_bun = kwargs.pop('pk_bun')
        data = Data.objects.get(pk=self.pk_data)

        super(Table_Delimited_Form, self).__init__(*args, **kwargs)

        if data.header:
            self.fields['local_identifier'] = forms.CharField(
                required=True,
                # widget=forms.TextInput(attrs={'class': 'form-control form-outline'})
            )

            # self.fields['header_offset'] = forms.IntegerField(
            #     required=True,
            #     # widget=forms.NumberInput(attrs={'class': 'form-control form-outline'})
            # )

            self.fields['header_object_length'] = forms.IntegerField(
                required=True,
                # widget=forms.NumberInput(attrs={'class': 'form-control form-outline'})
            )

        self.fields['offset'] = forms.IntegerField(min_value=0, required=False)
        
        self.fields['data'] = forms.ModelChoiceField(queryset=Data.objects.filter(name=self.pk_ins), required = True)
        self.fields['collection'] = forms.ModelChoiceField(queryset=AdditionalCollections.objects.filter(bundle=self.pk_bun), required = True)

    # name = models.CharField(max_length=256, blank=True)
    # offset = models.IntegerField(default=0)
    # object_length = models.IntegerField(default=1)
    # description = models.CharField(max_length=5000, default="unset")
    # records = models.IntegerField(default=1)
    # field_delimiter = models.CharField(max_length=256, choices=DELIMITER_CHOICES, default="Comma", blank=True)
    # fields = models.IntegerField(default=1)
    # facet1 = models.CharField(max_length=256, choices=PRIMARY_RESULTS_SUMMARY_FACET_CHOICES, default="Meteorology", blank=True)
    # data = models.ForeignKey(Data, on_delete=models.CASCADE, null=True)
    # collection = models.ForeignKey(AdditionalCollections, on_delete = models.CASCADE, default='',)
        self.fields['name'].widget.attrs.update({
            'class': 'form-control form-outline'
        })

        self.fields['offset'].widget.attrs.update({
            'class': 'form-control form-outline'
        })

        self.fields['object_length'].widget.attrs.update({
            'class': 'form-control form-outline'
        })

        self.fields['description'].widget.attrs.update({
            'class': 'form-control form-outline'
        })

        self.fields['records'].widget.attrs.update({
            'class': 'form-control form-outline'
        }) 

        self.fields['field_delimiter'].widget.attrs.update({
            'class': 'form-control form-outline'
        })

        self.fields['fields'].widget.attrs.update({
            'class': 'form-control form-outline'
        })     

        self.fields['facet1'].widget.attrs.update({
            'class': 'form-control form-outline'
        })

        self.fields['data'].widget.attrs.update({
            'class': 'form-control form-outline'
        })

        self.fields['collection'].widget.attrs.update({
            'class': 'form-control form-outline'
        })   
class Table_Binary_Form(forms.ModelForm):

    # data = forms.ModelChoiceField(queryset=Data.objects.all(), required=False)

    class Meta(object):
        model = Table_Binary
        exclude = ('bundle',)
    
    def __init__(self, *args, **kwargs):
        self.pk_data = kwargs.pop('pk_data')
        self.pk_ins = kwargs.pop('pk_ins')
        self.pk_bun = kwargs.pop('pk_bun')
        data = Data.objects.get(pk=self.pk_data)

        super(Table_Binary_Form, self).__init__(*args, **kwargs)

        if data.header:
            self.fields['local_identifier'] = forms.CharField(
                required=True,
                widget=forms.TextInput(attrs={'class': 'form-control form-outline'})
            )

            # self.fields['header_offset'] = forms.IntegerField(
            #     required=True,
            #     # widget=forms.NumberInput(attrs={'class': 'form-control form-outline'})
            # )

            self.fields['header_object_length'] = forms.IntegerField(
                required=True,
                widget=forms.NumberInput(attrs={'class': 'form-control form-outline'})
            )

    # name = models.CharField(max_length=256, blank=True)
    # offset = models.IntegerField(default=1)
    # records = models.IntegerField(default=1)
    # fields = models.IntegerField(default=1)
    # facet1 = models.CharField(max_length=256, choices=PRIMARY_RESULTS_SUMMARY_FACET_CHOICES, default="Meteorology", blank=True)
    # data = models.ForeignKey(Data, on_delete=models.CASCADE, null=True)
    # collection = models.ForeignKey(AdditionalCollections, on_delete = models.CASCADE, default='',)
    # bundle = models.ForeignKey(Bundle, on_delete=models.CASCADE, null=True,)      

        self.fields['offset'] = forms.IntegerField(min_value=0, required=False)

        self.fields['data'] = forms.ModelChoiceField(queryset=Data.objects.filter(name=self.pk_ins), required = True)
        self.fields['collection'] = forms.ModelChoiceField(queryset=AdditionalCollections.objects.filter(bundle=self.pk_bun), required = True)

        self.fields['name'].widget.attrs.update({
            'class': 'form-control form-outline'
        })

        self.fields['records'].widget.attrs.update({
            'class': 'form-control form-outline'
        })

        self.fields['fields'].widget.attrs.update({
            'class': 'form-control form-outline'
        })  

        self.fields['facet1'].widget.attrs.update({
            'class': 'form-control form-outline'
        })

        self.fields['offset'].widget.attrs.update({
            'class': 'form-control form-outline'
        })

        self.fields['data'].widget.attrs.update({
            'class': 'form-control form-outline'
        })

        self.fields['collection'].widget.attrs.update({
            'class': 'form-control form-outline'
        })       

class Table_Fixed_Width_Form(forms.ModelForm):

    # data = forms.ModelChoiceField(queryset=Data.objects.all(), required=False)

    class Meta(object):
        model = Table_Fixed_Width
        exclude = ('bundle',)
    
    def __init__(self, *args, **kwargs):
        self.pk_data = kwargs.pop('pk_data')
        self.pk_ins = kwargs.pop('pk_ins')
        self.pk_bun = kwargs.pop('pk_bun')
        data = Data.objects.get(pk=self.pk_data)

        super(Table_Fixed_Width_Form, self).__init__(*args, **kwargs)

        if data.header:
            self.fields['local_identifier'] = forms.CharField(
                required=True,
                # widget=forms.TextInput(attrs={'class': 'form-control form-outline'})
            )

            # self.fields['header_offset'] = forms.IntegerField(
            #     required=True,
            #     # widget=forms.NumberInput(attrs={'class': 'form-control form-outline'})
            # )

            self.fields['header_object_length'] = forms.IntegerField(
                required=True,
                # widget=forms.NumberInput(attrs={'class': 'form-control form-outline'})
            )

        self.fields['offset'] = forms.IntegerField(min_value=0, required=False)

        self.fields['data'] = forms.ModelChoiceField(queryset=Data.objects.filter(name=self.pk_ins), required = True)
        self.fields['collection'] = forms.ModelChoiceField(queryset=AdditionalCollections.objects.filter(bundle=self.pk_bun), required = True)

        self.fields['name'].widget.attrs.update({
            'class': 'form-control form-outline'
        })

        self.fields['offset'].widget.attrs.update({
            'class': 'form-control form-outline'
        })

        self.fields['object_length'].widget.attrs.update({
            'class': 'form-control form-outline'
        })

        self.fields['description'].widget.attrs.update({
            'class': 'form-control form-outline'
        })

        self.fields['records'].widget.attrs.update({
            'class': 'form-control form-outline'
        }) 

        self.fields['fields'].widget.attrs.update({
            'class': 'form-control form-outline'
        })     

        self.fields['facet1'].widget.attrs.update({
            'class': 'form-control form-outline'
        })

        self.fields['data'].widget.attrs.update({
            'class': 'form-control form-outline'
        })

        self.fields['collection'].widget.attrs.update({
            'class': 'form-control form-outline'
        })   

class EditTableFieldsForm(forms.Form):

    def __init__(self, *args, **kwargs):
        self.pk_table = kwargs.pop('pk_table')
        self.pk_data = kwargs.pop('pk_data')      

        super(EditTableFieldsForm, self).__init__(*args, **kwargs)

        self.data_instance = Data.objects.get(pk=self.pk_data)

        if self.data_instance.data_type == 'Table Delimited':
            self.table = Table_Delimited.objects.get(pk=self.pk_table)
        elif self.data_instance.data_type == 'Table Binary':
            self.table = Table_Binary.objects.get(pk=self.pk_table)
        elif self.data_instance.data_type == 'Table Character':
            self.table = Table_Fixed_Width.objects.get(pk=self.pk_table)

        # Add fields for table 
        self._add_fields(self.table.fields)

    def _add_fields(self, count):
        """Helper method to add fields for a Table."""
        for i in range(count):
            print('in adding firls loop')
            # Creating labels so that the words are capitalized
            name_label = f"Field {i+1} Name"
            field_number_label = f"Field {i+1} Field Number"
            data_type_label = f"Field {i+1} Data Type"
            field_location_label = f"Field {i + 1} Field Location"
            description_label = f"Field {i + 1} Description"
            max_field_length_label = f"Field {i+1} Maximum Field Length"

            if self.data_instance.data_type == 'Table Delimited':
                self.fields[f'name_{i}'] = forms.CharField(
                    required=False, 
                    label=name_label,
                    widget=forms.TextInput(attrs={'class': 'form-control form-outline'})
                )
                self.fields[f'field_number_{i}'] = forms.IntegerField(
                    required=False,
                    min_value=0,
                    label=field_number_label,
                    widget=forms.TextInput(attrs={'class': 'form-control form-outline'})
                )
                self.fields[f'data_type_{i}'] = forms.CharField(
                    required=False,
                    label=data_type_label,
                    widget=forms.TextInput(attrs={'class': 'form-control form-outline'})
                )
                self.fields[f'description_{i}'] = forms.IntegerField(
                    required=False,
                    label=description_label,
                    widget=forms.TextInput(attrs={'class': 'form-control form-outline'})
                )
            elif self.data_instance.data_type == 'Table Binary':
                self.fields[f'name_{i}'] = forms.CharField(
                    required=False, 
                    label=name_label,
                    widget=forms.TextInput(attrs={'class': 'form-control form-outline'})
                )
                self.fields[f'field_location_{i}'] = forms.CharField(
                    required=False, 
                    label=field_location_label,
                    widget=forms.TextInput(attrs={'class': 'form-control form-outline'})
                )
                self.fields[f'data_type_{i}'] = forms.CharField(
                    required=False,
                    label=data_type_label,
                    widget=forms.TextInput(attrs={'class': 'form-control form-outline'})
                )
                self.fields[f'max_field_length_{i}'] = forms.IntegerField(
                    required=False,
                    label=max_field_length_label,
                    widget=forms.TextInput(attrs={'class': 'form-control form-outline'})
                )
                self.fields[f'description_{i}'] = forms.IntegerField(
                    required=False,
                    label=description_label,
                    widget=forms.TextInput(attrs={'class': 'form-control form-outline'})
                )
            elif self.data_instance.data_type == 'Table Character':
                self.fields[f'name_{i}'] = forms.CharField(
                    required=False, 
                    label=name_label,
                    widget=forms.TextInput(attrs={'class': 'form-control form-outline'})
                )
                self.fields[f'field_location_{i}'] = forms.CharField(
                    required=False, 
                    label=field_location_label,
                    widget=forms.TextInput(attrs={'class': 'form-control form-outline'})
                )
                self.fields[f'data_type_{i}'] = forms.CharField(
                    required=False,
                    label=data_type_label,
                    widget=forms.TextInput(attrs={'class': 'form-control form-outline'})
                )
                self.fields[f'field_number_{i}'] = forms.IntegerField(
                    required=False,
                    min_value=0,
                    label=field_number_label,
                    widget=forms.TextInput(attrs={'class': 'form-control form-outline'})
                )
                self.fields[f'description_{i}'] = forms.CharField(
                    required=False,
                    label=description_label,
                    widget=forms.TextInput(attrs={'class': 'form-control form-outline'})
                )

class Field_Delimited_Form(forms.ModelForm):
    class Meta(object):
        model = Field_Delimited
        exclude = ('table',)


class Field_Binary_Form(forms.ModelForm):
    class Meta(object):
        model = Field_Binary
        exclude = ('table',)


class Field_Character_Form(forms.ModelForm):
    class Meta(object):
        model = Field_Character
        exclude = ('table',)


"""
    Table
"""


class TableForm(forms.ModelForm):
    class Meta(object):
        model = Table
        exclude = ('product_observational',
                   'observational_type', 'local_identifier')
                   


"""
    Context Forms 
"""


'''
"""
    Instrument_Host
"""
class InstrumentHostForm(forms.ModelForm):
    class Meta:
        model = Instrument_Host
        exclude = ('',)











"""
    Instrument
"""
class InstrumentForm(forms.ModelForm):
    class Meta:
        model = Instrument
        exclude = ('',)











"""
    Mission
"""
class MissionForm(forms.ModelForm):
    class Meta:
        model = Mission
        exclude = ('',)













"""
    Target
"""
class TargetForm(forms.ModelForm):
    class Meta:
        model = Target
        exclude = ('',)












"""
    Facility
"""
class Facility(forms.ModelForm):
    class Meta:
        model = Facility
        exclude = ('',)

'''


class ColorDisplaySettingsForm(forms.ModelForm):
    """
The blue_channel_band attribute identifies the
        number of the band, along the band axis, that should be loaded,
        by default, into the blue channel of a display device. The first
        band along the band axis has band number 1.
The color_display_axis attribute identifies, by
        name, the axis of an Array (or Array subclass) that is intended
        to be displayed in the color dimension of a display device.
        I.e., bands from this dimension will be loaded into the red,
        green, and blue bands of the display device. The value of this
        attribute must match the value of one, and only one, axis_name
        attribute in an Axis_Array class of the associated
        Array.
The green_channel_band attribute identifies the
        number of the band, along the band axis, that should be loaded,
        by default, into the green channel of a display device. The
        first band along the band axis has band number
        1.
The red_channel_band attribute identifies the
        number of the band, along the band axis, that should be loaded,
        by default, into the red channel of a display device. The first
        band along the band axis has band number 1.
    """

    class Meta(object):
        model = Color_Display_Settings
        exclude = ('display_dictionary',)


class DisplayDirectionForm(forms.ModelForm):
    """
The horizontal_display_axis attribute
        identifies, by name, the axis of an Array (or Array subclass)
        that is intended to be displayed in the horizontal or "sample"
        dimension on a display device. The value of this attribute must
        match the value of one, and only one, axis_name attribute in an
        Axis_Array class of the associated Array.
The horizontal_display_direction attribute
        specifies the direction across the screen of a display device
        that data along the horizontal axis of an Array is supposed to
        be displayed.
The vertical_display_axis attribute identifies,
        by name, the axis of an Array (or Array subclass) that is
        intended to be displayed in the vertical or "line" dimension on
        a display device. The value of this attribute must match the
        value of one, and only one, axis_name attribute in an Axis_Array
        class of the associated Array.
The vertical_display_direction attribute
        specifies the direction along the screen of a display device
        that data along the vertical axis of an Array is supposed to be
        displayed.
    """

    class Meta(object):
        model = Display_Direction
        exclude = ('display_dictionary',)


class DisplaySettingsForm(forms.ModelForm):
    """
The frame_rate attribute indicates the number of
        still pictures (or frames) that should be displayed per unit of
        time in a video. Note this is NOT necessarily the same as the
        rate at which the images were acquired.
The loop_back_and_forth_flag attribute specifies
        whether or not a movie should only be "looped" or played
        repeatedly in the forward direction, or whether it should be
        played forward followed by played in reverse,
        iteratively.
The loop_count attribute specifies the number of
        times a movie should be "looped" or replayed before
        stopping.
The loop_delay attribute specifies the amount of
        time to pause between "loops" or repeated playbacks of a
        movie.
The loop_flag attribute specifies whether or not
        a movie object should be played repeatedly without prompting
        from the user.
The time_display_axis attribute identifies, by
        name, the axis of an Array (or Array subclass), the bands of
        which are intended to be displayed sequentially in time on a
        display device. The frame_rate attribute, if present, provides
        the rate at which these bands are to be
        displayed.
    """

    class Meta(object):
        model = Display_Settings
        exclude = ('display_dictionary',)


class MovieDisplaySettingsForm(forms.ModelForm):
    """
The Movie_Display_Settings class provides
        default values for the display of a multi-banded Array using a
        software application capable of displaying video
        content.
The frame_rate attribute indicates the number of
        still pictures (or frames) that should be displayed per unit of
        time in a video. Note this is NOT necessarily the same as the
        rate at which the images were acquired.
The loop_back_and_forth_flag attribute specifies
        whether or not a movie should only be "looped" or played
        repeatedly in the forward direction, or whether it should be
        played forward followed by played in reverse,
        iteratively.
The loop_count attribute specifies the number of
        times a movie should be "looped" or replayed before
        stopping.
The loop_delay attribute specifies the amount of
        time to pause between "loops" or repeated playbacks of a
        movie.
The loop_flag attribute specifies whether or not
        a movie object should be played repeatedly without prompting
        from the user.
The time_display_axis attribute identifies, by
        name, the axis of an Array (or Array subclass), the bands of
        which are intended to be displayed sequentially in time on a
        display device. The frame_rate attribute, if present, provides
        the rate at which these bands are to be
        displayed.
    """
    LOOP_DELAY_UNIT_CHOICES = [
        ('microseconds', 'microseconds'),
        ('ms', 'milliseconds'),
        ('s', 'seconds'),
        ('min', 'minute'),
        ('hr', 'hour'),
        ('day', 'day'),
        ('julian day', 'julian day'),
        ('yr', 'year'),
    ]
    loop_delay_unit = forms.RadioSelect(choices=LOOP_DELAY_UNIT_CHOICES)

    class Meta(object):
        model = Movie_Display_Settings
        exclude = ('display_dictionary',)


# class DisplayDictionaryForm(forms.ModelForm):
    """
    This dictionary describes how to display Array data on a display device
The Color_Display_Settings class provides
        guidance to data users on how to display a multi-banded Array
        object on a color-capable display device.
The Display_Direction class specifies how two of
        the dimensions of an Array object should be displayed in the
        vertical (line) and horizontal (sample) dimensions of a display
        device.
The Display_Settings class contains one or more
        classes describing how data should be displayed on a display
        device.
The Movie_Display_Settings class provides
        default values for the display of a multi-banded Array using a
        software application capable of displaying video
        content.
    """


#    class Meta:
#        model = DisplayDictionary
#        exclude = ('array',)


"""
    Confirm
"""


class DictionaryForm(forms.Form):
    CHOICES = [('Display', 'Display'), ('testing', 'testing'), ]
    dictionary_type = forms.MultipleChoiceField(
        choices=CHOICES, widget=forms.CheckboxSelectMultiple())



# # To handle single NetCDF file
# class NetCDFForm(forms.ModelForm):
#     class Meta:
#         model = NetCDFFile
#         fields = ['title', 'file']


# To handle multiple NetCDF files
class MultipleNetCDFUploadForm(forms.Form):
    collection = forms.CharField(widget=forms.HiddenInput)
    netcdf_files = forms.FileField(
        widget=forms.ClearableFileInput(attrs={
            'multiple': True,
            'class': 'form-control'
        }),
        label='Select NetCDF files',
        required=True
    )

    # class Meta:
    #     widgets = {'collection': forms.HiddenInput()}

    # def __init__(self, *args, **kwargs):
    #     super(MultipleNetCDFUploadForm, self).__init__(*args, **kwargs)

    def clean_netcdf_files(self):
        files = self.files.getlist('netcdf_files')

        if not files:
            raise forms.ValidationError("No files were selected.")

        return files


"""
    AMA discipline dictionary forms

    These collect the AMA content the NetCDF harvest cannot produce. Every attribute in these three
    classes is minOccurs="0" in PDS4_AMA_1O00_1300.xsd, so nothing here is required by the schema and
    nothing is marked required in the form either; blank values are omitted from the label entirely
    (an empty element would violate the schema's minLength="1"). The LDD defines no enumerations for
    any of these attributes, so the dropdown-looking fields are deliberately free text.
"""


def _ama_text_widget(placeholder=''):
    attrs = {'class': 'form-control form-control-sm'}
    if placeholder:
        attrs['placeholder'] = placeholder
    return forms.TextInput(attrs=attrs)


# Three AMA attributes are closed vocabularies. The constraint lives ONLY in the Schematron
# (PDS4_AMA_1O00_1300.sch); the XSD carries no xs:enumeration at all, so a free-text value passes
# XSD validation and fails later, during Schematron validation at submission. These used to be text
# inputs whose placeholders suggested values the Schematron rejects - "General Circulation Model"
# for a field that wants GCM, "lat/lon" for one that wants lat-lon, "sigma" for one that wants
# Sigma - so following the hint produced an invalid archive.
AMA_ENUMERATIONS = {
    'type': ('ASSIMILATION', 'GCM', 'MESOSCALE'),
    'horizontal_grid_type': ('cube-sphere', 'icosahedral', 'lat-lon'),
    'vertical_grid_type': ('Altitude', 'Hybrid-Sigma', 'Isentropic', 'Pressure', 'Sigma'),
}


def _ama_choice_field(field_name):
    """A closed list, offered as a select so an invalid value cannot be typed in the first place."""
    return forms.ChoiceField(
        required=False,
        choices=[('', 'Not specified')] + [(v, v) for v in AMA_ENUMERATIONS[field_name]],
        widget=forms.Select(attrs={'class': 'form-select form-select-sm'}))


def _ama_number_widget(placeholder=''):
    attrs = {'class': 'form-control form-control-sm', 'step': 'any'}
    if placeholder:
        attrs['placeholder'] = placeholder
    return forms.NumberInput(attrs=attrs)


class AMAFormGroupsMixin(object):
    """Lets a template render an AMA form in labelled sections instead of one long field wall.

    Simulation Configuration alone has 17 attributes; presented flat they are hard to scan. The
    grouping lives on the form rather than in the template so the bundle-page modal and the
    per-file page cannot drift apart.
    """

    FIELD_GROUPS = ()

    def groups(self):
        for title, field_names in self.FIELD_GROUPS:
            # Fields removed for this scope (apply_to_all on per-file forms) are skipped, and an
            # entirely empty group is dropped rather than rendering a bare heading.
            bound_fields = [self[name] for name in field_names if name in self.fields]
            if bound_fields:
                yield {'title': title, 'fields': bound_fields}


class AMAScopedFormMixin(AMAFormGroupsMixin):
    """Shared behaviour for all three AMA classes.

    `scope` is 'collection' for the collection-wide default row and 'file' for one file's override.

    Two scope controls, only ever one of them present, because the question genuinely differs by
    scope:

    * File scope gets `apply_scope`, a radio reading "same for every file in this collection" or
      "just this file". It replaced a checkbox that rendered unchecked on every load whatever the
      values actually were, so it never showed where they lived. Being a radio it always posts an
      explicit answer, which is what lets the view tell "leave this file following the default"
      apart from "give this file its own copy of values that happen to match the default" - see
      `_save_ama_section`.
    * Collection scope keeps `apply_to_collection`, unchanged in meaning: the row being edited is
      already the shared value, so the only extra question is whether to discard the per-file
      overrides as well.

    One control per section either way, so a user can hold Model Metadata at collection level while
    giving this file its own File Description.
    """

    APPLY_COLLECTION = 'collection'
    APPLY_FILE = 'file'

    APPLY_SCOPE_CHOICES = (
        (APPLY_COLLECTION, 'Same for every file in this collection'),
        (APPLY_FILE, 'Just this file'),
    )

    # Declared on each concrete form rather than here: Django's form metaclass only collects
    # Field attributes from the class being defined and from bases that already have
    # `declared_fields`, which a plain mixin does not.
    CONTROL_FIELDS = ('apply_scope', 'apply_to_collection')

    def __init__(self, *args, **kwargs):
        self.scope = kwargs.pop('scope', 'collection')
        # Whether this file currently holds its own values for this section. Only meaningful in
        # file scope, where it decides which radio starts selected.
        has_override = kwargs.pop('has_override', False)
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = False

        # A value stored while these were free-text boxes is kept and flagged rather than
        # quietly discarded: an unrecognised value would otherwise leave the select blank, and the
        # next save would erase what the user had without ever saying so.
        for enum_name in AMA_ENUMERATIONS:
            field = self.fields.get(enum_name)
            if field is None or not hasattr(field, 'choices'):
                continue
            current = self.initial.get(enum_name) or getattr(self.instance, enum_name, '')
            if current and current not in [value for value, _label in field.choices if value]:
                field.choices = list(field.choices) + [
                    (current, '{} (not a valid PDS4 value)'.format(current))]

        # Drop whichever control does not belong in this scope, so neither can be posted into a
        # scope that would not know what to do with it.
        if self.scope == 'file':
            self.fields.pop('apply_to_collection', None)
            radio = self.fields.get('apply_scope')
            if radio is not None:
                radio.help_text = (
                    'Files uploaded into this collection later inherit the collection value. '
                    '"Just this file" leaves every other file alone.')
                if not self.is_bound:
                    self.initial.setdefault(
                        'apply_scope',
                        self.APPLY_FILE if has_override else self.APPLY_COLLECTION)
        else:
            self.fields.pop('apply_scope', None)
            checkbox = self.fields.get('apply_to_collection')
            if checkbox is not None:
                checkbox.label = 'Reset every file in this collection to these values'
                checkbox.help_text = (
                    'Clears any per-file {} values so all files follow this '
                    'default.'.format(self.SECTION_LABEL))

    def clean(self):
        """Enforce the two text rules the PDS4 base types impose, which Django cannot see.

        Both ASCII_Short_String_Collapsed and ASCII_Text_Collapsed derive from xs:token and carry
        the pattern \\p{IsBasicLatin}*:

        * xs:token collapses whitespace, so a value stored with newlines or runs of spaces would
          be silently rewritten when the archive is read back. Collapsing here keeps what is
          stored identical to what is archived.
        * Basic Latin means ASCII only. An institution such as "Universite de Paris" is fine but
          "Université" is not, and the resulting label fails PDS4 validation. Users would
          otherwise only discover that at submission time, so it is rejected at entry with an
          explanation instead.
        """
        cleaned_data = super().clean()

        for name, value in list(cleaned_data.items()):
            if name in self.CONTROL_FIELDS or not isinstance(value, str):
                continue

            collapsed = ' '.join(value.split())
            cleaned_data[name] = collapsed
            if not collapsed:
                continue

            offenders = sorted({character for character in collapsed if ord(character) > 127})
            if offenders:
                self.add_error(name, forms.ValidationError(
                    'The PDS4 dictionary allows only basic Latin (ASCII) characters here. '
                    'Please replace: {}'.format(' '.join(offenders))))

        return cleaned_data

    def has_any_value(self):
        """True when the user actually supplied something worth storing."""
        if not self.is_valid():
            return False
        for name, value in self.cleaned_data.items():
            if name in self.CONTROL_FIELDS:
                continue
            if value is None:
                continue
            if isinstance(value, str) and value.strip() == '':
                continue
            return True
        return False

    def wants_apply_to_collection(self):
        """True when this save should reach the whole collection rather than one file.

        Both scopes answer the same question through their own control: the file form's radio set
        to 'collection', or the collection form's reset checkbox.
        """
        if not self.is_valid():
            return False
        if self.scope == 'file':
            return self.cleaned_data.get('apply_scope') == self.APPLY_COLLECTION
        return bool(self.cleaned_data.get('apply_to_collection'))

    def wants_file_scope(self):
        """True when the user explicitly asked for this file to hold its own values.

        Distinct from "did not tick anything": the radio always posts, so this is a positive
        instruction and `_save_ama_section` may store an override even where the values match the
        collection default.
        """
        if self.scope != 'file' or not self.is_valid():
            return False
        return self.cleaned_data.get('apply_scope') == self.APPLY_FILE


class ModelMetadataForm(AMAScopedFormMixin, forms.ModelForm):
    """ama:Model_Metadata.

    Usually identical for every file in a collection, so it is normally filled in once and pushed
    out with the apply-to-collection checkbox. The per-file override exists so a single mislabelled
    file can be corrected without disturbing the rest.
    """

    SECTION_LABEL = 'Model Metadata'

    apply_to_collection = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}))

    apply_scope = forms.ChoiceField(
        required=False,
        choices=AMAScopedFormMixin.APPLY_SCOPE_CHOICES,
        widget=forms.RadioSelect(attrs={'class': 'form-check-input'}))

    type = _ama_choice_field('type')

    FIELD_GROUPS = (
        ('', ('type', 'name', 'version', 'institution')),
    )

    class Meta(object):
        model = ModelMetadata
        exclude = ('bundle', 'collection', 'netcdf_file')
        labels = {
            'type': 'Type of Model',
            'name': 'Model Name',
            'version': 'Model Version',
            'institution': 'Institution',
        }
        widgets = {
            'name': _ama_text_widget('e.g. MarsWRF'),
            'version': _ama_text_widget('e.g. 3.2.1'),
            'institution': _ama_text_widget('e.g. NASA Ames Research Center'),
        }


class SimulationConfigurationForm(AMAScopedFormMixin, forms.ModelForm):
    """ama:Simulation_Configuration."""

    horizontal_grid_type = _ama_choice_field('horizontal_grid_type')
    vertical_grid_type = _ama_choice_field('vertical_grid_type')

    FIELD_GROUPS = (
        ('Grid and Resolution', ('horizontal_grid_type', 'model_resolution',
                                 'model_resolution_unit', 'vertical_grid_type',
                                 'vertical_grid_unit')),
        ('Timing', ('model_timestep', 'model_timestep_unit', 'start_time', 'end_time',
                    'time_unit')),
        ('Spatial Extent', ('upper_boundary', 'lower_boundary', 'northern_boundary',
                            'southern_boundary', 'eastern_boundary', 'western_boundary')),
        ('Description', ('description',)),
    )

    SECTION_LABEL = 'Simulation Configuration'

    apply_to_collection = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}))

    apply_scope = forms.ChoiceField(
        required=False,
        choices=AMAScopedFormMixin.APPLY_SCOPE_CHOICES,
        widget=forms.RadioSelect(attrs={'class': 'form-check-input'}))

    class Meta(object):
        model = SimulationConfiguration
        exclude = ('bundle', 'collection', 'netcdf_file')
        labels = {
            'horizontal_grid_type': 'Horizontal Grid Type',
            'model_resolution': 'Model Resolution',
            'model_resolution_unit': 'Model Resolution Unit',
            'vertical_grid_type': 'Vertical Grid Type',
            'vertical_grid_unit': 'Vertical Grid Unit',
            'model_timestep': 'Model Timestep',
            'model_timestep_unit': 'Model Timestep Unit',
            'upper_boundary': 'Upper Boundary',
            'lower_boundary': 'Lower Boundary',
            'northern_boundary': 'Northern Boundary (deg)',
            'southern_boundary': 'Southern Boundary (deg)',
            'eastern_boundary': 'Eastern Boundary (deg)',
            'western_boundary': 'Western Boundary (deg)',
            'start_time': 'Start Time',
            'end_time': 'End Time',
            'time_unit': 'Time Unit',
            'description': 'Description',
        }
        widgets = {
            'model_resolution': _ama_text_widget('e.g. 5x5'),
            'model_resolution_unit': _ama_text_widget('e.g. deg'),
            'vertical_grid_unit': _ama_text_widget('e.g. Pa'),
            'model_timestep': _ama_number_widget(),
            'model_timestep_unit': _ama_text_widget('e.g. s'),
            'upper_boundary': _ama_number_widget(),
            'lower_boundary': _ama_number_widget(),
            'northern_boundary': _ama_number_widget('-90 to 90'),
            'southern_boundary': _ama_number_widget('-90 to 90'),
            'eastern_boundary': _ama_number_widget('-180 to 360'),
            'western_boundary': _ama_number_widget('-180 to 360'),
            # start_time / end_time are ASCII_Short_String_Collapsed in the LDD, not dates, so they
            # stay free text - a date picker here would be wrong for model time (e.g. sol 120).
            'start_time': _ama_text_widget('free text, e.g. sol 0'),
            'end_time': _ama_text_widget('free text, e.g. sol 668'),
            'time_unit': _ama_text_widget('e.g. sols'),
            'description': forms.Textarea(attrs={'class': 'form-control form-control-sm', 'rows': 3}),
        }


class FileDescriptionForm(AMAScopedFormMixin, forms.ModelForm):
    """ama:Model_Output/ama:File_Description."""

    FIELD_GROUPS = (
        ('Vertical Extent', ('top_level', 'bottom_level', 'level_unit')),
        ('Time Coverage', ('start_time', 'end_time', 'time_unit')),
        ('Processing', ('postprocessing_methods',)),
    )

    SECTION_LABEL = 'File Description'

    apply_to_collection = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}))

    apply_scope = forms.ChoiceField(
        required=False,
        choices=AMAScopedFormMixin.APPLY_SCOPE_CHOICES,
        widget=forms.RadioSelect(attrs={'class': 'form-check-input'}))

    class Meta(object):
        model = FileDescription
        exclude = ('bundle', 'collection', 'netcdf_file')
        labels = {
            'top_level': 'Top Level',
            'bottom_level': 'Bottom Level',
            'level_unit': 'Level Unit',
            'start_time': 'Start Time',
            'end_time': 'End Time',
            'time_unit': 'Time Unit',
            'postprocessing_methods': 'Postprocessing Methods',
        }
        widgets = {
            'top_level': _ama_number_widget(),
            'bottom_level': _ama_number_widget(),
            'level_unit': _ama_text_widget('e.g. Pa'),
            'start_time': _ama_text_widget('free text, e.g. sol 0'),
            'end_time': _ama_text_widget('free text, e.g. sol 668'),
            'time_unit': _ama_text_widget('e.g. sols'),
            'postprocessing_methods': _ama_text_widget("e.g. time:mean(interval=3 hours)"),
        }