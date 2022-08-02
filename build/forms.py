from builtins import object
from django import forms
from django.contrib.auth.models import User
from .chocolate import replace_all
#from django.forms import modelformset_factory

from lxml import etree
import urllib.request, urllib.error, urllib.parse, urllib.request, urllib.parse, urllib.error
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

#Hello Tommy How do I change the port 
"""
    Confirm
"""
class ConfirmForm(forms.Form):
    CHOICES = [('Yes','Yes') , ('No','No')]
    decision = forms.ChoiceField(choices=CHOICES, widget=forms.RadioSelect())


"""
    Alias
"""
class AliasForm(forms.ModelForm):
    alternate_id = forms.CharField(required=True)
    alternate_title = forms.CharField(required=True)
    comment = forms.CharField(required=False)

    class Meta(object):
        model = Alias
        exclude = ('bundle',)


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
class BundleForm(forms.ModelForm):
    name = forms.CharField( initial='Enter name here', required=True, max_length=50)

    class Meta(object):
        model = Bundle              
        fields = ('name', 'bundle_type', 'version', )


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
    def clean(self):                                     # name_edit can be removed from this function
        cleaned_data = self.cleaned_data
        name = cleaned_data.get('name')

        # - The name of the bundle should be less than or equal to 255 characters

        if len(name) <= 255:
            name_edit = name
            name_edit = name_edit.lower()
            name_edit = replace_all(name_edit, ' ', '_') # replace spaces with underscores
            if name_edit.endswith("bundle"):
                name_edit = name_edit[:-7] # seven because there is probably an underscore by now
            if name_edit.find(':') != -1:
                raise forms.ValidationError("The colon (:) is used to delimit segments of a urn and thus is not permitted within a bundle name.")
        else:
            raise forms.ValidationError("The length of your bundle name is too large");














"""
    Citation_Information
"""
class CitationInformationForm(forms.ModelForm):

    description = forms.CharField(required=True)
    #publication_year = forms.DateField(required=True, input_formats=['%m/%d/%Y','%d/%m/%Y','%Y-%m-%d','%m-%d-%Y','%d-%m-%Y'])
    publication_year = forms.CharField(required=True) #validators=[RegexValidator(r'^\d{1,10}$')])
    author_list = forms.CharField(required=False)
    editor_list = forms.CharField(required=False)
    keyword = forms.CharField(required=False)

    class Meta(object):
        model = Citation_Information
        exclude = ('bundle',)


    """
        clean should do nothing to the description.  For publication_year, CitationInformationForm uses Django's DateField form field.  Django's DateField form field (https://docs.djangoproject.com/en/2.0/_modules/django/forms/fields/#DateField) simply sees if the input could be converted to a date time object.  Therefore, values like 6020 can be input.  We need to decide if we want to prevent user errors such as this, raise warnings to the user, do nothing, etc...
    """
    def clean(self):
        pass


"""
Test
"""


"""
    Modification History 
"""
class ModificationHistoryForm(forms.ModelForm):

    description = forms.CharField(required=True)
    modification_date = forms.CharField(required=True) #validators=[RegexValidator(r'^\d{1,10}$')])
    version_id = forms.CharField(required=False)


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
    has_document = forms.BooleanField(required=True, initial=True)
    has_context = forms.BooleanField(required=True, initial=True)
   # has_xml_schema = forms.BooleanField(required=True, initial=True)
    has_data = forms.BooleanField(required=False, initial=False)   
    #has_raw_data = forms.BooleanField(required=False, initial=False)
    #has_calibrated_data = forms.BooleanField(required=False, initial=False)
    #has_derived_data = forms.BooleanField(required=False, initial=False)
    #data_enum = forms.IntegerField(required=False, min_value = 0, max_value=25)

    class Meta(object):
        model = Collections
        exclude = ('bundle',)


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
    class Meta(object):
        model = Data
        exclude = ('bundle',)
        #exclude = ('bundle','data_enum',)





"""
    Facility
"""
class FacilityForm(forms.Form):
    facility = forms.ModelChoiceField(queryset=Facility.objects.all(), required=True)



"""
    Telescope
"""
class TelescopeForm(forms.Form):
    telescope = forms.ModelChoiceField(queryset=Telescope.objects.all(), required=True)




"""
    Facility Instrument
    
    HERE : https://medium.com/@MicroPyramid/understanding-djangos-model-fromsets-in-detail-and-their-advanced-usage-131dfe66853d
"""
class FacilityInstrumentForm(forms.Form):

    instrument = forms.ModelChoiceField(queryset = Instrument.objects.all(), required=True)

    def __init__(self, *args, **kwargs):
        self.pk_fac = kwargs.pop('pk_fac')
        super(FacilityInstrumentForm,self).__init__(*args, **kwargs)
        self.fields['instrument'] = forms.ModelChoiceField(queryset=Instrument.objects.filter(facility=self.pk_fac), required=True)





