from builtins import object
from django import forms
from django.conf import settings
from django.contrib.auth.models import User
from .chocolate import replace_all
from .chocolate import validate_path_component
from django.utils.safestring import mark_safe

#from django.forms import modelformset_factory

from lxml import etree
import json
import re
import unicodedata
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

# PDS4 caps a logical identifier at 255 characters. The bundle name becomes the bundle_id
# in every LID, with the collection and product added after it:
# 
# urn:<agency>:<bundle_id>:<collection>:<product>
MAX_LID_LENGTH = 255
# Longest prefix: External uses 'urn:nasa:pds-ama:' (17), Archive uses 'urn:' + agency + ':'.
LONGEST_LID_PREFIX = len('urn:nasa:pds-ama:')
# Left over for ':<collection>:<product>' on the deepest products.
LID_SEGMENT_RESERVE = 64
# Ceiling the name and bundleID fields are allowed to reach.
MAX_BUNDLE_NAME = 150


class BundleForm(forms.ModelForm):
    # The name becomes a folder, so it has to be one path segment.
    name = forms.CharField(required=True, max_length=MAX_BUNDLE_NAME,
                           validators=[validate_path_component], widget = forms.TextInput(attrs={
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

        # max_length and required already cover these, so nothing left to check.
        if not name:
            return cleaned_data

        name_edit = replace_all(name.lower(), ' ', '_')

        # Check for the colon before trimming, since bundleID comes from the raw name.
        if name_edit.find(':') != -1:
            raise forms.ValidationError(
                "The colon (:) is used to delimit segments of a urn and thus is not permitted within a bundle name.")

        if name_edit.endswith("bundle"):
            # seven because there is probably an underscore by now
            name_edit = name_edit[:-7]

        # Check the whole identifier, not just the name.
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


_ROR_ID = re.compile(r'^(?:(?:https?://)?(?:www\.)?ror\.org/)?(0[a-hj-km-np-tv-z0-9]{6}[0-9]{2})/?$', re.I)


def clean_ror_id(value):
    """Returns the ROR ID as a full URI, or '' if blank.

    PDS4 has no pattern for organization_rorid so validate won't catch a bad one. Check it here.
    """
    value = (value or '').strip()
    if not value:
        return ''
    match = _ROR_ID.match(value)
    if match is None:
        raise forms.ValidationError(
            'This does not look like a ROR ID, which reads like https://ror.org/027k65916. '
            'Look the organization up at ror.org/search, or leave this blank.')
    return 'https://ror.org/{}'.format(match.group(1).lower())


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
        # The bundle, if the caller knows it. Used to decide if an author is required.
        self.bundle = kwargs.pop('bundle', None)
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
        # Treat blank as 0 since the model columns are NOT NULL.
        for f in ('number_of_editors_people', 'number_of_editors_organization'):
            if cleaned_data.get(f) is None:
                cleaned_data[f] = 0

        # External bundles need an author (build.preflight). Check here since the edit form can't
        # change how many there are.
        if self.bundle is not None and self.bundle.bundle_type == 'External':
            people = cleaned_data.get('number_of_authors_people') or 0
            organizations = cleaned_data.get('number_of_authors_organization') or 0
            if people + organizations < 1:
                raise forms.ValidationError(
                    'Add at least one author, a person or an organization. A citation '
                    'needs someone to credit, and the number of authors cannot be '
                    'changed once the citation has been created.')
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

        # Fields for user-added editors. The two fixed ATM editors aren't editable.
        self._add_person_fields('editor', self.citation_information.number_of_editors_people)
        self._add_organization_fields('editor', self.citation_information.number_of_editors_organization)

    def clean(self):
        """Catch names PDS can't store. PDS4 names are Basic Latin only, so accents are refused."""
        cleaned = super(EditCitationInformationForm, self).clean()

        for name, value in list(cleaned.items()):
            if not isinstance(value, str) or not value:
                continue
            if name.endswith('_rorid'):
                try:
                    cleaned[name] = clean_ror_id(value)
                except forms.ValidationError as error:
                    self.add_error(name, error)
                continue
            if name.endswith('_given_name'):
                label = 'given name'
            elif name.endswith('_family_name'):
                label = 'family name'
            else:
                continue
            try:
                clean_pds_person_name(value, label)
            except forms.ValidationError as error:
                self.add_error(name, error)

        return cleaned

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
    collection_name = forms.CharField(required=True, validators=[validate_path_component], max_length=100, widget=forms.TextInput(attrs={
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
    NIL_REASON_CHOICES = [
        ('', 'Select a reason'),
        ('inapplicable', 'Inapplicable'),
        ('anticipated', 'Anticipated'),
        ('missing', 'Missing'),
        ('unknown', 'Unknown'),
    ]

    name = forms.CharField(
        required=True,
        validators=[validate_path_component],
    )
    start_date_time = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(
            format='%Y-%m-%dT%H:%M',
            attrs={'type': 'datetime-local', 'class': 'form-control'}
        ),
    )
    stop_date_time = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(
            format='%Y-%m-%dT%H:%M',
            attrs={'type': 'datetime-local', 'class': 'form-control'}
        ),
    )
    start_date_time_nil_reason = forms.ChoiceField(
        required=False,
        choices=NIL_REASON_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    stop_date_time_nil_reason = forms.ChoiceField(
        required=False,
        choices=NIL_REASON_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    start_date_time_is_nil = forms.BooleanField(required=False)
    stop_date_time_is_nil = forms.BooleanField(required=False)

    class Meta:
        model = Data
        exclude = ('bundle',)
        fields = ['name', 'processing_level', 'data_type', 'header', 'collection']

    def __init__(self, *args, **kwargs):
        self.pk_bun = kwargs.pop('pk_bun')
        super().__init__(*args, **kwargs)

        self.fields['collection'] = forms.ModelChoiceField(
            queryset=AdditionalCollections.objects.filter(bundle=self.pk_bun),
            required=True,
            widget=forms.HiddenInput,
        )

        if self.is_bound:
            self.data = self.data.copy()

            for time_field in ('start_date_time', 'stop_date_time'):
                checkbox_name = f'{time_field}_is_nil'
                checkbox = self.fields[checkbox_name]
                is_nil = checkbox.widget.value_from_datadict(
                    self.data, self.files, self.add_prefix(checkbox_name)
                )

                inactive_field = time_field if is_nil else f'{time_field}_nil_reason'
                self.data.pop(self.add_prefix(inactive_field), None)

    def clean(self):
        cleaned_data = super().clean()

        for time_field in ('start_date_time', 'stop_date_time'):
            if cleaned_data.get(f'{time_field}_is_nil'):
                required_field = f'{time_field}_nil_reason'
                message = 'Select a reason for leaving this time blank.'
            else:
                required_field = time_field
                message = 'Enter a date and time or check the box below it.'

            if required_field not in self.errors and not cleaned_data.get(required_field):
                self.add_error(required_field, message)

        start = cleaned_data.get('start_date_time')
        stop = cleaned_data.get('stop_date_time')
        if start is not None and stop is not None and stop < start:
            self.add_error('stop_date_time', 'Stop time must be at or after start time.')

        return cleaned_data

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
            # Grouped, not filtered: related targets first, everything else below. PDS4 doesn't tie a
            # target to an investigation.
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
    """Best label for an investigation. Some have a blank name, so fall back to the LID's last segment."""
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
    """Group the target list by investigation, then list everything else.

    One optgroup per investigation, so a target can show under several. It's only for
    navigation, every target stays selectable.
    """

    def __iter__(self):
        field = self.field

        if field.empty_label is not None:
            yield ('', field.empty_label)

        objects = list(self.queryset)
        grouped = set()

        for label, pks in field.related_groups:
            if not pks:
                # No heading for an investigation with no published targets.
                continue
            members = [self.choice(o) for o in objects if o.pk in pks]
            if members:
                yield (label, members)
                grouped |= pks

        others = [self.choice(o) for o in objects if o.pk not in grouped]

        if grouped:
            yield ('All other targets', others)
        else:
            # Nothing published for these investigations, so use a flat list.
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

            # One group per investigation.
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
        """Target pks PDS lists for this bundle's investigations, for the template's caution."""
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


def clean_unique_document_name(form, value):
    """Refuse a document name already used in this bundle.

    The name becomes the product identifier, and an inventory can't list one twice.
    """
    value = (value or '').strip()
    bundle = getattr(form, 'bundle', None)
    if not value or bundle is None:
        return value

    clash = Product_Document.objects.filter(bundle=bundle, document_name=value)

    # Don't clash with itself. The edit views use initial= so `editing` says which document this is.
    editing = getattr(form, 'editing', None)
    exclude_pk = getattr(editing, 'pk', None) or (
        form.instance.pk if form.instance is not None else None)
    if exclude_pk:
        clash = clash.exclude(pk=exclude_pk)
    if clash.exists():
        raise forms.ValidationError(
            'This bundle already has a document called "%(value)s". PDS identifies '
            'a document by its name, and a collection cannot list the same one '
            'twice, so give this one a different name.',
            params={'value': value})
    return value


# PDS4 names are Basic Latin only, so say so and offer a spelling that works.
PDS_BASIC_LATIN = re.compile(r'^[\x00-\x7F]*$')


def ascii_suggestion(value):
    """The nearest unaccented spelling: Morales-Juberias for Morales-Juberias."""
    decomposed = unicodedata.normalize('NFKD', value or '')
    stripped = ''.join(c for c in decomposed if not unicodedata.combining(c))
    return ''.join(c for c in stripped if ord(c) < 128)


def clean_pds_person_name(value, label):
    """Reject a name PDS cannot store, and say what to write instead."""
    value = (value or '').strip()
    if not value or PDS_BASIC_LATIN.match(value):
        return value

    suggestion = ascii_suggestion(value)
    if suggestion and suggestion != value:
        raise forms.ValidationError(
            'PDS can only store unaccented letters in a %(label)s, so '
            '"%(value)s" cannot be recorded as written. Try "%(suggestion)s". '
            'This is a limit of the archive format, not a judgement about the name.',
            params={'label': label, 'value': value, 'suggestion': suggestion})

    raise forms.ValidationError(
        'PDS can only store unaccented Latin letters in a %(label)s, and '
        '"%(value)s" uses characters outside that set.',
        params={'label': label, 'value': value})


def document_file_field():
    """The document's file. Declared on each form since Django ignores fields on a plain mixin."""
    return forms.FileField(
        required=False,
        label='Document File',
        label_suffix='',
        help_text=('A PDF/A-1 (.pdf) or plain text (.txt) file. Its name becomes the '
                   'file name in the label.'),
        widget=forms.ClearableFileInput(attrs={
            'class': 'form-control',
            'accept': '.pdf,.txt,application/pdf,text/plain',
            # For the in-browser check in _document_upload_preview.html.
            'data-max-mb': getattr(settings, 'DOCUMENT_MAX_UPLOAD_MB', 100),
        }))


def clean_document_upload(form):
    """Check the uploaded file for either document form.

    A new document needs a file. An edit can keep the old one or replace it.
    The checked upload goes on form.prepared_file.
    """
    from build import document_files

    form.prepared_file = None
    uploaded = form.cleaned_data.get('document_file')
    if not uploaded:
        if form.editing is None:
            raise forms.ValidationError(
                'Attach the document itself: a PDF/A-1 (.pdf) or a plain text (.txt) file.')
        return None

    prepared = document_files.prepare(uploaded)
    if form.bundle is not None and document_files.name_taken(
            form.bundle, prepared.file_name, excluding=form.editing):
        raise forms.ValidationError(
            'Another document in this bundle already uses the file name "{}". Rename '
            'the file and upload it again.'.format(prepared.file_name))
    form.prepared_file = prepared
    return uploaded


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

    # The file itself. Its name and format come from the upload.
    document_file = document_file_field()

    class Meta:
        model = Product_Document
        fields = [
            "document_name",
            "document_id",
            "comment",
        ]

    def __init__(self, *args, **kwargs):
        # Needed to check if a document name is taken. Optional.
        self.bundle = kwargs.pop('bundle', None)
        # The Product_Document being edited, for the duplicate name check.
        self.editing = kwargs.pop('editing', None)
        super(AnnexProductDocumentForm, self).__init__(*args, **kwargs)
        self.prepared_file = None

    def clean_document_file(self):
        return clean_document_upload(self)

    def clean_document_name(self):
        return clean_unique_document_name(self, self.cleaned_data.get('document_name'))



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
    # doi = forms.CharField(
    #     required=False,
    #     max_length=100,
    #     label_suffix = '',
    #     widget=forms.TextInput(attrs={
    #         'class': 'form-control',
    #     })
    # )
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
    # The file itself. Name, format and file count come from the upload.
    document_file = document_file_field()
    local_id = forms.CharField(
        required=False,
        max_length=100,
        label_suffix = '',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            #'placeholder': 'Local ID'
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
            # "doi",
            "revision_id",
            "document_editions",
            "edition_name",
            "language",
            "local_id",
        ]

    def __init__(self, *args, **kwargs):
        self.bundle = kwargs.pop('bundle', None)
        # The Product_Document being edited, for the duplicate name check.
        self.editing = kwargs.pop('editing', None)
        super(ProductDocumentForm, self).__init__(*args, **kwargs)
        self.prepared_file = None

    def clean_document_file(self):
        return clean_document_upload(self)

    def clean_document_editions(self):
        # Optional on the form but NOT NULL in the table. ELSA writes one Document_Edition.
        value = self.cleaned_data.get('document_editions')
        return 1 if value is None else value

    def clean_document_name(self):
        return clean_unique_document_name(self, self.cleaned_data.get('document_name'))


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
        # exclude = ('bundle',)
        exclude = ('bundle', 'collection')

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
        # self.fields['collection'] = forms.ModelChoiceField(queryset=AdditionalCollections.objects.filter(bundle=self.pk_bun), required = True)

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

        # self.fields['collection'].widget.attrs.update({
        #     'class': 'form-control form-outline'
        # })   
class Table_Binary_Form(forms.ModelForm):

    # data = forms.ModelChoiceField(queryset=Data.objects.all(), required=False)

    class Meta(object):
        model = Table_Binary
        # exclude = ('bundle',)
        exclude = ('bundle', 'collection')
    
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
        # self.fields['collection'] = forms.ModelChoiceField(queryset=AdditionalCollections.objects.filter(bundle=self.pk_bun), required = True)

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

        # self.fields['collection'].widget.attrs.update({
        #     'class': 'form-control form-outline'
        # })       

class Table_Fixed_Width_Form(forms.ModelForm):

    # data = forms.ModelChoiceField(queryset=Data.objects.all(), required=False)

    class Meta(object):
        model = Table_Fixed_Width
        # exclude = ('bundle',)
        exclude = ('bundle', 'collection')
    
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
        # self.fields['collection'] = forms.ModelChoiceField(queryset=AdditionalCollections.objects.filter(bundle=self.pk_bun), required = True)

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

        # self.fields['collection'].widget.attrs.update({
        #     'class': 'form-control form-outline'
        # })   

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
        text = {'class': 'form-control form-outline'}
        table_type = self.data_instance.data_type
        
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


# Multiple files in one input. Django 4.2.1+ blocks 'multiple' on ClearableFileInput
# (CVE-2023-31047), so this follows the Django docs and cleans every file.
class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('widget', MultipleFileInput())
        super(MultipleFileField, self).__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single = super(MultipleFileField, self).clean
        if isinstance(data, (list, tuple)):
            if not data:
                return single(None, initial)
            return [single(item, initial) for item in data]
        return single(data, initial)


# To handle multiple NetCDF files
class MultipleNetCDFUploadForm(forms.Form):
    collection = forms.CharField(widget=forms.HiddenInput)
    netcdf_files = MultipleFileField(
        widget=MultipleFileInput(attrs={
            # Set explicitly so the picker still allows multiple files on Django 4.2.0.
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


# Three AMA attributes are closed lists. The limit is only in the Schematron
# (PDS4_AMA_1O00_1300.sch), so free text would pass the XSD and fail at submission.
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
    """Lets a template show an AMA form in sections. Grouping is here so both pages match."""

    FIELD_GROUPS = ()

    def groups(self):
        for title, field_names in self.FIELD_GROUPS:
            # Skip fields removed for this scope, and drop empty groups.
            bound_fields = [self[name] for name in field_names if name in self.fields]
            if bound_fields:
                yield {'title': title, 'fields': bound_fields}


class AMAScopedFormMixin(AMAFormGroupsMixin):
    """Shared behaviour for the three AMA classes.

    `scope` is 'collection' for the default row and 'file' for one file's override.

    * File scope has `apply_scope`, a radio for every file or just this file (see `_save_ama_section`).
    * Collection scope has `apply_to_collection`, which also discards per-file overrides.
    """

    APPLY_COLLECTION = 'collection'
    APPLY_FILE = 'file'

    APPLY_SCOPE_CHOICES = (
        (APPLY_COLLECTION, 'Same for every file in this collection'),
        (APPLY_FILE, 'Just this file'),
    )

    # Declared on each form since Django doesn't collect fields from a plain mixin.
    CONTROL_FIELDS = ('apply_scope', 'apply_to_collection')

    def __init__(self, *args, **kwargs):
        self.scope = kwargs.pop('scope', 'collection')
        # Whether this file has its own values for this section. Sets the starting radio.
        has_override = kwargs.pop('has_override', False)
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = False

        # Keep and flag an old free-text value, otherwise the next save would erase it.
        for enum_name in AMA_ENUMERATIONS:
            field = self.fields.get(enum_name)
            if field is None or not hasattr(field, 'choices'):
                continue
            current = self.initial.get(enum_name) or getattr(self.instance, enum_name, '')
            if current and current not in [value for value, _label in field.choices if value]:
                field.choices = list(field.choices) + [
                    (current, '{} (not a valid PDS4 value)'.format(current))]

        # Drop the control that doesn't belong in this scope.
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
        """Enforce the PDS4 text rules Django can't see.

        These types come from xs:token and have the pattern \p{IsBasicLatin}*, so collapse whitespace
        and refuse anything that isn't ASCII.
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
        """True if this save should apply to the whole collection."""
        if not self.is_valid():
            return False
        if self.scope == 'file':
            return self.cleaned_data.get('apply_scope') == self.APPLY_COLLECTION
        return bool(self.cleaned_data.get('apply_to_collection'))

    def wants_file_scope(self):
        """True if the user asked for this file to have its own values."""
        if self.scope != 'file' or not self.is_valid():
            return False
        return self.cleaned_data.get('apply_scope') == self.APPLY_FILE


class ModelMetadataForm(AMAScopedFormMixin, forms.ModelForm):
    """ama:Model_Metadata. Usually the same for every file in a collection."""

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
            # Free text in the LDD, not dates. Model time can be something like sol 120.
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