"""
    Investigation
"""
class InvestigationForm(forms.Form):
    investigation = forms.ModelChoiceField(queryset=Investigation.objects.all(), required=True, help_text="Note: Investigations contain: individual investigations, missions, observing campaigns, or other investigations</br>")


"""
    Instrument Host
    
    HERE : https://medium.com/@MicroPyramid/understanding-djangos-model-fromsets-in-detail-and-their-advanced-usage-131dfe66853d
"""
class InstrumentHostForm(forms.Form):

    instrument_host = forms.ModelChoiceField(queryset = Instrument_Host.objects.all(), required=True)

    def __init__(self, *args, **kwargs):
        self.pk_inv = kwargs.pop('pk_inv')
        super(InstrumentHostForm,self).__init__(*args, **kwargs)
        self.fields['instrument_host'] = forms.ModelChoiceField(queryset=Instrument_Host.objects.filter(investigations=self.pk_inv), required=True)


"""
    Target
    
    HERE : https://medium.com/@MicroPyramid/understanding-djangos-model-fromsets-in-detail-and-their-advanced-usage-131dfe66853d
"""
class TargetForm(forms.Form):

    target = forms.ModelChoiceField(queryset = Target.objects.all(), required=True)

    def __init__(self, *args, **kwargs):
        self.pk_ins = kwargs.pop('pk_ins')
        super(TargetForm,self).__init__(*args, **kwargs)
        self.fields['target'] = forms.ModelChoiceField(queryset=Target.objects.filter(instrument_host=self.pk_ins), required=True)



"""
    Instrument
    
    HERE : https://medium.com/@MicroPyramid/understanding-djangos-model-fromsets-in-detail-and-their-advanced-usage-131dfe66853d
"""
class InstrumentForm(forms.Form):

    instrument = forms.ModelChoiceField(queryset = Instrument.objects.all(), required=True)

    def __init__(self, *args, **kwargs):
        self.pk_ins = kwargs.pop('pk_ins')
        super(InstrumentForm,self).__init__(*args, **kwargs)
        self.fields['instrument'] = forms.ModelChoiceField(queryset=Instrument.objects.filter(instrument_host=self.pk_ins), required=True)



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
class ProductDocumentForm(forms.ModelForm):
    document_name = forms.CharField(required=True)
    publication_date = forms.CharField(required=True)
    acknowledgement_text = forms.CharField(required=False)
    author_list = forms.CharField(required=False)
    copyright = forms.CharField(required=False)
    description = forms.CharField(required=False)
    document_editions = forms.CharField(required=False)
    doi = forms.CharField(required=False)
    editor_list = forms.CharField(required=False)
    revision_id = forms.CharField(required=False)

    class Meta(object):
        model = Product_Document
        exclude = ('bundle',)













"""
    ProductObservational
"""
class ProductObservationalForm(forms.ModelForm):
    OBSERVATIONAL_TYPES = [

        ('Array','Array'),
        ('Table','Table'),
        #('Table Binary','Table Binary'),
        #('Table Character','Table Character'),
        #('Table Delimited','Table Delimited'),
    ]
    PURPOSE_TYPES = [
        ('Calibration','Calibration'),
        ('Checkout','Checkout'),
        ('Engineering','Engineering'),
        ('Navigation','Navigation'),
        ('Observation Geometry','Observation Geometry'),
        ('Science','Science'),
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
    class Meta(object):
        model = Table_Delimited
        exclude = ('bundle','name',)



class Table_Binary_Form(forms.ModelForm):
    class Meta(object):
        model = Table_Binary
        exclude = ('bundle','name',)        



class Table_Fixed_Width_Form(forms.ModelForm):
    class Meta(object):
        model = Table_Fixed_Width
        exclude = ('bundle','name',)



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
        exclude = ('product_observational', 'observational_type', 'local_identifier')



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
        ('microseconds','microseconds'),
        ('ms','milliseconds'),
        ('s','seconds'),
        ('min','minute'),
        ('hr','hour'),
        ('day','day'),
        ('julian day','julian day'),
        ('yr','year'),
    ]
    loop_delay_unit = forms.RadioSelect(choices=LOOP_DELAY_UNIT_CHOICES)

    class Meta(object):
        model = Movie_Display_Settings
        exclude = ('display_dictionary',)



#class DisplayDictionaryForm(forms.ModelForm):
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
    CHOICES = [('Display','Display') , ('testing','testing'),]
    dictionary_type = forms.MultipleChoiceField(choices=CHOICES, widget=forms.CheckboxSelectMultiple())








