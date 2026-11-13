# Stdlib imports
# -*- coding: UTF-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

from builtins import str
from builtins import object
from enum import unique
from queue import Empty
from enum import unique
from queue import Empty
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone
from django.conf import settings
from django.contrib.auth.models import User
from django.core.validators import MaxValueValidator, MinValueValidator, FileExtensionValidator
from django.http import HttpResponseRedirect
from django.shortcuts import render, redirect
from django.urls import reverse
# from django.utils.encoding import python_2_unicode_compatible
from django.utils.encoding import *
from shutil import copyfile
from .chocolate import *
# from context.models import *
from shutil import *
import datetime
import re
import logging
import shutil
import hashlib
import os
import uuid
import copy



from build.label_migrations import head as label_migrations_head
from build.checker import checker_version as current_checker_version

#    Final Variables ------------------------------------------------------------------------------------
MAX_CHAR_FIELD = 255
MAX_LID_FIELD = 255
MAX_TEXT_FIELD = 1000

PDS4_LABEL_TEMPLATE_DIRECTORY = os.path.join(
    settings.TEMPLATE_DIR, 'pds4_labels')
NAMESPACE = "{http://pds.nasa.gov/pds4/pds/v1}"


#       --- These will be changed when Version model object gets complete ---
MODEL_LOCATION = "http://pds.nasa.gov/pds4/schema/released/pds/v1/PDS4_PDS_1800.sch"
NAMESPACE = "{http://pds.nasa.gov/pds4/pds/v1}"
SCHEMA_INSTANCE = "http://www.w3.org/2001/XMLSchema-instance"
SCHEMA_LOCATION = "http://pds.nasa.gov/pds4/pds/v1 http://pds.nasa.gov/pds4/schema/released/pds/v1/PDS4_PDS_1800.xsd"


#    Helpful functions here ----------------------------------------------------------------------------
"""
"""


def get_most_current_version():
    # Get all versions listed in ELSA
    Version_List = Version.objects.all()

    if Version_List:
        # Find the highest version number

        highest_number = 0

        for version in Version_List:
            if version.num > highest_number:
                highest_number = version.num
                highest_version = version

            # Now that we have iterated through the list, the highest number should be obtained
            return highest_version
    else:
        return 0


"""
"""



# PDS4 Target_Identification/type is a closed list. The crawler stores what the registry
# returned, usually upper case, so map it to the spelling PDS accepts.
PDS_TARGET_TYPES = (
    'Asteroid', 'Astrophysical', 'Calibration', 'Calibration Field', 'Calibrator',
    'Centaur', 'Comet', 'Dust', 'Dwarf Planet', 'Equipment', 'Exoplanet System',
    'Galaxy', 'Globular Cluster', 'Laboratory Analog', 'Lunar Sample',
    'Magnetic Field', 'Meteorite', 'Meteoroid', 'Meteoroid Stream', 'Nebula',
    'Open Cluster', 'Planet', 'Planetary Nebula', 'Planetary System',
    'Plasma Cloud', 'Plasma Stream', 'Ring', 'Sample', 'Satellite', 'Sky', 'Star',
    'Star Cluster', 'Synthetic Sample', 'Terrestrial Sample',
    'Trans-Neptunian Object',
)

_PDS_TARGET_TYPE_BY_KEY = {value.lower(): value for value in PDS_TARGET_TYPES}

# The reference_type depends on what's doing the referring.
TARGET_REFERENCE_TYPES = {
    'Product_Bundle': 'bundle_to_target',
    'Product_Collection': 'collection_to_target',
    'Product_Observational': 'data_to_target',
    'Product_Document': 'document_to_target',
    # Every AMA data product is a Product_External, so it needs its own entry.
    'Product_External': 'external_to_target',
}


# Context_Area is an xs:sequence, so children have a fixed order and can't just be appended.
CONTEXT_AREA_ORDER = (
    'comment', 'Time_Coordinates', 'Primary_Result_Summary', 'Investigation_Area',
    'Observing_System', 'Target_Identification', 'Mission_Area', 'Discipline_Area',
)


def insert_in_context_area(Context_Area, element):
    """Put element into Context_Area where the PDS4 sequence says it goes."""
    try:
        position = CONTEXT_AREA_ORDER.index(etree.QName(element).localname)
    except ValueError:
        Context_Area.append(element)
        return element

    for existing in Context_Area:
        name = etree.QName(existing).localname
        if name in CONTEXT_AREA_ORDER and CONTEXT_AREA_ORDER.index(name) > position:
            existing.addprevious(element)
            return element

    Context_Area.append(element)
    return element


# PDS4 only accepts 'English' for Document_Edition/language, so normalize what the user typed.
# Anything that clearly isn't English is passed through so PDS reports it.
def _require_text(parent, tag, value, fallback):
    """Give a required element a value, creating it if the template doesn't have one."""
    if parent is None:
        return None
    element = parent.find('{}{}'.format(NAMESPACE, tag))
    if element is None:
        element = etree.SubElement(parent, '{}{}'.format(NAMESPACE, tag))
    text = (value or '').strip() if isinstance(value, str) else value
    element.text = text or fallback
    return element


PDS_DOCUMENT_LANGUAGE = 'English'

_LANGUAGE_ALIASES = ('english', 'en', 'eng', 'en-us', 'en_us')


def pds_document_language(value):
    """The spelling PDS accepts for a document's language."""
    cleaned = (value or '').strip()
    if not cleaned or cleaned.lower() in _LANGUAGE_ALIASES:
        return PDS_DOCUMENT_LANGUAGE
    return cleaned


def pds_context_title(name, type_of):
    """The title PDS publishes for a context product: "<name> <type>".

    validate warns if Investigation_Area/name doesn't match. Won't add the type twice.
    """
    name = (name or '').strip()
    type_of = (type_of or '').strip()
    if not name or not type_of:
        return name
    if name.lower().endswith(type_of.lower()):
        return name
    return '{} {}'.format(name, type_of)


def pds_target_type(value):
    """PDS spelling of a stored target type. Falls back to title case if it's not in the list."""
    if not value:
        return value
    cleaned = value.strip()
    return _PDS_TARGET_TYPE_BY_KEY.get(cleaned.lower(), cleaned.title())


# PDS4 only allows lowercase letters, digits, hyphen, dot and underscore in a LID segment:
# urn(:[\p{Ll}\p{Nd}\-._]+){3,5}
_LID_ALLOWED = re.compile(r'[^a-z0-9._-]')


def pds_lid_segment(value):
    """Clean up one segment of a logical identifier so it matches the PDS4 pattern.

    File names can be mixed case but LIDs can't. Bad characters become an underscore instead
    of being dropped, so two names don't end up the same. Use on segments, not a whole LID,
    since colons aren't allowed.
    """
    if not value:
        return value
    return _LID_ALLOWED.sub('_', value.strip().lower())


# Acronyms to keep in caps in titles, since str.title() would turn NASA into Nasa.
_TITLE_ACRONYMS = {
    word.lower(): word for word in (
        'NASA', 'ESA', 'JAXA', 'ISRO', 'NOAA', 'NCAR', 'GFDL', 'JPL', 'GSFC', 'SwRI',
        'PDS', 'PDS4', 'AMA', 'ELSA', 'NMSU', 'GCM', 'MGCM', 'LES', 'WRF', 'MTWRF',
        'EPIC', 'FV3', 'TOI', 'UV', 'IR')
}


def pds_title_case(name):
    """Title-case a name but keep acronyms and anything the user already capitalized."""
    words = []
    for word in (name or '').split():
        known = _TITLE_ACRONYMS.get(word.lower())
        if known:
            words.append(known)
        elif any(character.isupper() for character in word[1:]):
            words.append(word)
        else:
            words.append(word.title())
    return ' '.join(words)


# PDS4 titles max out at 255 characters. Past that, drop the collection name.
_TITLE_MAX_LENGTH = 255


def collection_title(bundle_title, collection_name):
    """Collection title is the bundle title plus the collection name."""
    name = pds_title_case((collection_name or '').replace('_', ' '))
    title = '{} {} Collection'.format(bundle_title, name) if name else bundle_title
    return title if len(title) <= _TITLE_MAX_LENGTH else bundle_title


def reference_list_element(root):
    """Find the label's Reference_List, or create it in the right place if it's missing."""
    Reference_List = root.find('{}Reference_List'.format(NAMESPACE))
    if Reference_List is not None:
        return Reference_List
    Reference_List = etree.Element('{}Reference_List'.format(NAMESPACE))
    previous = root.find('{}Context_Area'.format(NAMESPACE))
    if previous is None:
        previous = root.find('{}Identification_Area'.format(NAMESPACE))
    previous.addnext(Reference_List)
    return Reference_List


def target_reference_type(label_root):
    """The reference_type a Target_Identification needs in this kind of label.

    Every product class ELSA writes should be in the table above.
    """
    localname = etree.QName(label_root).localname
    return TARGET_REFERENCE_TYPES.get(localname, 'collection_to_target')


def context_container(Context_Area, tag):
    """Find a Context_Area child, creating it in the PDS namespace if missing.

    The templates don't have empty Investigation_Area and Observing_System since those fail
    validation. Has to use the namespace so the next find() can see it.
    """
    child = Context_Area.find('{}{}'.format(NAMESPACE, tag))
    if child is None:
        child = etree.SubElement(Context_Area, '{}{}'.format(NAMESPACE, tag))
    return child


def get_upload_path(instance, filename):
    return '{0}/{1}'.format(instance.user.id, filename)


"""
"""


def get_three_years_in_future():
    now = datetime.datetime.now()
    return now.year + 3


"""
"""


def get_user_document_directory(instance, filename):
    document_collection_directory = 'archive/{0}/{1}/documents/'.format(
        instance.bundle.user.username, instance.bundle.name)
    return document_collection_directory


#    Register your models here -----------------------------------------------------------------------
"""
    Note:  Most names in ELSA are explicit.  However, we could not make a 'number' attribute to identify the version number (ex: 1800, 1A00, 1A10) because it conflicted with Django's number attribute given to each model.
    
"""


# @python_2_unicode_compatible
class Version(models.Model):

    num = models.CharField(max_length=4)
    xml_model = models.CharField(max_length=MAX_CHAR_FIELD)
    xmlns = models.CharField(max_length=MAX_CHAR_FIELD)
    xsi = models.CharField(max_length=MAX_CHAR_FIELD)
    schemaLocation = models.CharField(max_length=MAX_CHAR_FIELD)
    schematypens = models.CharField(max_length=MAX_CHAR_FIELD)

    """
         __str__ returns a string to be displayed when a model object is called.
         For the Version model object we want a four-digit value like 1800 or 1A00.
    """

    def __str__(self):
        return self.num

    """
        with_dots gives the version number with dots between each number.  Sometimes in PDS4
        we require that the number include dots, other times not so much.  For four-digit values with
        hex characters (1A00), we should return the hex character as its decimal equivalent (1.10.0.0).
    """

    def with_dots(self, number):
        # PDS4 uses a single letter for version components of 10 or more, so build numbers stay
        # four characters: A=10, B=11, ... O=24, ... Z=35. 1O00 is 1.24.0.0 and 1800 is 1.8.0.0.
        components = []

        for character in number:
            if character.isalpha():
                components.append(
                    str(10 + ord(character.upper()) - ord('A')))
            else:
                components.append(character)

        # Number is now formatted to pds standard, so return it.
        return '.'.join(components)

    """
        fill_xml_schema takes in the root of a label (ex tags: Product_Bundle, Product_Collection)
        and adds in the xml-model processing instruction and the xsi:schemaLocation
    

        Fillers follow a set flow.
            1. Input the root element of an XML label.
                - We want the root because we can access all areas of the document through it's root.
            2. Find the areas you want to fill.
                - Always do find over a static search to ensure we are always on the right element.
                  (  ex. of static search ->    root[0] = Identification_Area in fill_base_case    )
                  Originally, ELSA used a static search for faster performance, but we found out
                  that comments in the XML label through the code off and we were pulling incorrect
                  elements.
            3. Fill those areas.
                - Fill is easy.  Just fill it.. with the information from the model it was called on,
                  self (like itself).
    
    """

    def fill_xml_schema(self, root):

        # Change the xml-model processing instruction
        text = 'href={0} schematypens="http://purl.oclc.org/dsdl/schematron"'.format(
            self.xml_model)
        root.addprevious(etree.ProcessingInstruction('xml-model', text=text))

        # Change the xsi:schemaLocation

        return root

    def validate(self):
        print('Currently, ELSA does no version validation.')

    # Validators

    def get_validators(self):
        pass

    # Main Functions

    def version_update(self, number, label_path):
        '''Updates the pds4 information model version number in an xml file,
        given the new version number and the path to that file (both strings).
        
            
            '''

        #function from chocolate. still doing imports the old way for now
        label_path, label_root, tree = open_label_with_tree(label_path)
        
        location = '{http://www.w3.org/2001/XMLSchema-instance}schemaLocation'
        schemas = label_root.attrib[location].split(' ')
        
        #Singles out just the regular pds4 info model, so we don't touch any
        #other dictionary schema (for now)
        info_model=[s for s in schemas if '/pds/' in s and '.xsd/' in s][0]
        schemas.remove(info_model)
        
        #Isolates the 4-character version number
        old_number=info_model.split('/')[-1].split('.')[0].split('_')[-1]
        
        schemas.insert(1, info_model.replace(old_number, number))
        label_root.set(location, ' '.join(schemas)) #replace version number
        
        #maps the namespace string (label_root.tag.split et cetera) to 
        #something shorter    
        ns = {'d':label_root.tag.split('{')[1].split('}')[0]} 
        
        #find the information_model_version tag
        for im_version in label_root.findall("d:Identification_Area/d:information_model_version", ns):
            im_version.text=self.with_dots(number) #replace version number
            
        close_label(label_path, label_root) #also from chocolate
        
        
    def version_update_old(self, number, inFile, outFile):
        ''''Original version of this function, to be replaced with above and 
        renamed (or deleted?) once I'm positive the new one works. Keep this 
        one around for now, for reference and for when the new one breaks.'''

        #read the bundle and collection template files and store their contents in strings.
        #If the file is invalid a statement will be printed and the function will quit.
        try: 
            fil = open(inFile, 'r', encoding='utf-8')

            fileText = fil.read()

            fil.close()
        except:
            print(inFile + " is an invalid file")
            return

        # External templates are pinned to one IM version and have no placeholder, so it's just a copy.
        if 'AAAA' not in fileText:
            print(inFile + " has no 'AAAA' placeholder; copying its version as pinned.")

        # information_model_version gets the dotted form (1.24.0.0). The other placeholders are in
        # schema filenames and get the build number (1O00). Fill the dotted one first, by tag.
        fileText = re.sub(
            r'(<(?:\w+:)?information_model_version>)AAAA(</(?:\w+:)?information_model_version>)',
            r'\g<1>{}\g<2>'.format(self.with_dots(number)),
            fileText)

        # change the version number everywhere else
        fileText = fileText.replace('AAAA', number)

        # write the new bundle and collection to the xmls

        fil = open(outFile, 'w', encoding='utf-8')

        fil.write(fileText)

        fil.close()


"""
10.21  Investigation_Area

Root Class:Product_Components
Role:Concrete

Class Description:The Investigation_Area class provides information about an investigation (mission, observing campaign or other coordinated, large-scale data collection effort).

Steward:pds
Namespace Id:pds
Version Id:1.1.0.0
          Entity         Card         Value/Class         Ind

Hierarchy        Product_Components                           
                 . Investigation_Area                           
Subclass        none                           
Attribute        name        1                  
                 type        1        Individual Investigation         
                                   Mission         
                                   Observing Campaign         
                                   Other Investigation         
Inherited Attribute        none                           
Association                internal_reference        1..*        Internal_Reference         
Inherited Association        none                           

Referenced from        Context_Area                           
                Observation_Area                           
"""
'''
class Investigation(models.Model):
    INVESTIGATION_TYPES = [
        ('Individual Investigation','Individual Investigation'),
        ('Mission','Mission'),
        ('Observing Campaign','Observing Campaign'),
        ('Other Investigation','Other Investigation'),
    ]
    name = models.CharField(max_length=MAX_CHAR_FIELD)
    type_of = models.CharField(max_length=MAX_CHAR_FIELD, choices=INVESTIGATION_TYPES)
'''


"""
14.2  Facility

Root Class:Tagged_NonDigital_Object
Role:Concrete

Class Description:The Facility class provides a name and address for a terrestrial observatory or laboratory.

Steward:pds
Namespace Id:pds
Version Id:1.0.0.0
          Entity         Card         Value/Class         Ind
Hierarchy        Tagged_NonDigital_Object                           
                 . TNDO_Context                           
                 . . Facility                           
Subclass        none                           
Attribute        address                0..1                  
                 country                0..1                  
                 description        0..1                  
                 name                0..1                  
                 type                1        Laboratory         
                                           Observatory         

Inherited Attribute        none                           
Association                data_object        1        Physical_Object         
Inherited Association        none                           

Referenced from        Product_Context                           
"""
'''
@python_2_unicode_compatible
class Facility(models.Model):
    FACILITY_TYPES = [
        ('Laboratory','Laboratory'),
        ('Observatory','Observatory'),
    ]

    address = models.CharField(max_length=MAX_CHAR_FIELD)
    country = models.CharField(max_length=MAX_CHAR_FIELD)
    description = models.CharField(max_length=MAX_TEXT_FIELD) # Use a widget in forms if need be 
    investigation = models.ManyToManyField(Investigation)
    name = models.CharField(max_length=MAX_CHAR_FIELD)
    logical_identifier = models.CharField(max_length=MAX_CHAR_FIELD)
    type_of = models.CharField(max_length=MAX_CHAR_FIELD, choices=FACILITY_TYPES) 

    # Accessors
    def name_lid_case(self):
        # Convert name to lower case
        name_edit = self.name.lower()
        # Convert spaces to underscores
        name_edit = replace_all(name_edit, ' ', '_')

    # Meta
    def __str__(self):
        return self.name

'''


"""
14.4  Instrument_Host

Root Class:Tagged_NonDigital_Object
Role:Concrete

Class Description:The Instrument Host class provides a description of the physical object upon which an instrument is mounted.

Steward:pds
Namespace Id:pds
Version Id:1.3.0.0
          Entity         Card         Value/Class         Ind
Hierarchy        Tagged_NonDigital_Object                           
                . TNDO_Context                           
                . . Instrument_Host                           
Subclass        none                           

Attribute        description                                1                  
                instrument_host_version_id *Deprecated*        0..1                  
                naif_host_id                                0..1                  
                name                                        0..1                  
                serial_number                                0..1                  
                type                                        1        Earth Based         
                                                                   Earth-based         
                                                                   Lander         
                                                                   Rover         
                                                                   Spacecraft         
                version_id *Deprecated*                        0..1                  

Inherited Attribute        none                           
Association             data_object        1        Physical_Object         
Inherited Association        none                           

Referenced from        Product_Context                           
"""
'''
@python_2_unicode_compatible
class InstrumentHost(models.Model):
    INSTRUMENT_HOST_TYPES = [
        ('Earth Based','Earth Based'),
        ('Lander', 'Lander'),
        ('Rover', 'Rover'),
        ('Spacecraft','Spacecraft'),
    ]
    description = models.CharField(max_length=MAX_TEXT_FIELD)
    mission = models.ForeignKey(Mission, on_delete=models.CASCADE)
    name = models.CharField(max_length=MAX_CHAR_FIELD)
    naif_host_id = models.CharField(max_length=MAX_CHAR_FIELD)
    serial_number = models.CharField(max_length=MAX_CHAR_FIELD)
    type_of = models.CharField(max_length=MAX_CHAR_FIELD, choices=INSTRUMENT_HOST_TYPES)

    # Meta
    def __str__(self):
        return self.name
'''


"""
14.3  Instrument

Root Class:Tagged_NonDigital_Object
Role:Concrete

Class Description:The Instrument class provides a description of a physical object that collects data.

Steward:pds
Namespace Id:pds
Version Id:1.3.0.0
          Entity         Card         Value/Class         Ind
Hierarchy        Tagged_NonDigital_Object                           
                . TNDO_Context                           
                 . . Instrument                           
Subclass        none                           

Attribute        description                1                  
                model_id                0..1                  
                naif_instrument_id        0..1                  
                name                        0..1                  
                 serial_number                0..1                  
                 subtype                        0..*                  
                 type                        1..*        Accelerometer         
                                                  Alpha Particle Detector         
                                                  Alpha Particle X-Ray Spectrometer         
                                                  Altimeter         
                                                  Anemometer         
                                                  Atmospheric Sciences         
                                                  Atomic Force Microscope         
                                                  Barometer         
                                                  Biology Experiments         
                                                  Bolometer         
                                                  Camera         
                                                  Cosmic Ray Detector         
                                                  Drilling Tool         
                                                  Dust         
                                                  Dust Detector         
                                                  Electrical Probe         
                                                  Energetic Particle Detector         
                                                  Gamma Ray Detector         
                                                  Gas Analyzer         
                                                  Gravimeter         
                                                  Grinding Tool         
                                                  Hygrometer         
                                                  Imager         
                                                  Imaging Spectrometer         
                                                  Inertial Measurement Unit         
                                                  Infrared Spectrometer         
                                                  Interferometer         
                                                  Laser Induced Breakdown Spectrometer         
                                                  Magnetometer         
                                                  Mass Spectrometer         
                                                  Microscope         
                                                  Microwave Spectrometer         
                                                  Moessbauer Spectrometer         
                                                  Naked Eye         
                                                  Neutral Particle Detector         
                                                  Neutron Detector         
                                                  Particle Detector         
                                                  Photometer         
                                                  Plasma Analyzer         
                                                  Plasma Detector         
                                                  Plasma Wave Spectrometer         
                                                  Polarimeter         
                                                  Radar         
                                                  Radio Science         
                                                  Radio Spectrometer         
                                                  Radio Telescope         
                                                  Radio-Radar         
                                                  Radiometer         
                                                  Reflectometer         
                                                  Regolith Properties         
                                                  Robotic Arm         
                                                  Seismometer         
                                                  Small Bodies Sciences         
                                                  Spectrograph         
                                                  Spectrograph Imager         
                                                  Spectrometer         
                                                  Thermal Imager         
                                                  Thermal Probe         
                                                  Thermometer         
                                                  Ultraviolet Spectrometer         
                                                  Weather Station         
                                                  Wet Chemistry Laboratory         
                                                  X-ray Detector         
                                                  X-ray Diffraction Spectrometer         
                                                  X-ray Fluorescence Spectrometer         
Inherited Attribute        none                           
Association              data_object        1        Physical_Object         
Inherited Association        none                           

Referenced from        Product_Context                           
"""
'''
class Instrument(models.Model):
    INSTRUMENT_TYPES = [
        ('Accelerometer','Accelerometer'),
        ('Alpha Particle Detector','Alpha Particle Detector'),
        ('Alpha Particle X-Ray Spectrometer','Alpha Particle X-Ray Spectrometer'),
        ('Altimeter','Altimeter'),
        ('Anemometer','Anemometer'),
        ('Atmospheric Sciences','Atmospheric Sciences'),
        ('Atomic Force Microscope','Atomic Force Microscope'),
        ('Barometer','Barometer'),
        ('Biology Experiments','Biology Experiments'),
        ('Bolometer','Bolometer'),
        ('Camera','Camera'),
        ('Cosmic Ray Detector','Cosmic Ray Detector'),
        ('Drilling Tool','Drilling Tool'),
        ('Dust','Dust'),
        ('Dust Detector','Dust Detector'),
        ('Electrical Probe','Electrical Probe'),
        ('Energetic Particle Detector','Energetic Particle Detector'),
        ('Gamma Ray Detector','Gamma Ray Detector'),
        ('Gas Analyzer','Gas Analyzer'),
        ('Gravimeter','Gravimeter'),
        ('Grinding Tool','Grinding Tool'),
        ('Hygrometer','Hygrometer'),
        ('Imager','Imager'),
        ('Imaging Spectrometer','Imaging Spectrometer'),
        ('Inertial Measurement Unit','Inertial Measurement Unit'),
        ('Infrared Spectrometer','Infrared Spectrometer'),
        ('Interferometer','Interferometer'),
        ('Laser Induced Breakdown Spectrometer','Laser Induced Breakdown Spectrometer'),
        ('Magnetometer','Magnetometer'),
        ('Mass Spectrometer','Mass Spectrometer'),
        ('Microscope','Microscope'),
        ('Microwave Spectrometer','Microwave Spectrometer'),
        ('Moessbauer Spectrometer','Moessbauer Spectrometer'),
        ('Naked Eye','Naked Eye'),
        ('Neutral Particle Detector','Neutral Particle Detector'),
        ('Neutron Detector','Neutron Detector'),
        ('Particle Detector','Particle Detector'),
        ('Photometer','Photometer'),
        ('Plasma Analyzer','Plasma Analyzer'),
        ('Plasma Detector','Plasma Detector'),
        ('Plasma Wave Spectrometer','Plasma Wave Spectrometer'),
        ('Polarimeter','Polarimeter'),
        ('Radar','Radar'),
        ('Radio Science','Radio Science'),
        ('Radio Spectrometer','Radio Spectrometer'),
        ('Radio Telescope','Radio Telescope'),
        ('Radio-Radar','Radio-Radar'),
        ('Radiometer','Radiometer'),
        ('Reflectometer','Reflectometer'),
        ('Regolith Properties','Regolith Properties'),
        ('Robotic Arm','Robotic Arm'),
        ('Seismometer','Seismometer'),
        ('Small Bodies Sciences','Small Bodies Sciences'),
        ('Spectrograph','Spectrograph'),
        ('Spectrograph Imager','Spectrograph Imager'),
        ('Spectrometer','Spectrometer'),
        ('Thermal Imager','Thermal Imager'),
        ('Thermal Probe','Thermal Probe'),
        ('Thermometer','Thermometer'),
        ('Ultraviolet Spectrometer','Ultraviolet Spectrometer'),
        ('Weather Station','Weather Station'),
        ('Wet Chemistry Laboratory','Wet Chemistry Laboratory'),
        ('X-ray Detector','X-ray Detector'),
        ('X-ray Diffraction Spectrometer','X-ray Diffraction Spectrometer'),
        ('X-ray Fluorescence Spectrometer','X-ray Fluorescence Spectrometer'),
    ]
    description = models.CharField(max_length=MAX_TEXT_FIELD)
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE)  # This might be incorrect.
    instrument_host = models.ForeignKey(InstrumentHost)
    model_id = models.CharField(max_length=MAX_CHAR_FIELD)
    naif_instrument_id = models.CharField(max_length=MAX_CHAR_FIELD)
    name = models.CharField(max_length=MAX_CHAR_FIELD)
    serial_number = models.CharField(max_length=MAX_CHAR_FIELD)
    subtype = models.CharField(max_length=MAX_CHAR_FIELD)
    type_of = models.CharField(max_length=MAX_CHAR_FIELD, choices=INSTRUMENT_TYPES)

    # Meta
    def __str__(self):
        return self.name
'''


"""
14.8  Target

Root Class:Tagged_NonDigital_Object
Role:Concrete
Class Description:The Target class provides a description of a physical object that is the object of data collection.
Steward:pds
Namespace Id:pds
Version Id:1.3.0.0
          Entity         Card         Value/Class         Ind
Hierarchy        Tagged_NonDigital_Object                           
         . TNDO_Context                           
         . . Target                           
Subclass        none                          
Attribute        description        1                  
         name        0..1                  
         type        0..*        Asteroid         
                           Calibration         
                           Calibration Field         
                           Calibrator         
                           Comet         
                           Dust         
                           Dwarf Planet         
                           Equipment         
                           Exoplanet System         
                           Galaxy         
                           Globular Cluster         
                           Lunar Sample         
                           Meteorite         
                           Meteoroid         
                           Meteoroid Stream         
                           Nebula         
                           Open Cluster         
                           Planet         
                           Planetary Nebula         
                           Planetary System         
                           Plasma Cloud         
                           Plasma Stream         
                           Ring         
                           Satellite         
                           Star         
                           Star Cluster         
                           Sun         
                           Synthetic Sample         
                           Terrestrial Sample         
                           Trans-Neptunian Object         
Inherited Attribute        none                           
Association        data_object        1        Physical_Object         
Inherited Association        none                           
Referenced from        Product_Context                           
"""
'''
@python_2_unicode_compatible
class Target(models.Model):
    TARGET_TYPES = [
        ('Asteroid','Asteroid'),
        ('Calibration','Calibration'),
        ('Calibration Field','Calibration Field'),
        ('Calibrator','Calibrator'),
        ('Comet','Comet'),
        ('Dust','Dust'),
        ('Dwarf Planet','Dwarf Planet'),
        ('Equipment','Equipment'),
        ('Exoplanet System','Exoplanet System'),
        ('Galaxy','Galaxy'),
        ('Globular Cluster','Globular Cluster'),
        ('Lunar Sample','Lunar Sample'),
        ('Meteorite','Meteorite'),
        ('Meteoroid','Meteoroid'),
        ('Meteoroid Stream','Meteoroid Stream'),
        ('Nebula','Nebula'),
        ('Open Cluster','Open Cluster'),
        ('Planet','Planet'),
        ('Planetary Nebula','Planetary Nebula'),
        ('Planetary System','Planetary System'),
        ('Plasma Cloud','Plasma Cloud'),
        ('Plasma Stream','Plasma Stream'),
        ('Ring','Ring'),
        ('Satellite','Satellite'),
        ('Star','Star'),
        ('Star Cluster','Star Cluster'),
        ('Sun','Sun'),
        ('Synthetic Sample','Synthetic Sample'),
        ('Target Analog', 'Target Analog'),
        ('Terrestrial Sample','Terrestrial Sample'),
        ('Trans-Neptunian Object','Trans-Neptunian Object'),
    ]
    description = models.CharField(max_length=MAX_CHAR_FIELD)
    facility = models.ForeignKey(Facility, on_delete=models.CASCADE)
    mission = models.ForeignKey(Mission, on_delete=models.CASCADE)
    name = models.CharField(max_length=MAX_CHAR_FIELD)
    type_of = models.CharField(max_length=MAX_CHAR_FIELD, choices=TARGET_TYPES)

    # Meta
    def __str__(self):
        return self.name
'''


"""
    Context Models
"""

"""
10.21  Investigation_Area

Root Class:Product_Components
Role:Concrete

Class Description:The Investigation_Area class provides information about an investigation (mission, observing campaign or other coordinated, large-scale data collection effort).

Steward:pds
Namespace Id:pds
Version Id:1.1.0.0
          Entity         Card         Value/Class         Ind

Hierarchy        Product_Components                           
                 . Investigation_Area                           
Subclass        none                           
Attribute        name        1                  
                 type        1        Individual Investigation         
                                   Mission         
                                   Observing Campaign         
                                   Other Investigation         
Inherited Attribute        none                           
Association                internal_reference        1..*        Internal_Reference         
Inherited Association        none                           

Referenced from        Context_Area                           
                Observation_Area                           
"""


class InvestigationManager(models.Manager):
    def update_version(self, product_dict):
        self.vid = product_dict['vid']
        self.lid = product_dict['lid']
        self.file_ref = product_dict['url']
        self.save()


class Investigation(models.Model):
    INVESTIGATION_TYPES = [
        ('individual_investigation', 'individual_investigation'),
        ('mission', 'mission'),
        ('observing_campaign', 'observing_campaign'),
        ('other_investigation', 'other_investigation'),
    ]

    # Attributes used for crawler
    # null=true's added 11/2022

    name = models.CharField(max_length=MAX_CHAR_FIELD, null=True)
    type_of = models.CharField(
        max_length=MAX_CHAR_FIELD, choices=INVESTIGATION_TYPES, null=True)
    lid = models.CharField(max_length=MAX_LID_FIELD)
    vid = models.FloatField(default=1.0)
    internal_references = []
    file_ref = models.CharField(max_length=MAX_CHAR_FIELD)

    # Relational Attributes
    # (added 11/2022 by zena)
    # note we have to give these fields the name of the model, not the model
    # objects themselves, because these models haven't been defined yet

    instrument_hosts = models.ManyToManyField("Instrument_Host")
    facilities = models.ManyToManyField("Facility")
    # related_name gives Target a `.investigations` accessor (merged in migration 0067).
    targets = models.ManyToManyField("Target", related_name='investigations')

    # Attributes used to manage Investigation object
    #objects = InvestigationManager()

    class Meta:
        ordering = ('name',)

    def update_version(self, product_dict):
        self.vid = product_dict['vid']
        self.lid = product_dict['lid']
        self.file_ref = product_dict['url']
        self.save()

    def __str__(self):
        return self.name

    def fill_label(self, label_root):
        # Use 'is None', not truthiness. An lxml element with no children is falsy.
        Context_Area = label_root.find('{}Context_Area'.format(NAMESPACE))
        if Context_Area is None:
            Context_Area = label_root.find('{}Observation_Area'.format(NAMESPACE))

        Investigation_Area = context_container(Context_Area, 'Investigation_Area')

        # Build whichever of these are missing.
        name = Investigation_Area.find('{}name'.format(NAMESPACE))
        if name is None:
            name = etree.SubElement(Investigation_Area, '{}name'.format(NAMESPACE))
        name.text = pds_context_title(self.name, self.type_of)

        investigation_type = Investigation_Area.find('{}type'.format(NAMESPACE))
        if investigation_type is None:
            investigation_type = etree.SubElement(
                Investigation_Area, '{}type'.format(NAMESPACE))
        investigation_type.text = self.type_of
        # Investigation_Area.name.text = self.name
        # Investigation_Area.type.text = self.type_of

        Internal_Reference = Investigation_Area.find('{}Internal_Reference'.format(NAMESPACE))
        if Internal_Reference is None:
            Internal_Reference = etree.SubElement(
                Investigation_Area, '{}Internal_Reference'.format(NAMESPACE))
        lid_reference = Internal_Reference.find('{}lid_reference'.format(NAMESPACE))
        if lid_reference is None:
            lid_reference = etree.SubElement(
                Internal_Reference, '{}lid_reference'.format(NAMESPACE))
        lid_reference.text = self.lid
        reference_type = Internal_Reference.find('{}reference_type'.format(NAMESPACE))
        if reference_type is None:
            reference_type = etree.SubElement(
                Internal_Reference, '{}reference_type'.format(NAMESPACE))
        if label_root.tag == '{http://pds.nasa.gov/pds4/pds/v1}Product_Bundle':
            reference_type.text = 'bundle_to_investigation'
        elif label_root.tag == '{http://pds.nasa.gov/pds4/pds/v1}Product_Observational':
            reference_type.text = 'data_to_investigation'
        else:
            reference_type.text = 'collection_to_investigation'
        # Internal_Reference.lid_reference.text = self.lid
        # Internal_Reference.reference_type.text = 'is_investigation'

        # Add Facility to Observing System
        # Observing_System_Component = etree.SubElement(
        #     Observing_System, 'Observing_System_Component')
        # name = etree.SubElement(Observing_System_Component, 'name')
        # name.text = self.name
        # facility_type = etree.SubElement(
        #     Observing_System_Component, 'type')
        # facility_type.text = self.type_of
        # Internal_Reference = etree.SubElement(
        #     Observing_System_Component, 'Internal_Reference')
        # lid_reference = etree.SubElement(
        #     Internal_Reference, 'lid_reference')
        # lid_reference.text = self.lid
        # reference_type = etree.SubElement(
        #     Internal_Reference, 'reference_type')
        # reference_type.text = 'is_investigation'

        return label_root

        # Below is cursed do not touch - Said
        # will remove eventually
        # Get all xml labels in bundle directory
        # xml_path_list = get_xml_path(bundle.directory())

        # # Traverse labels
        # for xml_path in xml_path_list:

            # fil = open('/home/tpagan/older ELSAs/elsa_kays_current/ELSA-online-master/archive/tpagan/testingxml_bundle/document/collection_document.xml','r')

            # fileText = fil.read()

            # fil.close()
        
            # print fileText


            # Create Parser
            # parser = etree.XMLParser(
            #     remove_blank_text=True, remove_comments=True)
            # tree = etree.parse(xml_path, parser)
            # label = open(xml_path, 'w')

            # # Do what needs to be done
            # root = tree.getroot()
            # root_tag = etree.QName(root)

            # if root_tag.localname is 'Product_Bundle' or 'Product_Collection':

            #     # Locate Observing System
            #     print(root)
            #     Observing_System = root[1][2]
            #     print("\n\n\n\nDEBUG")
            #     print(root_tag.localname)
            #     print(Observing_System)

            #     # Add Facility to Observing System
            #     Observing_System_Component = etree.SubElement(
            #         Observing_System, 'Observing_System_Component')
            #     name = etree.SubElement(Observing_System_Component, 'name')
            #     name.text = self.name
            #     facility_type = etree.SubElement(
            #         Observing_System_Component, 'type')
            #     facility_type.text = self.type_of
            #     Internal_Reference = etree.SubElement(
            #         Observing_System_Component, 'Internal_Reference')
            #     lid_reference = etree.SubElement(
            #         Internal_Reference, 'lid_reference')
            #     lid_reference.text = self.lid
            #     reference_type = etree.SubElement(
            #         Internal_Reference, 'reference_type')
            #     reference_type.text = 'is_investigation'

            # # Properly close file
            # label_tree = etree.tostring(
            #     root, pretty_print=True, encoding='UTF-8', xml_declaration=True)
            # label.write(label_tree.decode())
            # label.close()

    def remove_xml(self, label_root):
        Context_Area = label_root.find('{}Context_Area'.format(NAMESPACE))
        if Context_Area is None:
            # This label never got a Context_Area, so there is nothing to take out.
            return label_root

        Investigation_Area = Context_Area.find('{}Investigation_Area'.format(NAMESPACE))

        # Remove the whole Investigation_Area. Blanking the fields would fail validation.
        # It's optional, and fill_label rebuilds it when another investigation is added.
        if Investigation_Area is not None:
            Context_Area.remove(Investigation_Area)

        Observing_System = Context_Area.find('{}Observing_System'.format(NAMESPACE))
        if Observing_System is None:
            # Nothing was ever added, so there is nothing to take out.
            return label_root

        for component in Observing_System:

            if(component.tag == "{http://pds.nasa.gov/pds4/pds/v1}Observing_System_Component"):
                if(component[0].text == self.name):
                    component.getparent().remove(component)

        return label_root

"""
14.3  Instrument

Root Class:Tagged_NonDigital_Object
Role:Concrete

Class Description:The Instrument class provides a description of a physical object that collects data.

Steward:pds
Namespace Id:pds
Version Id:1.3.0.0
          Entity         Card         Value/Class         Ind
Hierarchy        Tagged_NonDigital_Object                           
                . TNDO_Context                           
                 . . Instrument                           
Subclass        none                           

Attribute        description                1                  
                model_id                0..1                  
                naif_instrument_id        0..1                  
                name                        0..1                  
                 serial_number                0..1                  
                 subtype                        0..*                  
                 type                        1..*        Accelerometer         
                                                  Alpha Particle Detector         
                                                  Alpha Particle X-Ray Spectrometer         
                                                  Altimeter         
                                                  Anemometer         
                                                  Atmospheric Sciences         
                                                  Atomic Force Microscope         
                                                  Barometer         
                                                  Biology Experiments         
                                                  Bolometer         
                                                  Camera         
                                                  Cosmic Ray Detector         
                                                  Drilling Tool         
                                                  Dust         
                                                  Dust Detector         
                                                  Electrical Probe         
                                                  Energetic Particle Detector         
                                                  Gamma Ray Detector         
                                                  Gas Analyzer         
                                                  Gravimeter         
                                                  Grinding Tool         
                                                  Hygrometer         
                                                  Imager         
                                                  Imaging Spectrometer         
                                                  Inertial Measurement Unit         
                                                  Infrared Spectrometer         
                                                  Interferometer         
                                                  Laser Induced Breakdown Spectrometer         
                                                  Magnetometer         
                                                  Mass Spectrometer         
                                                  Microscope         
                                                  Microwave Spectrometer         
                                                  Moessbauer Spectrometer         
                                                  Naked Eye         
                                                  Neutral Particle Detector         
                                                  Neutron Detector         
                                                  Particle Detector         
                                                  Photometer         
                                                  Plasma Analyzer         
                                                  Plasma Detector         
                                                  Plasma Wave Spectrometer         
                                                  Polarimeter         
                                                  Radar         
                                                  Radio Science         
                                                  Radio Spectrometer         
                                                  Radio Telescope         
                                                  Radio-Radar         
                                                  Radiometer         
                                                  Reflectometer         
                                                  Regolith Properties         
                                                  Robotic Arm         
                                                  Seismometer         
                                                  Small Bodies Sciences         
                                                  Spectrograph         
                                                  Spectrograph Imager         
                                                  Spectrometer         
                                                  Thermal Imager         
                                                  Thermal Probe         
                                                  Thermometer         
                                                  Ultraviolet Spectrometer         
                                                  Weather Station         
                                                  Wet Chemistry Laboratory         
                                                  X-ray Detector         
                                                  X-ray Diffraction Spectrometer         
                                                  X-ray Fluorescence Spectrometer         
Inherited Attribute        none                           
Association              data_object        1        Physical_Object         
Inherited Association        none                           

Referenced from        Product_Context                           
"""


class InstrumentManager(models.Manager):
    def update_version(self, product_dict):
        self.vid = product_dict['vid']
        self.lid = product_dict['lid']
        self.file_ref = product_dict['url']
        self.save()


class Instrument(models.Model):
    INSTRUMENT_TYPES = [
        ('Accelerometer', 'Accelerometer'),
        ('Alpha Particle Detector', 'Alpha Particle Detector'),
        ('Alpha Particle X-Ray Spectrometer', 'Alpha Particle X-Ray Spectrometer'),
        ('Altimeter', 'Altimeter'),
        ('Anemometer', 'Anemometer'),
        ('Atmospheric Sciences', 'Atmospheric Sciences'),
        ('Atomic Force Microscope', 'Atomic Force Microscope'),
        ('Barometer', 'Barometer'),
        ('Biology Experiments', 'Biology Experiments'),
        ('Bolometer', 'Bolometer'),
        ('Camera', 'Camera'),
        ('Cosmic Ray Detector', 'Cosmic Ray Detector'),
        ('Drilling Tool', 'Drilling Tool'),
        ('Dust', 'Dust'),
        ('Dust Detector', 'Dust Detector'),
        ('Electrical Probe', 'Electrical Probe'),
        ('Energetic Particle Detector', 'Energetic Particle Detector'),
        ('Gamma Ray Detector', 'Gamma Ray Detector'),
        ('Gas Analyzer', 'Gas Analyzer'),
        ('Gravimeter', 'Gravimeter'),
        ('Grinding Tool', 'Grinding Tool'),
        ('Hygrometer', 'Hygrometer'),
        ('Imager', 'Imager'),
        ('Imaging Spectrometer', 'Imaging Spectrometer'),
        ('Inertial Measurement Unit', 'Inertial Measurement Unit'),
        ('Infrared Spectrometer', 'Infrared Spectrometer'),
        ('Interferometer', 'Interferometer'),
        ('Laser Induced Breakdown Spectrometer',
         'Laser Induced Breakdown Spectrometer'),
        ('Magnetometer', 'Magnetometer'),
        ('Mass Spectrometer', 'Mass Spectrometer'),
        ('Microscope', 'Microscope'),
        ('Microwave Spectrometer', 'Microwave Spectrometer'),
        ('Moessbauer Spectrometer', 'Moessbauer Spectrometer'),
        ('Naked Eye', 'Naked Eye'),
        ('Neutral Particle Detector', 'Neutral Particle Detector'),
        ('Neutron Detector', 'Neutron Detector'),
        ('Particle Detector', 'Particle Detector'),
        ('Photometer', 'Photometer'),
        ('Plasma Analyzer', 'Plasma Analyzer'),
        ('Plasma Detector', 'Plasma Detector'),
        ('Plasma Wave Spectrometer', 'Plasma Wave Spectrometer'),
        ('Polarimeter', 'Polarimeter'),
        ('Radar', 'Radar'),
        ('Radio Science', 'Radio Science'),
        ('Radio Spectrometer', 'Radio Spectrometer'),
        ('Radio Telescope', 'Radio Telescope'),
        ('Radio-Radar', 'Radio-Radar'),
        ('Radiometer', 'Radiometer'),
        ('Reflectometer', 'Reflectometer'),
        ('Regolith Properties', 'Regolith Properties'),
        ('Robotic Arm', 'Robotic Arm'),
        ('Seismometer', 'Seismometer'),
        ('Small Bodies Sciences', 'Small Bodies Sciences'),
        ('Spectrograph', 'Spectrograph'),
        ('Spectrograph Imager', 'Spectrograph Imager'),
        ('Spectrometer', 'Spectrometer'),
        ('Thermal Imager', 'Thermal Imager'),
        ('Thermal Probe', 'Thermal Probe'),
        ('Thermometer', 'Thermometer'),
        ('Ultraviolet Spectrometer', 'Ultraviolet Spectrometer'),
        ('Weather Station', 'Weather Station'),
        ('Wet Chemistry Laboratory', 'Wet Chemistry Laboratory'),
        ('X-ray Detector', 'X-ray Detector'),
        ('X-ray Diffraction Spectrometer', 'X-ray Diffraction Spectrometer'),
        ('X-ray Fluorescence Spectrometer', 'X-ray Fluorescence Spectrometer'),
    ]
    # Relational Attributes
    instrument_hosts = models.ManyToManyField("Instrument_Host")
    investigations = models.ManyToManyField(Investigation)

    facilities = models.ManyToManyField("Facility")

    # Attributes used for crawler
    # null=true's added 11/2022 

    lid = models.CharField(max_length=MAX_LID_FIELD)
    name = models.CharField(max_length=MAX_CHAR_FIELD, null=True)
    type_of = models.CharField(
        max_length=MAX_CHAR_FIELD, choices=INSTRUMENT_TYPES, null=True)
    vid = models.FloatField(default=1.0)
    file_ref = models.CharField(max_length=MAX_CHAR_FIELD)

    # inv = []
    # ih = []

    # Attributes used to manage Instrument Host object
    #objects = InstrumentManager()

    # Meta
    class Meta:
        ordering = ('name',)

    def __str__(self):
        return self.name

    def update_version(self, product_dict):
        self.vid = product_dict['vid']
        self.lid = product_dict['lid']
        self.file_ref = product_dict['url']
        self.save()

    def fill_label(self, label_root):
        # Use 'is None', not truthiness. An lxml element with no children is falsy.
        Context_Area = label_root.find('{}Context_Area'.format(NAMESPACE))
        if Context_Area is None:
            Context_Area = label_root.find('{}Observation_Area'.format(NAMESPACE))

        Observing_System = context_container(Context_Area, 'Observing_System')
        

        # Add Facility to Observing System
        Observing_System_Component = etree.SubElement(
            Observing_System, 'Observing_System_Component')
        
        Observing_System.insert(2, Observing_System_Component)

        name = etree.SubElement(Observing_System_Component, 'name')
        name.text = self.name.title()
        facility_type = etree.SubElement(
            Observing_System_Component, 'type')
        facility_type.text = 'Instrument'
        Internal_Reference = etree.SubElement(
            Observing_System_Component, 'Internal_Reference')
        lid_reference = etree.SubElement(
            Internal_Reference, 'lid_reference')
        lid_reference.text = self.lid
        reference_type = etree.SubElement(
            Internal_Reference, 'reference_type')
        reference_type.text = 'is_instrument'

        return label_root

        # Get all xml labels in directory
        # xml_path_list = get_xml_path(bundle.directory())

        # # Traverse labels
        # for xml_path in xml_path_list:

        #     # Create Parser
        #     parser = etree.XMLParser(
        #         remove_blank_text=True, remove_comments=True)
        #     tree = etree.parse(xml_path, parser)
        #     label = open(xml_path, 'w')

        #     # Do what needs to be done
        #     root = tree.getroot()
        #     root_tag = etree.QName(root)

        #     if root_tag.localname is 'Product_Bundle' or 'Product_Collection':
        #         # Locate Observing System
        #         Observing_System = root[1][2]

        #         # Add Instrument Host to Observing System
        #         Observing_System_Component = etree.SubElement(
        #             Observing_System, 'Observing_System_Component')
        #         name = etree.SubElement(Observing_System_Component, 'name')
        #         name.text = self.name.title()
        #         inst_type = etree.SubElement(
        #             Observing_System_Component, 'type')
        #         inst_type.text = self.type_of
        #         Internal_Reference = etree.SubElement(
        #             Observing_System_Component, 'Internal_Reference')
        #         lid_reference = etree.SubElement(
        #             Internal_Reference, 'lid_reference')
        #         lid_reference.text = self.lid
        #         reference_type = etree.SubElement(
        #             Internal_Reference, 'reference_type')
        #         reference_type.text = 'is_instrument'

        #     # When the time comes, add in if tag.localname is 'Product_Document' and if tag.localname is 'Product_Collection' and use the same function to call for those.

        #     # Properly close file
        #     # .decode() ensures that the label is written in the proper encoding from UTF-8 to unicode
        #     # UTF-8 is the format that the xml files in UTF-8 which is bytes, and unicode ensures string
        #     label_tree = etree.tostring(
        #         root, pretty_print=True, encoding='UTF-8', xml_declaration=True)
        #     label.write(label_tree.decode())
        #     label.close()

    def remove_xml(self, label_root):
        Context_Area = label_root.find('{}Context_Area'.format(NAMESPACE))
        if Context_Area is None:
            # This label never got a Context_Area, so there is nothing to take out.
            return label_root

        Observing_System = Context_Area.find('{}Observing_System'.format(NAMESPACE))
        if Observing_System is None:
            # Nothing was ever added, so there is nothing to take out.
            return label_root

        for component in Observing_System:

            print(component.tag)
            if(component.tag == "{http://pds.nasa.gov/pds4/pds/v1}Observing_System_Component"):
                if(component[0].text.title() == self.name.title()):
                    component.getparent().remove(component)

        return label_root


"""
14.8  Target

Root Class:Tagged_NonDigital_Object
Role:Concrete
Class Description:The Target class provides a description of a physical object that is the object of data collection.
Steward:pds
Namespace Id:pds
Version Id:1.3.0.0
          Entity         Card         Value/Class         Ind
Hierarchy        Tagged_NonDigital_Object                           
         . TNDO_Context                           
         . . Target                           
Subclass        none                          
Attribute        description        1                  
         name        0..1                  
         type        0..*        Asteroid         
                           Calibration         
                           Calibration Field         
                           Calibrator         
                           Comet         
                           Dust         
                           Dwarf Planet         
                           Equipment         
                           Exoplanet System         
                           Galaxy         
                           Globular Cluster         
                           Lunar Sample         
                           Meteorite         
                           Meteoroid         
                           Meteoroid Stream         
                           Nebula         
                           Open Cluster         
                           Planet         
                           Planetary Nebula         
                           Planetary System         
                           Plasma Cloud         
                           Plasma Stream         
                           Ring         
                           Satellite         
                           Star         
                           Star Cluster         
                           Sun         
                           Synthetic Sample         
                           Terrestrial Sample         
                           Trans-Neptunian Object         
Inherited Attribute        none                           
Association        data_object        1        Physical_Object         
Inherited Association        none                           
Referenced from        Product_Context                           
"""


class TargetManager(models.Manager):
    def update_version(self, product_dict):
        self.vid = product_dict['vid']
        self.lid = product_dict['lid']
        self.file_ref = product_dict['url']
        self.save()


# @python_2_unicode_compatible
class Target(models.Model):
    TARGET_TYPES = [
        ('Asteroid', 'Asteroid'),
        ('Calibration', 'Calibration'),
        ('Calibration Field', 'Calibration Field'),
        ('Calibrator', 'Calibrator'),
        ('Comet', 'Comet'),
        ('Dust', 'Dust'),
        ('Dwarf Planet', 'Dwarf Planet'),
        ('Equipment', 'Equipment'),
        ('Exoplanet System', 'Exoplanet System'),
        ('Galaxy', 'Galaxy'),
        ('Globular Cluster', 'Globular Cluster'),
        ('Lunar Sample', 'Lunar Sample'),
        ('Meteorite', 'Meteorite'),
        ('Meteoroid', 'Meteoroid'),
        ('Meteoroid Stream', 'Meteoroid Stream'),
        ('Nebula', 'Nebula'),
        ('Open Cluster', 'Open Cluster'),
        ('Planet', 'Planet'),
        ('Planetary Nebula', 'Planetary Nebula'),
        ('Planetary System', 'Planetary System'),
        ('Plasma Cloud', 'Plasma Cloud'),
        ('Plasma Stream', 'Plasma Stream'),
        ('Ring', 'Ring'),
        ('Satellite', 'Satellite'),
        ('Star', 'Star'),
        ('Star Cluster', 'Star Cluster'),
        ('Sun', 'Sun'),
        ('Synthetic Sample', 'Synthetic Sample'),
        ('Target Analog', 'Target Analog'),
        ('Terrestrial Sample', 'Terrestrial Sample'),
        ('Trans-Neptunian Object', 'Trans-Neptunian Object'),
    ]
    # Relational Attributes

    # Attributes used for crawler
    # null=True's added 11/2022

    lid = models.CharField(max_length=MAX_LID_FIELD)
    name = models.CharField(max_length=MAX_CHAR_FIELD, null=True)
    type_of = models.CharField(max_length=MAX_CHAR_FIELD, choices=TARGET_TYPES, null=True)
    vid = models.FloatField(default=1.0)
    file_ref = models.CharField(max_length=MAX_CHAR_FIELD)

    # Investigations for this target come from target.investigations (Investigation.targets).

    # Attributes used to manage Instrument Host object
    #objects = TargetManager()

    # Meta
    class Meta:
        ordering = ('name',)

    def __str__(self):
        return self.name

    def update_version(self, product_dict):
        self.vid = product_dict['vid']
        self.lid = product_dict['lid']
        self.file_ref = product_dict['url']
        self.save()

    def fill_label(self, label_root):
        # Use 'is None', not truthiness. An lxml element with no children is falsy.
        Context_Area = label_root.find('{}Context_Area'.format(NAMESPACE))
        if Context_Area is None:
            Context_Area = label_root.find('{}Observation_Area'.format(NAMESPACE))

        # Observing_System = Context_Area.find('{}Observing_System'.format(NAMESPACE))

        Target_Identification = insert_in_context_area(
            Context_Area, etree.SubElement(Context_Area, 'Target_Identification'))
        name = etree.SubElement(Target_Identification, 'name')
        name.text = self.name.title()
        target_type = etree.SubElement(Target_Identification, 'type')
        target_type.text = pds_target_type(self.type_of)
        Internal_Reference = etree.SubElement(
            Target_Identification, 'Internal_Reference')
        lid_reference = etree.SubElement(
            Internal_Reference, 'lid_reference')
        lid_reference.text = self.lid
        reference_type = etree.SubElement(
            Internal_Reference, 'reference_type')
        reference_type.text = target_reference_type(label_root)

        return label_root

        # Get all xml labels in directory
        # xml_path_list = get_xml_path(bundle.directory())

        # # Traverse labels
        # for xml_path in xml_path_list:

        #     # Create Parser
        #     parser = etree.XMLParser(
        #         remove_blank_text=True, remove_comments=True)
        #     tree = etree.parse(xml_path, parser)
        #     label = open(xml_path, 'w')

        #     # Do what needs to be done
        #     root = tree.getroot()
        #     root_tag = etree.QName(root)

        #     # Are there specifics on Target?  This code was copy and pasted from adding an Instrument_Host to an Observing_System_Component.
        #     if root_tag.localname is 'Product_Bundle' or 'Product_Collection':
        #         # Locate Context_Area
        #         Context_Area = root[1]

        #         # Add Target to Target Identification
        #         Target_Identification = etree.SubElement(
        #             Context_Area, 'Target_Identification')
        #         name = etree.SubElement(Target_Identification, 'name')
        #         name.text = self.name.title()
        #         targ_type = etree.SubElement(Target_Identification, 'type')
        #         targ_type.text = self.type_of
        #         Internal_Reference = etree.SubElement(
        #             Target_Identification, 'Internal_Reference')
        #         lid_reference = etree.SubElement(
        #             Internal_Reference, 'lid_reference')
        #         lid_reference.text = self.lid
        #         reference_type = etree.SubElement(
        #             Internal_Reference, 'reference_type')
        #         reference_type.text = 'is_target'

        #     # When the time comes, add in if tag.localname is 'Product_Document' and if tag.localname is 'Product_Collection' and use the same function to call for those.

        #     # Properly close file
        #     label_tree = etree.tostring(
        #         root, pretty_print=True, encoding='UTF-8', xml_declaration=True)
        #     label.write(label_tree.decode())
        #     label.close()

    def remove_xml(self, label_root):
        context_area = label_root.find(
            './/{}Context_Area'.format(NAMESPACE)
        )

        if context_area is None:
            return label_root

        for target_identification in context_area.findall(
            '{}Target_Identification'.format(NAMESPACE)
        ):
            target_name = target_identification.find(
                '{}name'.format(NAMESPACE)
            )

            if (
                target_name is not None
                and target_name.text
                and self.name
                and target_name.text.strip().casefold()
                == self.name.strip().casefold()
            ):
                context_area.remove(target_identification)
                print('Removed target from XML:', self.name)

        return label_root




"""
14.4  Instrument_Host

Root Class:Tagged_NonDigital_Object
Role:Concrete

Class Description:The Instrument Host class provides a description of the physical object upon which an instrument is mounted.

Steward:pds
Namespace Id:pds
Version Id:1.3.0.0
          Entity         Card         Value/Class         Ind
Hierarchy        Tagged_NonDigital_Object                           
                . TNDO_Context                           
                . . Instrument_Host                           
Subclass        none                           

Attribute        description                                1                  
                instrument_host_version_id *Deprecated*        0..1                  
                naif_host_id                                0..1                  
                name                                        0..1                  
                serial_number                                0..1                  
                type                                        1        Earth Based         
                                                                   Earth-based         
                                                                   Lander         
                                                                   Rover         
                                                                   Spacecraft         
                version_id *Deprecated*                        0..1                  

Inherited Attribute        none                           
Association             data_object        1        Physical_Object         
Inherited Association        none                           

Referenced from        Product_Context                           
"""


class Instrument_HostManager(models.Manager):
    def update_version(self, product_dict):
        self.vid = product_dict['vid']
        self.lid = product_dict['lid']
        self.file_ref = product_dict['url']
        self.save()


# @python_2_unicode_compatible
class Instrument_Host(models.Model):
    INSTRUMENT_HOST_TYPES = [
        ('Earth Based', 'Earth Based'),
        ('Lander', 'Lander'),
        ('Rover', 'Rover'),
        ('Spacecraft', 'Spacecraft'),
        # This is only for a fix in Starbase and should be deleted once fixed
        ('unk', 'unk'),
    ]

    # Relational Attributes
    investigations = models.ManyToManyField(Investigation)
    instruments = models.ManyToManyField(Instrument)
    # targets = models.ManyToManyField(Target)

    # inv = models.ManyToManyField(Investigation)

    # Attributes used for crawler
    # null=True's added 11/2022

    lid = models.CharField(max_length=MAX_LID_FIELD)
    name = models.CharField(max_length=MAX_CHAR_FIELD, null=True)
    type_of = models.CharField(
        max_length=MAX_CHAR_FIELD, choices=INSTRUMENT_HOST_TYPES, null=True)
    vid = models.FloatField(default=1.0)
    file_ref = models.CharField(max_length=MAX_CHAR_FIELD)

    # Attributes used to manage Instrument Host object
    #objects = Instrument_HostManager()

    # Meta
    class Meta:
        ordering = ('name',)

    def __str__(self):
        return self.name

    def update_version(self, product_dict):
        self.vid = product_dict['vid']
        self.lid = product_dict['lid']
        self.file_ref = product_dict['url']
        self.save()

    def fill_label(self, label_root):
        # Use 'is None', not truthiness. An lxml element with no children is falsy.
        Context_Area = label_root.find('{}Context_Area'.format(NAMESPACE))
        if Context_Area is None:
            Context_Area = label_root.find('{}Observation_Area'.format(NAMESPACE))

        Observing_System = context_container(Context_Area, 'Observing_System')

        # Add Facility to Observing System
        Observing_System_Component = etree.SubElement(
            Observing_System, 'Observing_System_Component')

        Observing_System.insert(1, Observing_System_Component)

        name = etree.SubElement(Observing_System_Component, 'name')
        name.text = self.name.title()
        facility_type = etree.SubElement(
            Observing_System_Component, 'type')
        facility_type.text = 'Host'
        Internal_Reference = etree.SubElement(
            Observing_System_Component, 'Internal_Reference')
        lid_reference = etree.SubElement(
            Internal_Reference, 'lid_reference')
        lid_reference.text = self.lid
        reference_type = etree.SubElement(
            Internal_Reference, 'reference_type')
        reference_type.text = 'is_instrument_host'

        return label_root

        # # Get all xml labels in directory
        # xml_path_list = get_xml_path(bundle.directory())

        # # Traverse labels
        # for xml_path in xml_path_list:

        #     # Create Parser
        #     parser = etree.XMLParser(
        #         remove_blank_text=True, remove_comments=True)
        #     tree = etree.parse(xml_path, parser)
        #     label = open(xml_path, 'w')

        #     # Do what needs to be done
        #     root = tree.getroot()
        #     root_tag = etree.QName(root)

        #     if root_tag.localname is 'Product_Bundle' or 'Product_Collection':
        #         # Locate Observing System
        #         Observing_System = root[1][2]

        #         # Add Instrument Host to Observing System
        #         Observing_System_Component = etree.SubElement(
        #             Observing_System, 'Observing_System_Component')
        #         name = etree.SubElement(Observing_System_Component, 'name')
        #         name.text = self.name.title()
        #         insthost_type = etree.SubElement(
        #             Observing_System_Component, 'type')
        #         insthost_type.text = self.type_of
        #         Internal_Reference = etree.SubElement(
        #             Observing_System_Component, 'Internal_Reference')
        #         lid_reference = etree.SubElement(
        #             Internal_Reference, 'lid_reference')
        #         lid_reference.text = self.lid
        #         reference_type = etree.SubElement(
        #             Internal_Reference, 'reference_type')
        #         reference_type.text = 'is_instrument_host'

        #     # When the time comes, add in if tag.localname is 'Product_Document' and if tag.localname is 'Product_Collection' and use the same function to call for those.

        #     # Properly close file
        #     label_tree = etree.tostring(
        #         root, pretty_print=True, encoding='UTF-8', xml_declaration=True)
        #     label.write(label_tree.decode())
        #     label.close()

    def remove_xml(self, label_root):
        Context_Area = label_root.find('{}Context_Area'.format(NAMESPACE))
        if Context_Area is None:
            # This label never got a Context_Area, so there is nothing to take out.
            return label_root

        Observing_System = Context_Area.find('{}Observing_System'.format(NAMESPACE))
        if Observing_System is None:
            # Nothing was ever added, so there is nothing to take out.
            return label_root

        for component in Observing_System:
            if(component.tag == "{http://pds.nasa.gov/pds4/pds/v1}Observing_System_Component"):
                if(component[0].text == self.name.title()):
                    component.getparent().remove(component)

        return label_root


"""
14.2  Facility

Root Class:Tagged_NonDigital_Object
Role:Concrete

Class Description:The Facility class provides a name and address for a terrestrial observatory or laboratory.

Steward:pds
Namespace Id:pds
Version Id:1.0.0.0
          Entity         Card         Value/Class         Ind
Hierarchy        Tagged_NonDigital_Object                           
                 . TNDO_Context                           
                 . . Facility                           
Subclass        none                           
Attribute        address                0..1                  
                 country                0..1                  
                 description        0..1                  
                 name                0..1                  
                 type                1        Laboratory         
                                           Observatory         

Inherited Attribute        none                           
Association                data_object        1        Physical_Object         
Inherited Association        none                           

Referenced from        Product_Context                           
"""


# @python_2_unicode_compatible
class Facility(models.Model):
    FACILITY_TYPES = [
        ('Laboratory', 'Laboratory'),
        ('Observatory', 'Observatory'),
    ]

    # Relational attribute
    instruments = models.ManyToManyField(Instrument)
    # instruments = models.ManyToManyField('Instrument')

    investigations = models.ManyToManyField(Investigation)

    # Characteristic attributes
    # null=True's added 11/2022

    lid = models.CharField(max_length=MAX_LID_FIELD)
    name = models.CharField(max_length=MAX_CHAR_FIELD, null=True)
    type_of = models.CharField(
        max_length=MAX_CHAR_FIELD, choices=FACILITY_TYPES, null=True)
    version = models.FloatField(default=1.0)

    vid = models.FloatField(default=1.0)
    file_ref = models.CharField(max_length=MAX_CHAR_FIELD)

    # Accessors
    def name_lid_case(self):
        # Convert name to lower case
        name_edit = self.name.lower()
        # Convert spaces to underscores
        name_edit = replace_all(name_edit, ' ', '_')

    # Meta
    class Meta:
        ordering = ('name',)


    def __str__(self):
        return self.name

    def update_version(self, product_dict):
        self.vid = product_dict['vid']
        self.lid = product_dict['lid']
        self.file_ref = product_dict['url']
        self.save()

    def fill_label(self, label_root):
        Context_Area = label_root.find('{}Context_Area'.format(NAMESPACE)) # <- This is the issue

        Observing_System = context_container(Context_Area, 'Observing_System')

        # Add Facility to Observing System
        Observing_System_Component = etree.SubElement(
            Observing_System, 'Observing_System_Component')
        name = etree.SubElement(Observing_System_Component, 'name')
        name.text = self.name
        facility_type = etree.SubElement(
            Observing_System_Component, 'type')
        facility_type.text = self.type_of
        Internal_Reference = etree.SubElement(
            Observing_System_Component, 'Internal_Reference')
        lid_reference = etree.SubElement(
            Internal_Reference, 'lid_reference')
        lid_reference.text = self.lid
        reference_type = etree.SubElement(
            Internal_Reference, 'reference_type')
        reference_type.text = 'is_facility'

        return label_root

        # Get all xml labels in bundle directory
        # xml_path_list = get_xml_path(bundle.directory())

        # # Traverse labels
        # for xml_path in xml_path_list:
        #     print(xml_path)
        #     print(xml_path_list)

        #     fil = open(xml_path, 'r')

        #     fileText = fil.read()

        #     fil.close()

        #     print(fileText)

        #     # Create Parser
        #     parser = etree.XMLParser(
        #         remove_blank_text=True, remove_comments=True)
        #     tree = etree.parse(xml_path, parser)
        #     label = open(xml_path, 'w')

        #     # Do what needs to be done
        #     root = tree.getroot()
        #     root_tag = etree.QName(root)

        #     if root_tag.localname is 'Product_Bundle' or 'Product_Collection':
        #         # Locate Observing System
        #         Observing_System = root[1][2]

        #         # Add Facility to Observing System
        #         Observing_System_Component = etree.SubElement(
        #             Observing_System, 'Observing_System_Component')
        #         name = etree.SubElement(Observing_System_Component, 'name')
        #         name.text = self.name
        #         facility_type = etree.SubElement(
        #             Observing_System_Component, 'type')
        #         facility_type.text = self.type_of
        #         Internal_Reference = etree.SubElement(
        #             Observing_System_Component, 'Internal_Reference')
        #         lid_reference = etree.SubElement(
        #             Internal_Reference, 'lid_reference')
        #         lid_reference.text = self.lid
        #         reference_type = etree.SubElement(
        #             Internal_Reference, 'reference_type')
        #         reference_type.text = 'is_facility'

        #     # Properly close file
        #     label_tree = etree.tostring(
        #         root, pretty_print=True, encoding='UTF-8', xml_declaration=True)
        #     label.write(label_tree.decode())
        #     label.close()

    def remove_xml(self, label_root):
        Context_Area = label_root.find('{}Context_Area'.format(NAMESPACE))
        if Context_Area is None:
            # This label never got a Context_Area, so there is nothing to take out.
            return label_root

        Observing_System = Context_Area.find('{}Observing_System'.format(NAMESPACE))
        if Observing_System is None:
            # Nothing was ever added, so there is nothing to take out.
            return label_root

        for component in Observing_System:
            if(component.tag == "{http://pds.nasa.gov/pds4/pds/v1}Observing_System_Component"):
                if(component[0].text == self.name.title()):
                    component.getparent().remove(component)

        return label_root


"""
10.25  Mission_Area

Root Class:Product_Components
Role:Concrete

Class Description:The mission area allows the insertion of mission specific metadata.

Steward:pds
Namespace Id:pds
Version Id:1.0.0.0
          Entity         Card         Value/Class         Ind

Hierarchy        Product_Components                           
                . Mission_Area                           
Subclass                none                           
Attribute                none                           
Inherited Attribute        none                           
Association                none                           
Inherited Association        none                           
Referenced from        Context_Area                           
                 Observation_Area                           
"""


# @python_2_unicode_compatible
class Mission(models.Model):
    investigation = models.ManyToManyField(Investigation)
    name = models.CharField(max_length=MAX_CHAR_FIELD)

    # Accessors
    def name_lid_case(self):
        # Convert name to lower case
        name_edit = self.name.lower()
        # Convert spaces to underscores
        name_edit = replace_all(name_edit, ' ', '_')

    # Meta

    def __str__(self):
        return self.name


"""
Telescope                  
"""


class TelescopeManager(models.Manager):
    def update_version(self, product_dict):
        self.vid = product_dict['vid']
        self.lid = product_dict['lid']
        self.file_ref = product_dict['url']
        self.save()


# @python_2_unicode_compatible
class Telescope(models.Model):

    # Relational Attributes
    facilities = models.ManyToManyField(Facility)
    investigations = models.ManyToManyField(Investigation)

    # Attributes used for crawler
    # null=True's added 11/2022

    lid = models.CharField(max_length=MAX_LID_FIELD)
    name = models.CharField(max_length=MAX_CHAR_FIELD, null=True)
    vid = models.FloatField(default=1.0)
    file_ref = models.CharField(max_length=MAX_CHAR_FIELD)

    # Attributes used to manage Instrument Host object
    #objects = Instrument_HostManager()

    # Meta
    class Meta:
        ordering = ('name',)

    def __str__(self):
        return self.name

    def update_version(self, product_dict):
        self.vid = product_dict['vid']
        self.lid = product_dict['lid']
        self.file_ref = product_dict['url']
        self.save()

    def fill_label(self, label_root):
        # Use 'is None', not truthiness. An lxml element with no children is falsy.
        Context_Area = label_root.find('{}Context_Area'.format(NAMESPACE))
        if Context_Area is None:
            Context_Area = label_root.find('{}Observation_Area'.format(NAMESPACE))

        Observing_System = context_container(Context_Area, 'Observing_System')

        # Add Facility to Observing System
        Observing_System_Component = etree.SubElement(
            Observing_System, 'Observing_System_Component')
        name = etree.SubElement(Observing_System_Component, 'name')
        name.text = self.name
        facility_type = etree.SubElement(
            Observing_System_Component, 'type')
        # facility_type.text = self.type_of
        Internal_Reference = etree.SubElement(
            Observing_System_Component, 'Internal_Reference')
        lid_reference = etree.SubElement(
            Internal_Reference, 'lid_reference')
        lid_reference.text = self.lid
        reference_type = etree.SubElement(
            Internal_Reference, 'reference_type')
        reference_type.text = 'is_telescope'

        return label_root

        # Get all xml labels in bundle directory
        # xml_path_list = get_xml_path(bundle.directory())

        # # Traverse labels
        # for xml_path in xml_path_list:
        #     print(xml_path)

        #     # Create Parser
        #     parser = etree.XMLParser(
        #         remove_blank_text=True, remove_comments=True)
        #     tree = etree.parse(xml_path, parser)
        #     label = open(xml_path, 'w')

        #     # Do what needs to be done
        #     root = tree.getroot()
        #     root_tag = etree.QName(root)

        #     if root_tag.localname is 'Product_Bundle' or 'Product_Collection':
        #         # Locate Observing System
        #         Observing_System = root[1][2]

        #         # Add Facility to Observing System
        #         Observing_System_Component = etree.SubElement(
        #             Observing_System, 'Observing_System_Component')
        #         name = etree.SubElement(Observing_System_Component, 'name')
        #         name.text = self.name
        #         facility_type = etree.SubElement(
        #             Observing_System_Component, 'type')
        #         facility_type.text = self.type_of
        #         Internal_Reference = etree.SubElement(
        #             Observing_System_Component, 'Internal_Reference')
        #         lid_reference = etree.SubElement(
        #             Internal_Reference, 'lid_reference')
        #         lid_reference.text = self.lid
        #         reference_type = etree.SubElement(
        #             Internal_Reference, 'reference_type')
        #         reference_type.text = 'is_facility'

        #     # Properly close file
        #     label_tree = etree.tostring(
        #         root, pretty_print=True, encoding='UTF-8', xml_declaration=True)
        #     label.write(label_tree.decode())
        #     label.close()


"""
"""


# @python_2_unicode_compatible
class Bundle(models.Model):
    """
    Bundle has a many-one correspondance with User so a User can have multiple Bundles.
    Bundle name is currently not unique and we may want to ask someone whether or not it should be.
    If we require Bundle name to be unique, we could implement a get_or_create so multiple users
    can work on the same Bundle.  However we first must figure out how to click a Bundle and have it
    display the Build-A-Bundle app with form data pre-filled.  Not too sure how to go about this.
    """

    BUNDLE_STATUS = (
        ('b', 'Build'),
        ('r', 'Review'),
        ('s', 'Submit'),
    )

    bundle_type = models.CharField(max_length=12, default='Archive',)
    bundleID = models.CharField(max_length=MAX_CHAR_FIELD, verbose_name="Bundle ID", null=True, blank=True)
    name = models.CharField(max_length=MAX_CHAR_FIELD, unique=True)
    status = models.CharField(max_length=1, choices=BUNDLE_STATUS, blank=False, default='b')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    version = models.CharField(max_length=4)
    #version = models.ForeignKey(Version, on_delete=models.CASCADE, default=get_most_current_version())
    #raw_enum = models.PositiveIntegerField(null=True, default = 0)
    #calibrated_enum = models.PositiveIntegerField(null=True, default = 0)
    #derived_enum = models.PositiveIntegerField(null=True, default = 0)
   # Context Attributes
    investigations = models.ManyToManyField(Investigation)
    instrument_hosts = models.ManyToManyField(Instrument_Host)
    instruments = models.ManyToManyField(Instrument)
    targets = models.ManyToManyField(Target)
    facilities = models.ManyToManyField(Facility)
    telescopes = models.ManyToManyField(Telescope)
    submitted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True, null=True, blank=True)
    # Last label migration this bundle has had (build/label_migrations).
    label_revision = models.PositiveIntegerField(default=label_migrations_head)


    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'name'], name='unique_name_per_user')
        ]

    def save(self, *args, **kwargs):
        if not self.bundleID:
            # Auto-generate bundleID from bundle_name
            self.bundleID = self.name.lower().replace(" ", "_")
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    """
    - absolute_url
      Returns the url to continue the Build a Bundle process.
    """

    def absolute_url(self):
        return reverse('build:bundle', args=[smart_str(self.id)])

    """
    - directory
      Return the file path to the bundle.
    """

    def directory(self):
        bundle_directory = os.path.join(
            settings.ARCHIVE_DIR, self.user.username)
        bundle_directory = os.path.join(
            bundle_directory, self.name_directory_case())
        return bundle_directory


    def content_fingerprint(self):
        """A short digest of every file in this bundle as it is on disk.

        Used to tell if a validation result is still current. Can't use updated_at since most
        views that edit labels don't save the bundle. Files are stat-ed, not read, so large NetCDFs
        don't cost anything. Returns '' if the directory doesn't exist yet.
        """
        # Not cached on purpose. Anything in the same request could write a label and the walk is cheap.
        directory = self.directory()
        if not os.path.isdir(directory):
            return ''

        digest = hashlib.sha256()
        for dirpath, dirnames, filenames in os.walk(directory):
            # Sorted so the digest doesn't depend on filesystem order.
            dirnames.sort()
            for filename in sorted(filenames):
                path = os.path.join(dirpath, filename)
                try:
                    stat = os.stat(path)
                except OSError:
                    # A file that vanished mid-walk is a change too, so still record the name.
                    digest.update(os.path.relpath(path, directory).encode('utf-8'))
                    continue
                digest.update('{}|{}|{}'.format(
                    os.path.relpath(path, directory),
                    stat.st_size,
                    stat.st_mtime_ns).encode('utf-8'))
        return digest.hexdigest()

    def relative_dir(self):
        # rel_dir = os.path.join('archive/', self.user.username)
        rel_dir = os.path.join(self.user.username, self.name_directory_case())
        return rel_dir

    """
    - name_title_case
      Returns the bundle name in normal Title case with spaces.
    """

    def name_title_case(self):
        name_edit = self.name
        name_edit = replace_all(name_edit, '_', ' ')
        return pds_title_case(name_edit)

    """
    - name_directory_case
      Returns the bundle name in PDS4 compliant directory case with spaces.
      This is lid case with '_bundle' at the end.
    """

    def name_directory_case(self):

        # self.name is in title case with spaces
        name_edit = self.name

        # edit name to be in lower case
        name_edit = name_edit.lower()

        # edit name to have underscores where spaces are present
        name_edit = replace_all(name_edit, ' ', '_')

        # edit name to append _bundle at the end
        name_edit = '{0}_bundle'.format(name_edit)

        return name_edit

    """
    """

    def name_file_case(self):

        # Get bundle name in directory case: {name_of_bundle}_bundle
        name_edit = self.name_directory_case()

        # Remove _bundle
        name_edit = name_edit[:-7]

        # Now we are returning {name_of_bundle} where {name_of_bundle} is lowercase with underscores rather than spaces
        return name_edit

    """
    name_lid_case
         - Returns the name in proper lid case.
             - Maximum Length: 255 characters
             - Allowed characters: lower case letters, digits, dash, period, underscore
             - Delimiters are colons (So no delimiters in name).
    """

    def name_lid_case(self):
        return self.name_file_case()

    def lid(self):

        # if self.bundle_type == 'External':
        #     return 'urn:nasa:pds-ama:{0}'.format(self.name_lid_case()) 
        # else:
        #     return 'urn:{0}:{1}'.format(self.user.userprofile.agency, self.name_lid_case())

        if self.bundleID:
            bundle_id = self.bundleID.strip().lower().replace(' ', '_')
        else:
            bundle_id = self.name_lid_case()

        # The bundle name and ID are free text, so clean them up for the LID.
        bundle_id = pds_lid_segment(bundle_id)

        if self.bundle_type == 'External':
            return 'urn:nasa:pds-ama:{0}'.format(bundle_id)
        else:
            return 'urn:{0}:{1}'.format(self.user.userprofile.agency, bundle_id)

    """ 
        build_directory currently is not working.
    """

    def build_directory(self):
        user_path = os.path.join(settings.ARCHIVE_DIR, self.user.username)
        print(user_path)
        bundle_path = os.path.join(user_path, self.name_directory_case())
        make_directory(bundle_path)
        self.save()

    """
        remove_bundle removes the bundle directory and all of its contents from the user's directory.  If 
        the directory was removed, then the bundle model object is deleted from the ELSA database.  The 
        function then returns status true if everything was removed correctly.

    """

    def directory_is_contained(self):
        """Whether directory() resolves to somewhere strictly inside ARCHIVE_DIR/<owner>/."""
        owner_root = os.path.realpath(os.path.join(settings.ARCHIVE_DIR, self.user.username))
        path = os.path.realpath(self.directory())
        return os.path.commonpath([path, owner_root]) == owner_root and path != owner_root

    def remove_bundle(self):
        # Declarations
        debug_status = True
        complete_removal_status = False
        directory_removal_status = False
        model_removal_status = False

        if debug_status == True:
            print('-----------------------------')
            print('remove_bundle \n\n')
            print('bundle_directory: {}'.format(self.directory()))

        # Older rows may have a name like "../someone/x", so only delete if the path is really this owner's.
        if not self.directory_is_contained():
            logging.getLogger(__name__).warning(
                'remove_bundle: %r resolves outside %s\'s archive; files left in place',
                self.directory(), self.user.username)
        elif os.path.isdir(self.directory()):

            if debug_status == True:
                print('os.path.isdir(self.directory()): True')

            shutil.rmtree(self.directory())
            if not os.path.isdir(self.directory()):
                directory_removal_status = True

        # Should be no more than one
        if Bundle.objects.filter(name=self.name, user=self.user).count() > 0:
            b = Bundle.objects.filter(name=self.name, user=self.user)
            b.delete()
            if Bundle.objects.filter(name=self.name, user=self.user).count() == 0:
                model_removal_status = True

        if directory_removal_status and model_removal_status:
            complete_removal_status = True

        return complete_removal_status

    """
       update is used when a new label is being created for a product. Given a product, update will see which objects (whether that be other products or individual components of a label like an alias) are currently associated with the bundle and ensure all of the current metadata will be found on the new label being created for the given product.
    """

    def get_status(self):
        if self.submitted_at is not None:
            return 'submitted'
        # Requirements live in build.preflight. Imported here because preflight reads these models.
        from build import preflight
        return 'ready' if preflight.met(self) else 'in_progress'

    def update(self, product):

        # Components of labels
        #        alias_set = Alias.objects.filter(bundle=self)
        citation_information_set = Alias.objects.filter(bundle=self)
        modification_history_set = Alias.objects.filter(bundle=self)
        # context_set = Needs to be created still
        # modification_history_set --> Needs to be created still

        # Products
        document_set = document.objects.filter(bundle=self)
#        data_set = Data.objects.filter(data=self)

#        print alias_set
        print(citation_information_set)
        print(document_set)
        print(modification_history_set)
#        print data_set


"""
"""


# @python_2_unicode_compatible
class Collections(models.Model):
    # Attributes
    bundle = models.OneToOneField(Bundle, on_delete=models.CASCADE)
    has_data = models.BooleanField(default=False)
    has_document = True
    has_context = True
    has_xml_schema = True
    #has_raw_data = models.BooleanField(default=False)
    #has_calibrated_data = models.BooleanField(default=False)
    #has_derived_data = models.BooleanField(default=False)
    #data_enum = models.PositiveIntegerField(default = 0)
    
    # Cleaners

    def list(self):
        collections_list = []
        if self.has_document:
            collections_list.append("document")
        if self.has_context:
            collections_list.append("context")
        if self.has_xml_schema:
            collections_list.append("xml_schema")
        if self.has_data:
            collections_list.append("data")
        return collections_list

    #     Note: When we call on Collections, we want to be able to have a list of all collections
    #           pertaining to a bundle.

    def __str__(self):
       return '{0} Bundle has document={1}, context={2}, data={3}, xml_schema={4}'.format(self.bundle, self.has_document, self.has_context, self.has_data, self.has_xml_schema)
    #   return '{0} bundle.format{1}, document={2}, context={3}, data={4}'.format(self.bundle, self.has_context, self.has_document, self.has_data)
       
    class Meta(object):
        verbose_name_plural = 'Collections'        

    class Meta(object):
        verbose_name_plural = 'Collections'

    def build_directories(self):
        for collection in self.list():
            if collection != "data" and collection != "data_enum":
                collection_directory = os.path.join(
                    self.bundle.directory(), collection)
                make_directory(collection_directory)
                # We don't want the data collection to make a folder titled data.  Instead, the data
                # collection is a set of folders where the name(s) of these folders take on the form
                # data_<processing_level>.  For example: data_raw, data_calibrated, etc.  When a
                # user adds in a data product, we find out what type of data they have.  At this
                # point, ELSA will build the data_<processing_level> folder if it does not already
                # exist. The model object that creates the data collection folders is the Data model
                # object.

            if collection == "data": 
                self.build_data_directories(collection)
                # If no other data collections, data as a name is fine
                # If data exists fill in <name> in data_<name> through some means

    def build_data_directories(self, data):
        collection_directory = os.path.join(self.bundle.directory(), data)
        make_directory(collection_directory)


def _local_name(element):
    return etree.QName(element).localname if isinstance(element.tag, str) else ''


def bundle_member_entries(label_root):
    """Bundle_Member_Entry elements of a bundle label, as (element, lid_reference).

    Match on local name since new entries have no namespace and ones read from disk do.
    """
    entries = []
    for element in label_root:
        if _local_name(element) != 'Bundle_Member_Entry':
            continue
        lid = ''
        for child in element:
            if _local_name(child) == 'lid_reference':
                lid = (child.text or '').strip()
        entries.append((element, lid))
    return entries


def remove_bundle_member_entries(label_root, lid):
    """Remove every Bundle_Member_Entry naming this LID. Returns how many went."""
    removed = 0
    for element, entry_lid in bundle_member_entries(label_root):
        if entry_lid == lid:
            label_root.remove(element)
            removed += 1
    return removed


class AdditionalCollections(models.Model):
    #External Data Collections should only be of type external
    ADDITIONAL_COLLECTION_CHOICES = (
        ('External', 'External'),
        ('Data','Data'),
        ('Browse','Browse'),
        ('Geometry','Geometry'),
        ('Calibration','Calibration'),   
    )
    
    bundle = models.ForeignKey(Bundle, on_delete=models.CASCADE)
    collection_name = models.CharField(max_length=MAX_CHAR_FIELD)
    collection_type = models.CharField(max_length=MAX_CHAR_FIELD, choices=ADDITIONAL_COLLECTION_CHOICES, default='Data')
    collections_list = []

    def list(self):
        return self.collections_list

    def append_list(self):
        self.collection_name = self.collection_name.lower().replace(' ', '_')
        collection = [self.collection_name, self.collection_type]
        if collection not in self.collections_list:
            self.collections_list.append(collection)
        return self.collections_list

    def __str__(self):
       return self.collection_name
    #   return '{0} bundle.format{1}, document={2}, context={3}, data={4}'.format(self.bundle, self.has_context, self.has_document, self.has_data)
       
    class Meta(object):
        verbose_name_plural = 'AdditionalCollections'

    def build_directories(self):
        for collection in self.list():
            if collection[0] == self.collection_name: 
                collection_directory = os.path.join(self.bundle.directory(), collection[0])
                make_directory(collection_directory)
                # If no other data collections, data as a name is fine
                # If no other data collections, data as a name is fine
                # If data exists fill in <name> in data_<name> through some means    

    def save_collection(self):
        AdditionalCollections.objects.get_or_create(bundle_id=self.bundle.id)


    def directory(self):
        name_edit = self.collection_name.lower()
        collection_directory = os.path.join(self.bundle.directory(), name_edit)
        return collection_directory

    def name_label_case(self):
        bundle_id = self.bundle.bundleID.strip().lower().replace(' ', '_')
        collection_id = self.collection_name.lower().replace(' ', '_')

        return f'collection_{bundle_id}_{collection_id}.xml'
        # Append cleaned collection name to name edit for Product_Collection xml label
        # name_edit = self.collection_name.lower()
        # name_edit = 'collection_{}.xml'.format(name_edit)
        # return name_edit
        

    def bundle_member_lid(self):
        """The lid_reference this collection has in the bundle label, and its own LID."""
        return '{}:{}'.format(self.bundle.lid(), self.collection_name.lower())

    def remove_xml(self, label_root):
        """Remove this collection's entry from the bundle's member list.

        Called by delete_collection through remove_from_label.
        """
        remove_bundle_member_entries(label_root, self.bundle_member_lid())
        return label_root

    def label(self):
        return os.path.join(self.directory(), self.name_label_case())
    
    def build_base_case(self):
        # External collections use the External template, which has the AMA investigation.
        if self.bundle.bundle_type == 'External':
            source_file = os.path.join(PDS4_LABEL_TEMPLATE_DIRECTORY, 'base_templates')
            source_file = os.path.join(source_file, 'product_external_collection.xml')
        else:
            # Locate base case Product_Collection template found in templates/pds4_labels/base_case/
            source_file = os.path.join(PDS4_LABEL_TEMPLATE_DIRECTORY, 'base_case')
            source_file = os.path.join(source_file, 'product_collection.xml')

        # Locate collection directory and create path for new label
        label_file = os.path.join(self.directory(), self.name_label_case())

        #set selected version
        update = Version()
        bundle = Bundle()
        update.version_update_old(self.bundle.version, source_file,label_file)

        return

    def member_lidvids(self):
        """LIDVID of every NetCDF product in this collection, only files with a good label."""
        # Same as views._process_single_netcdf: bundle LID, collection directory, file name.
        # Use pds_lid_segment on both, the same way, so the inventory matches the labels.
        collection_segment = pds_lid_segment(os.path.basename(self.directory()))
        return ['{}:{}:{}::1.0'.format(self.bundle.lid(), collection_segment,
                                       pds_lid_segment(os.path.basename(nc.file.name)))
                for nc in self.netcdf_files.filter(processed=True).order_by('id')]

    def build_inventory(self):
        """Write this collection's inventory table and update its label. Safe to call again."""
        return write_collection_inventory(self.label(), self.member_lidvids())

    def fill_base_case(self, root):
        Product_Collection = root
         
        # Fill in Identification_Area
        Identification_Area = Product_Collection.find('{}Identification_Area'.format(NAMESPACE))


        #     lid
        
        logical_identifier = Identification_Area.find('{}logical_identifier'.format(NAMESPACE))

        # if self.bundle.bundle_type == "External":
        #     authority = "nasa:pds-ama"
        # else:
        #     authority = self.bundle.user.userprofile.agency

        # Just get the lid straight from the bundle since it's already formatted, has the authority, and alternative bundleID (if applicable).
        logical_identifier.text = f"{self.bundle.lid()}:{self.collection_name.lower()}"

        #logical_identifier.text = f'urn:{authority}:{self.bundle.name_lid_case()}:{self.collection_name}'

        # logical_identifier.text = 'urn:{0}:{1}:{2}'.format(self.bundle.user.userprofile.agency, self.bundle.name_lid_case(), self.collection_name) # where agency is something like nasa:pds
        

        #     version_id --> Note:  Can be changed to be more dynamic once we implement bundle versions (which is different from PDS4 versions)
        version_id = Identification_Area.find('{}version_id'.format(NAMESPACE))
        version_id.text = '1.0'  

        #     title
        title = Identification_Area.find('{}title'.format(NAMESPACE))
        title.text = collection_title(self.bundle.name_title_case(), self.collection_name)

        collection = Product_Collection.find('{}Collection'.format(NAMESPACE))
        col_type = collection.find('{}collection_type'.format(NAMESPACE))
        col_type.text = self.collection_type

        #     information_model_version
        #information_model_version = Identification_Area.find('{}information_model_version'.format(NAMESPACE))
        #information_model_version = self.bundle.version.name_with_dots()
        
        return Product_Collection



"""
@python_2_unicode_compatible
class Data(models.Model):
    PROCESSING_LEVEL_CHOICES = (            
        ('Calibrated', 'Calibrated'),
        ('Derived', 'Derived'),
        ('Raw', 'Raw'),
        ('Reduced', 'Reduced'),
    )
    name = models.CharField(max_length=256, blank=True)
    bundle = models.ForeignKey(Bundle, on_delete=models.CASCADE)
    processing_level = models.CharField(max_length=30, choices=PROCESSING_LEVEL_CHOICES, default='Raw',)
    data_enum = models.PositiveSmallIntegerField(default = 0,)


    class Meta:
        verbose_name_plural = 'Data'    


    def __str__(self):
        return 'Data associated'  # Better this once we work on data more


    # build_directory builds a directory of the form data_<processing_level>.  
    # Function make_directory(path) can be found in chocolate.py.  It checks the existence
    # of a directory before creating the directory.
    def build_directory(self):
        data_directory = os.path.join(self.bundle.directory(),'data_{}'.format(self.processing_level.lower()))
        make_directory(data_directory)


    # directory returns the file path associated with the given model.
    def directory(self):
        data_collection_name = 'data_{}'.format(self.processing_level.lower())
        data_directory = os.path.join(self.bundle.directory(), data_collection_name)
        return data_directory  


@python_2_unicode_compatible
class Data_Object(models.Model):
    DATA_TYPES = (
        ('Table Delimited','Table Delimited'),
        ('Table Binary','Table Binary'),
        ('Table Fixed-Width','Table Fixed-Width'),
        ('Array', 'Array'),
    )
    name=models.CharField(max_length=251)
    data_type = models.CharField(max_length=256,choices=DATA_TYPES, default='Table Delimited',)
    data = models.ForeignKey(Data, on_delete=models.CASCADE, null=True,)
    

    class Meta:
        verbose_name_plural = 'Data Object'

    def build_data_directory(self):
        data_directory = os.path.join(self.bundle.directory(), self.name)
        make_directory(data_directory)

    def label(self):
        return os.path.join(self.data.bundle.directory(), self.name)

    def build_base_case(self):
        pass

    # directory returns the file path associated with the given model.
    def directory(self):
        data_collection_name = 'data_{}'.format(self.data.processing_level.lower())
        data_directory = os.path.join(self.data.bundle.directory(), data_collection_name)
        return data_directory  

    def build_data_file(self):
        # Locate base case Product_Bundle template found in templates/pds4_labels/base_case/product_bundle
        source_file = os.path.join(settings.TEMPLATE_DIR, 'pds4_labels')
        source_file = os.path.join(source_file, 'base_templates')
        out_file = self.directory()
        
        if self.data_type == 'Table Delimited':
            source_file = os.path.join(source_file, 'data_table_delimited.xml')
            out_file = os.path.join(out_file, self.name + '.xml')

        elif self.data_type == 'Table Binary':
            source_file = os.path.join(source_file, 'table_binary.xml')
            out_file = os.path.join(out_file, self.name + '.xml')

        elif self.data_type == 'Table Fixed-Width':
            source_file = os.path.join(source_file, 'data_table_character.xml')
            out_file = os.path.join(out_file, self.name + '.xml')

        else:
            pass

        #set selected version
        update = Version()
        bundle = Bundle()
        print source_file + "<<<<<<<<"
        print self.data_type
        update.version_update(self.data.bundle.version, source_file,out_file)

        # Copy the base case template to the correct directory
        copy(source_file, self.label())
        
        return

    def __str__(self):
        return 'Data Prep'
    


"""


"""
Table and Field Objects

    -Table Objects belong to bundle objects. How many tables of what type is determined by the data prep
        object. The name atribute is also determined by data prep.

    -Field Objects belong to Table objects. Their quantity is deterined by the fields atribute of their 
        parent Table object.
"""
"""
@python_2_unicode_compatible
class Table_Delimited(models.Model):
    
    DELIMITER_CHOICES = (
        ('Comma','Comma'),
        ('Horizontal Tab','Horizontal Tab'),
        ('Semicolon','Semicolon'),
        ('Vertical Bar','Vertical Bar'),
    )

    name = models.CharField(max_length=256, blank=True)
    offset = models.IntegerField(default=-1)
    object_length = models.IntegerField(default=-1)
    description = models.CharField(max_length=5000, default="unset")
    records = models.IntegerField(default=-1)
    field_delimiter = models.CharField(max_length=256, choices=DELIMITER_CHOICES, default="Comma", blank=True)
    fields = models.IntegerField(default=-1)
    data = models.ForeignKey(Data, on_delete=models.CASCADE, null=True,)
    bundle = models.ForeignKey(Bundle, on_delete=models.CASCADE,null=True)

    def __str__(self):
        return smart_str(self.id)


@python_2_unicode_compatible
class Table_Binary(models.Model):
    name = models.CharField(max_length=256, blank=True)
    offset = models.IntegerField(default=-1)
    records = models.IntegerField(default=-1)
    fields = models.IntegerField(default=-1)
    data = models.ForeignKey(Data, on_delete=models.CASCADE, null=True,)
    bundle = models.ForeignKey(Bundle, on_delete=models.CASCADE, null=True)

    def __str__(self):
        return smart_str(self.id)

@python_2_unicode_compatible
class Table_Fixed_Width(models.Model):

    RECORD_CHOICES = (
        ('Sample Choice','Sample Choice'),
    )

    name = models.CharField(max_length=256, blank=True)
    offset = models.IntegerField(default=-1)
    object_length = models.IntegerField(default=-1)
    description = models.CharField(max_length=5000, default="unset")
    records = models.IntegerField(default=-1)
    record_delimiter = models.CharField(max_length=256, choices=RECORD_CHOICES, default="Sample Choice", blank=True)
    fields = models.IntegerField(default=-1)
    data = models.ForeignKey(Data, on_delete=models.CASCADE, null=True,)
    bundle = models.ForeignKey(Bundle, on_delete=models.CASCADE, null=True)

    def __str__(self):
        return smart_str(self.id)


@python_2_unicode_compatible
class Field_Delimited(models.Model):
    name = models.CharField(max_length=256)
    field_number = models.IntegerField()
    data_type = models.CharField(max_length=256)
    unit = models.CharField(max_length=256, null=True,)
    description = models.CharField(max_length=5000)
    table = models.ForeignKey(Table_Delimited, on_delete=models.CASCADE, null=True,)

    def __str__(self):
        pass

@python_2_unicode_compatible
class Field_Binary(models.Model):
    name = models.CharField(max_length=256)
    field_number = models.IntegerField()
    field_location = models.CharField(max_length=256)
    data_type = models.CharField(max_length=256)
    field_length = models.IntegerField()
    unit = models.CharField(max_length=256, null=True,)
    scaling_factor = models.IntegerField()
    value_offset = models.IntegerField()
    description = models.CharField(max_length=5000)
    table = models.ForeignKey(Table_Binary, on_delete=models.CASCADE, null=True,)

    def __str__(self):
        pass
    

@python_2_unicode_compatible
class Field_Character(models.Model):
    name = models.CharField(max_length=256)
    field_number = models.IntegerField()
    data_type = models.CharField(max_length=256)
    field_length = models.IntegerField()
    field_location = models.CharField(max_length=256)
    description = models.CharField(max_length=5000)
    table = models.ForeignKey(Table_Fixed_Width, on_delete=models.CASCADE, null=True,)

    def __str__(self):
        pass


"""

"""
15.1  Product_Bundle

Root Class:Product
Role:Concrete

Class Description:A Product_Bundle is an aggregate product and has a table of references to one or more collections.

Steward:pds
Namespace Id:pds

Version Id:1.1.0.0
          Entity         Card         Value/Class         Ind

Hierarchy        Product                           
                . Product_Bundle                           

Subclass                none                           
Attribute                none                           
Inherited Attribute        none                           
Association                context_area                0..1        Context_Area         
                         file_area                0..1        File_Area_Text         
                        member_entry                1..*        Bundle_Member_Entry         
                         product_data_object        1        Bundle         
                        reference_list                0..1        Reference_List         
Inherited Association        has_identification_area        1        Identification_Area         
Referenced from        none                           
"""


# @python_2_unicode_compatible
class Product_Bundle(models.Model):
    bundle = models.OneToOneField(Bundle, on_delete=models.CASCADE)

    def __str__(self):
        return '{}: Product Bundle'.format(self.bundle)

    def name_file_case(self):
        # Append bundle name in file case to name edit for a Product_Bundle xml label
        name_edit = 'bundle_{}.xml'.format(self.bundle.bundleID.strip().lower().replace(' ', '_'))
        return name_edit

    """
        label gives the physical location of the label on atmos (or wherever).  Since Product_Bundle is located within the bundle directory, our path is .../user_directory_here/bundle_directory_here/product_bundle_label_here.xml.
    """

    def label(self):
        return os.path.join(self.bundle.directory(), self.name_file_case())

    """
        build_base_case copies the base case product_bundle template (versionless) into bundle dir
    """

    def build_base_case(self):

        # Locate base case Product_Bundle template found in templates/pds4_labels/base_case/product_bundle
        source_file = os.path.join(settings.TEMPLATE_DIR, 'pds4_labels')
        source_file = os.path.join(source_file, 'base_case')
        if self.bundle.bundle_type == 'External':
            source_file = os.path.join(PDS4_LABEL_TEMPLATE_DIRECTORY, 'base_templates')
            source_file = os.path.join(source_file, 'product_external_bundle.xml')
        else:
            source_file = os.path.join(PDS4_LABEL_TEMPLATE_DIRECTORY, 'base_case')
            source_file = os.path.join(source_file, 'product_bundle.xml')

        # set selected version
        update = Version()
        bundle = Bundle()
        print(bundle.version + "test<<<<<<<<")
        update.version_update_old(self.bundle.version, source_file, self.label())

        # Copy the base case template to the correct directory
        #copyfile(source_file, self.label())

        return

    """
        fill_base_case is the initial fill given the bundle name, version, and collections.


        Fillers follow a set flow.
            1. Input the root element of an XML label.
                - We want the root because we can access all areas of the document through it's root.
            2. Find the areas you want to fill.
                - Always do find over a static search to ensure we are always on the right element.
                  (  ex. of static search ->    root[0] = Identification_Area in fill_base_case    )
                  Originally, ELSA used a static search for faster performance, but we found out
                  that comments in the XML label through the code off and we were pulling incorrect
                  elements.
            3. Fill those areas.
                - Fill is easy.  Just fill it.. with the information from the model it was called on,
                  self (like itself).
   
    """

    def fill_base_case(self, root):
        Product_Bundle = root

        # Fill in Identification_Area
        Identification_Area = Product_Bundle.find(
            '{}Identification_Area'.format(NAMESPACE))

        #     lid
        logical_identifier = Identification_Area.find(
            '{}logical_identifier'.format(NAMESPACE))
        logical_identifier.text = self.bundle.lid()

        #     version_id --> Note:  Can be changed to be more dynamic once we implement bundle versions (which is different from PDS4 versions)
        version_id = Identification_Area.find('{}version_id'.format(NAMESPACE))
        version_id.text = '1.0'

        #     title
        title = Identification_Area.find('{}title'.format(NAMESPACE))
        title.text = self.bundle.name_title_case()

        bundle_area = Product_Bundle.find('{}Bundle'.format(NAMESPACE))
        bundle_type = bundle_area.find('{}bundle_type'.format(NAMESPACE))
        bundle_type.text = self.bundle.bundle_type

        #     information_model_version
        #information_model_version = Identification_Area.find('{}information_model_version'.format(NAMESPACE))
        #information_model_version = self.bundle.version.name_with_dots()

        return Product_Bundle

    """
        build_internal_reference builds and fills the Internal_Reference information within the 
        Reference_List of Product_Bundle.  The relation is used within reference_type to associate what 
        the bundle is related to, like bundle_to_document.  Therefore, relation is a model object in 
        ELSA, like Document.  The possible relations as of V1A00 are errata, document, investigation, 
        instrument, instrument_host, target, resource, associate.
    """

    def build_internal_reference(self, root, relation):

        Reference_List = reference_list_element(root)

        # Create every element in the PDS namespace so the find() below can see them.
        Internal_Reference = etree.SubElement(
            Reference_List, '{}Internal_Reference'.format(NAMESPACE))

        lid_reference = etree.SubElement(
            Internal_Reference, '{}lid_reference'.format(NAMESPACE))
        lid_reference.text = relation.lid()

        reference_type = etree.SubElement(
            Internal_Reference, '{}reference_type'.format(NAMESPACE))
        reference_type.text = 'bundle_to_{}'.format(relation.reference_type())

        return root


#    def base_case(self):
#        return


    def build_bundle_member_entry(self, root, collection):
        """
        build_internal_reference builds and fills the Internal_Reference information within the 
        Reference_List of Product_Bundle.  The relation is used within reference_type to associate what 
        the bundle is related to, like bundle_to_document.  Therefore, relation is a model object in 
        ELSA, like Document.  The possible relations as of V1A00 are errata, document, investigation, 
        instrument, instrument_host, target, resource, associate.
        """
        print('---DEBUG---')
        print('Root: {}'.format(root))

        Bundle_Member_Entry = etree.SubElement(root, 'Bundle_Member_Entry')

        lid_reference = etree.SubElement(Bundle_Member_Entry, 'lid_reference')
        lid_reference.text = '{}:{}'.format(
            self.bundle.lid(), collection.collection.lower())

        member_status = etree.SubElement(Bundle_Member_Entry, 'member_status')
        member_status.text = 'Primary'


        # Reference type has to match the declared <collection_type>, so get it from the collection.
        # External document collections are bundle_has_external_collection.
        reference_type = etree.SubElement(Bundle_Member_Entry, 'reference_type')
        reference_type.text = collection.reference_type()

        return root

    def build_additional_bundle_member_entry(self, root, collection):
        """
        build_internal_reference builds and fills the Internal_Reference information within the 
        Reference_List of Product_Bundle.  The relation is used within reference_type to associate what 
        the bundle is related to, like bundle_to_document.  Therefore, relation is a model object in 
        ELSA, like Document.  The possible relations as of V1A00 are errata, document, investigation, 
        instrument, instrument_host, target, resource, associate.
        """
        print('---DEBUG---')
        print('Root: {}'.format(root))

        
        lid = '{}:{}'.format(self.bundle.lid(), collection.collection_name.lower())

        # Once per collection. PDS rejects a bundle that lists a member twice.
        if any(entry_lid == lid for _element, entry_lid in bundle_member_entries(root)):
            return root

        Bundle_Member_Entry = etree.SubElement(root, 'Bundle_Member_Entry')

        lid_reference = etree.SubElement(Bundle_Member_Entry, 'lid_reference')
        lid_reference.text = lid

        member_status = etree.SubElement(Bundle_Member_Entry, 'member_status')
        member_status.text = 'Primary'

        reference_type = etree.SubElement(Bundle_Member_Entry, 'reference_type')
        reference_type.text = 'bundle_has_{}_collection'.format(collection.collection_type.lower())   

        return root



    def base_case(self):
        return


"""
15.2  Product_Collection

Root Class:Product
Role:Concrete


Class Description:A Product_Collection has a table of references to one or more basic products. The references are stored in a table called the inventory.


Steward:pds
Namespace Id:pds
Version Id:1.1.0.0
          Entity         Card         Value/Class         Ind


Hierarchy        Product                           
                 . Product_Collection                           


Subclass                none                           
Attribute                none                           
Inherited Attribute        none                           


Association        context_area                0..1        Context_Area         
                file_area_inventory        1        File_Area_Inventory         
                product_data_object        1        Collection         
                 reference_list                0..1        Reference_List         


Inherited Association        has_identification_area        1        Identification_Area         


Referenced from        none                           
"""


# @python_2_unicode_compatible
class Product_Collection(models.Model):
    # Stored key on the left, display name on the right. The key is used for the directory,
    # LID segment and label filename, so it stays underscored.
    COLLECTION_CHOICES = (

        ('Document', 'Document'),
        ('Context', 'Context'),
        ('XML_Schema', 'XML Schema'),
        ('Data', 'Data'),
        ('Browse', 'Browse'),
        ('Geometry', 'Geometry'),
        ('Calibration', 'Calibration'),
        ('Not_Set', 'Not Set'),

    )
    bundle = models.ForeignKey(Bundle, on_delete=models.CASCADE)

    collection = models.CharField(max_length=MAX_CHAR_FIELD, choices=COLLECTION_CHOICES)

    # The stored `collection` value is our internal key. <collection_type> needs one of the
    # Collection/type values from IM v1.24.0.0 (1O00): Browse, Calibration, Context, Data,
    # Document, External, Geometry, Miscellaneous, SPICE Kernel, XML Schema.
    PDS4_COLLECTION_TYPES = {
        'XML_Schema': 'XML Schema',
    }

    # reference_type comes from the collection type, but two types need a different spelling.
    PDS4_REFERENCE_TYPES = {
        'XML Schema': 'bundle_has_schema_collection',
        'SPICE Kernel': 'bundle_has_spice_kernel_collection',
    }

    def collection_type(self):
        """The Collection/type for this collection's label.

        External (AMA) bundles use urn:nasa:pds-ama LIDs, which the Document type doesn't allow,
        so every collection in an External bundle is External, including the document one.
        """
        if self.bundle.bundle_type == 'External':
            return 'External'
        return self.PDS4_COLLECTION_TYPES.get(self.collection, self.collection)

    def reference_type(self):
        """The Bundle_Member_Entry/reference_type naming this collection from the bundle label."""
        collection_type = self.collection_type()
        return self.PDS4_REFERENCE_TYPES.get(
            collection_type,
            'bundle_has_{}_collection'.format(collection_type.lower()))
    
#    def __str__(self):

#        return "{0}\nProduct Collection for {1} Collection".format(self.collections.bundle, self.collection)
    

    def lid(self):
        """Builds lid for collection
        """
        if self.collection != 'Data':
            collection_lid = '{0}:{1}'.format(
                self.bundle.lid, self.collection.lower())
        else:
            collection_lid = '{0}:data_<DATA_TYPE_HERE>'.format(
                self.bundle.lid)
        return collection_lid

    """
        This returns the directory path of all collections but the data collection.
        To return any of the data collection directory paths, see directory_data.
    """

    def directory(self):
        name_edit = self.collection.lower()
        collection_directory = os.path.join(self.bundle.directory(), name_edit)
        return collection_directory

    def directory_data(self, data):
        collection_directory = os.path.join(
            self.bundle.directory(), data.get_directory_name())
# Jacob's        name_edit = '{0}_{1}'.format(self.collection.lower(), data.processing_level.lower())
#        collection_directory = os.path.join(self.bundle.directory(), name_edit)
        return collection_directory

    """
       name_label_case returns the name in label case with the proper .xml extension.

    """

    def name_label_case(self):

        # Append cleaned collection name to name edit for Product_Collection xml label
        bundle_id = self.bundle.bundleID.strip().lower().replace(' ', '_')
        name_edit = self.collection.lower()
        return f'collection_{bundle_id}_{name_edit}.xml'
        #name_edit = 'collection_{}_{}.xml'.format(bundle_id, name_edit)   
        #return name_edit

    def name_label_case_data(self, data):
        # Append cleaned collection name to name edit for Product_Collection xml label
        name_edit = '{0}_{1}.xml'.format(
            self.collection.lower(), data.processing_level.lower())
        return name_edit

    """
       label returns the physical label location in ELSAs archive
    """

    def label(self):
        if self.collection == 'Data':
            return os.path.join(self.directory(), self.name_label_case())                        # Need to fix with new data collection changes
        else:
            return os.path.join(self.directory(), self.name_label_case())

    """
    """

    def build_base_case(self):

        # Locate base case Product_Collection template found in templates/pds4_labels/base_case/
        
        if self.bundle.bundle_type == 'External':
            source_file = os.path.join(PDS4_LABEL_TEMPLATE_DIRECTORY, 'base_templates')
            source_file = os.path.join(source_file, 'product_external_collection.xml')
        else:
            source_file = os.path.join(PDS4_LABEL_TEMPLATE_DIRECTORY, 'base_case')
            source_file = os.path.join(source_file, 'product_collection.xml')

        # Locate collection directory and create path for new label
        label_file = os.path.join(self.directory(), self.name_label_case())

        # set selected version
        update = Version()
        bundle = Bundle()

        update.version_update_old(self.bundle.version, source_file,label_file)


        # Copy the base case template to the correct directory
        #copyfile(source_file, label_file)

        return

    def member_lidvids(self):
        """LIDVID of every product in this collection, for the inventory table.

        Only the Document collection has products we track right now.
        """
        if self.collection != 'Document':
            return []
        return ['{}::1.0'.format(document.lid())
                for document in Product_Document.objects.filter(bundle=self.bundle)]

    def build_inventory(self):
        """Write this collection's inventory table and update its label. Safe to call again."""
        return write_collection_inventory(self.label(), self.member_lidvids())


    def build_base_case_data(self, data):

        # Locate base case Product_Collection template found in templates/pds4_labels/base_case/
        source_file = os.path.join(PDS4_LABEL_TEMPLATE_DIRECTORY, 'base_case')
        source_file = os.path.join(source_file, 'product_collection.xml')

        # Locate collection directory and create path for new label
        label_file = os.path.join(self.directory_data(
            data), self.name_label_case_data(data))

        # Copy the base case template to the correct directory if it does not already exist
        if not os.path.exists(label_file):
            copyfile(source_file, label_file)

        return

    """
        Fillers follow a set flow.
            1. Input the root element of an XML label.
                - We want the root because we can access all areas of the document through it's root.
            2. Find the areas you want to fill.
                - Always do find over a static search to ensure we are always on the right element.
                  (  ex. of static search ->    root[0] = Identification_Area in fill_base_case    )
                  Originally, ELSA used a static search for faster performance, but we found out
                  that comments in the XML label through the code off and we were pulling incorrect
                  elements.
            3. Fill those areas.
                - Fill is easy.  Just fill it.. with the information from the model it was called on,
                  self (like itself).
    """

    def fill_base_case(self, root):
        if self.collection == 'Data':
            pass
        Product_Collection = root

        # Fill in Identification_Area
        Identification_Area = Product_Collection.find(
            '{}Identification_Area'.format(NAMESPACE))

        #     lid
        logical_identifier = Identification_Area.find(
            '{}logical_identifier'.format(NAMESPACE))
        
        if self.bundle.bundle_type == "External":
            authority = "nasa:pds-ama"
        else:
            authority = self.bundle.user.userprofile.agency

        logical_identifier.text = f'{self.bundle.lid()}:{self.collection.lower()}'

        #logical_identifier.text = f'urn:{authority}:{self.bundle.name_lid_case()}:{self.collection.lower()}'
        # logical_identifier.text = 'urn:{0}:{1}:{2}'.format(self.bundle.user.userprofile.agency, self.bundle.name_lid_case(
        # ), self.collection.lower())  # where agency is something like nasa:pds

        #     version_id --> Note:  Can be changed to be more dynamic once we implement bundle versions (which is different from PDS4 versions)
        version_id = Identification_Area.find('{}version_id'.format(NAMESPACE))
        version_id.text = '1.0'

        #     title
        title = Identification_Area.find('{}title'.format(NAMESPACE))
        title.text = collection_title(self.bundle.name_title_case(), self.collection)

        collection = Product_Collection.find('{}Collection'.format(NAMESPACE))
        col_type = collection.find('{}collection_type'.format(NAMESPACE))
        col_type.text = self.collection_type()

        #     information_model_version
        #information_model_version = Identification_Area.find('{}information_model_version'.format(NAMESPACE))
        #information_model_version = self.bundle.version.name_with_dots()

        return Product_Collection

    """
        build_internal_reference builds and fills the Internal_Reference information within the Reference_List of Product_Collection.  The relation is used within reference_type to associate what the collection is related to, like collection_to_document.  Therefore, relation is a model object in ELSA, like Document.  The possible relations as of V1A00 are resource, associate, calibration, geometry, spice kernel, document, browse, context, data, ancillary, schema, errata, bundle, personnel, investigation, instrument, instrument_host, target.
    """

    def build_internal_reference(self, root, relation):

        Reference_List = reference_list_element(root)

        Internal_Reference = etree.SubElement(
            Reference_List, 'Internal_Reference')

        lid_reference = etree.SubElement(Internal_Reference, 'lid_reference')
        lid_reference.text = relation.lid()

        reference_type = etree.SubElement(Internal_Reference, 'reference_type')
        reference_type.text = 'collection_to_{}'.format(
            relation.reference_type())

        return root

    # Meta
    def __str__(self):

        return "{0}: Product Collection for {1} Collection".format(self.bundle, self.collection)


"""
"""


# @python_2_unicode_compatible
class Data(models.Model):
    PROCESSING_LEVEL_CHOICES = (
        ('Calibrated', 'Calibrated'),
        ('Derived', 'Derived'),
        ('Raw', 'Raw'),
        ('Reduced', 'Reduced'),
    )
    
    DATA_TYPES = (
        #('Array', 'Array'),    
        ('Table Binary','Table Binary'),
        ('Table Character','Table Character'),
        ('Table Delimited','Table Delimited'),
    )

    bundle = models.ForeignKey(Bundle, on_delete=models.CASCADE)
    name = models.CharField(max_length=MAX_CHAR_FIELD)
    processing_level = models.CharField(max_length=30, choices=PROCESSING_LEVEL_CHOICES, default='Raw')
    data_type = models.CharField(max_length=256,choices=DATA_TYPES, default = '')
    header = models.BooleanField(default=False)

    collection = models.ForeignKey(AdditionalCollections, on_delete = models.CASCADE, default='',)

    class Meta(object):
        verbose_name_plural = 'Data'

    def __str__(self):

        return self.name  # Better this once we work on data more

    # get_directory_name returns the name of the directory for this data object.
    # def get_directory_name(self):
    #     # Edit name for directory
    #     # replace all spaces with underscores
    #     name = replace_all(self.name.lower(), ' ', '_')
    #     return 'data_{}_{}'.format(self.processing_level.lower(), name)

    # build_directory builds a directory of the form data_<processing_level>.
    # Function make_directory(path) can be found in chocolate.py.  It checks the existence
    # of a directory before creating the directory.

    def build_base_file(
        self,
        *,
        start_date_time=None,
        stop_date_time=None,
        start_date_time_nil_reason='',
        stop_date_time_nil_reason='',
    ):
        source_file = os.path.join(
            settings.TEMPLATE_DIR, 'pds4_labels', 'base_templates'
        )

        data_type = replace_all(self.data_type.lower(), ' ', '_')
        if self.header:
            template_name = 'data_{}_with_header.xml'.format(data_type)
        else:
            template_name = 'data_{}.xml'.format(data_type)

        source_file = os.path.join(source_file, template_name)

        ret_name = replace_all(self.name.lower(), ' ', '_')
        out_file = os.path.join(self.collection.directory(), ret_name + '.xml')

        update = Version()
        update.version_update_old(self.bundle.version, source_file, out_file)

        if (
            start_date_time is None
            and stop_date_time is None
            and not start_date_time_nil_reason
            and not stop_date_time_nil_reason
        ):
            return

        label_object, label_root, label_metadata = open_label_with_tree(out_file)
        time_coordinates = label_root.find(
            f'{NAMESPACE}Observation_Area/{NAMESPACE}Time_Coordinates'
        )
        xsi_nil = '{http://www.w3.org/2001/XMLSchema-instance}nil'

        for field_name, timestamp, nil_reason in (
            ('start_date_time', start_date_time, start_date_time_nil_reason),
            ('stop_date_time', stop_date_time, stop_date_time_nil_reason),
        ):
            element = time_coordinates.find(f'{NAMESPACE}{field_name}')

            if nil_reason:
                element.text = None
                element.set(xsi_nil, 'true')
                element.set('nilReason', nil_reason)
            elif timestamp is not None:
                element.text = timestamp.isoformat().replace('+00:00', 'Z')
                element.attrib.pop(xsi_nil, None)
                element.attrib.pop('nilReason', None)

        close_label(label_object, label_root, label_metadata)

    def build_directory(self):
        data_name = self.name.lower()
        data_name = replace_all(data_name, ' ', '_')
        data_directory = os.path.join(self.collection.directory(), data_name)
        make_directory(data_directory)

    def label(self):
        return os.path.join(self.collection.directory(), self.name)

    def build_base_case(self):
        pass



"""
Table and Field Objects

    -Table Objects belong to bundle objects. How many tables of what type is determined by the data prep
        object. The name atribute is also determined by data prep.

    -Field Objects belong to Table objects. Their quantity is deterined by the fields atribute of their 
        parent Table object.
"""


# @python_2_unicode_compatible
class Table_Delimited(models.Model):
    DELIMITER_CHOICES = (
        ('Comma', 'Comma'),
        ('Horizontal Tab', 'Horizontal Tab'),
        ('Semicolon', 'Semicolon'),
        ('Vertical Bar', 'Vertical Bar'),
    )
    PRIMARY_RESULTS_SUMMARY_FACET_CHOICES = (
        ('Meteorology', 'Meteorology'),
        ('Structure', 'Structure')
    )

    name = models.CharField(max_length=256, blank=True)
    offset = models.IntegerField(default=0)
    object_length = models.IntegerField(default=1)
    description = models.CharField(max_length=5000, default="unset")
    records = models.IntegerField(default=1)
    field_delimiter = models.CharField(max_length=256, choices=DELIMITER_CHOICES, default="Comma", blank=True)
    fields = models.IntegerField(default=1)
    facet1 = models.CharField(max_length=256, choices=PRIMARY_RESULTS_SUMMARY_FACET_CHOICES, default="Meteorology", blank=True)
    data = models.ForeignKey(Data, on_delete=models.CASCADE, null=True)
    collection = models.ForeignKey(AdditionalCollections, on_delete = models.CASCADE, default='',)
    bundle = models.ForeignKey(Bundle, on_delete=models.CASCADE, null=True)

    def name_label_case(self):
        """
            This could be improved to ensure disallowed characters for a file name are not contained
            in name.
        """
        name_edit = self.data.name.lower()
        name_edit = replace_all(name_edit, ' ', '_')
        name_edit = '{}.xml'.format(name_edit)
        return name_edit

    def label(self):
        """
            label returns the physical label location in ELSAs archive
        """
        print(os.path.join(self.collection.directory(), self.name_label_case()))
        return os.path.join(self.collection.directory(), self.name_label_case())

        # directory returns the file path associated with the given model.
    def directory(self):
        # data_collection_name = self.collection.collection_name.lower()
        data_name = self.data.name.lower()
        data_name = replace_all(data_name, ' ', '_')
        data_directory = os.path.join(self.collection.directory(), data_name)
        return data_directory

    def build_data_file(self):
        # Locate base case Product_Bundle template found in templates/pds4_labels/base_case/product_bundle
        source_file = os.path.join(settings.TEMPLATE_DIR, 'pds4_labels')
        source_file = os.path.join(source_file, 'base_templates')
        
        source_file = os.path.join(source_file, 'data_table_delimited.xml')

        ret_name = self.data.name.lower()
        ret_name = replace_all(ret_name, ' ', '_')
        out_file = os.path.join(self.directory(), ret_name + '.xml')

        #set selected version
        update = Version()
        bundle = Bundle()
        print (source_file + "<<<<<<<<")
        print(out_file)

        update.version_update_old(self.data.bundle.version, source_file,out_file)
        
    def fill_base_case(self, root, cleaned_form):

        Table_Delimited = root

        # Fill in Identification_Area
        print("id area")
        Identification_Area = Table_Delimited.find('{}Identification_Area'.format(NAMESPACE))

        # lid
        logical_identifier = Identification_Area.find('{}logical_identifier'.format(NAMESPACE))
        logical_identifier.text = 'urn:{0}:{1}:{2}:{3}'.format(self.data.bundle.user.userprofile.agency, self.data.bundle.name_lid_case(), self.collection.collection_name.lower(), replace_all(self.name.lower(), ' ', '_')) # where agency is something like nasa:pds

        print("version")
        version_id = Identification_Area.find('{}version_id'.format(NAMESPACE))
        version_id.text = '1.0'  # Can make this better

        print("title")
        title = Identification_Area.find('{}title'.format(NAMESPACE))
        title.text = self.name

        print("info model")
        information_model_version = Identification_Area.find('information_model_version')
        #information_model_version.text = self.bundle.version.with_dots()

        Observation_Area = Table_Delimited.find('{}Observation_Area'.format(NAMESPACE))
        Primary_Result_Summary = Observation_Area.find('{}Primary_Result_Summary'.format(NAMESPACE))
        Science_Facets = Primary_Result_Summary.find('{}Science_Facets'.format(NAMESPACE))
        science_facet1 = Science_Facets.find('{}facet1'.format(NAMESPACE))
        science_facet1.text = self.facet1

        f = Table_Delimited.find('{}File_Area_Observational'.format(NAMESPACE))

        if self.data.header:
            header = f.find('{}Header'.format(NAMESPACE))
            local_id = header.find('{}local_identifier'.format(NAMESPACE))
            local_id.text = cleaned_form.get('local_identifier')

            offset = header.find('{}offset'.format(NAMESPACE))
            offset.text = '0' # str(cleaned_form.get('header_offset'))

            object_length = header.find('{}object_length'.format(NAMESPACE))
            object_length.text = str(cleaned_form.get('header_object_length'))

        td = f.find('{}Table_Delimited'.format(NAMESPACE))
        
        if self.name:
            name = td.find('{}name'.format(NAMESPACE))
            name.text = self.name

        # Probably change this to have it be part of a form
        local_identifier = td.find('{}local_identifier'.format(NAMESPACE))
        local_identifier.text = 'table'

        # if self.offset:
        offset = td.find('{}offset'.format(NAMESPACE))

        if self.data.header:
            print('within offset with header')
            offset.text = str(cleaned_form.get('header_object_length')) # str(cleaned_form.get('header_offset') + self.offset)
        else:
            offset.text = str(self.offset)
        
        if self.object_length:
            object_length = td.find('{}object_length'.format(NAMESPACE))
            object_length.text = str(self.object_length)

        if self.description:
            description = td.find('{}description'.format(NAMESPACE))
            description.text = self.description

        if self.records:
            records = td.find('{}records'.format(NAMESPACE))
            records.text = str(self.records)
        
        if self.field_delimiter:
            field_delimiter = td.find('{}field_delimiter'.format(NAMESPACE))
            field_delimiter.text = self.field_delimiter

        rd = td.find('{}Record_Delimited'.format(NAMESPACE))

        fields = rd.find('{}fields'.format(NAMESPACE))
        fields.text = str(self.fields)

        # should create a form to ask for this, but will default to 0
        groups = rd.find('{}groups'.format(NAMESPACE))
        groups.text = '0'

        # field_delimited = rd.find('{}Field_Delimited'.format(NAMESPACE))
        existing = rd.findall('{}Field_Delimited'.format(NAMESPACE))
        field_delimited = existing[0]
        insert_at = list(rd).index(field_delimited)
        for extra in existing[1:]:
            rd.remove(extra)

        for i in range(int(self.fields) - 1):
            cloned_file = copy.deepcopy(field_delimited)
            # rd.append(cloned_file)
            rd.insert(insert_at + i + 1, cloned_file)
        
        return root

    def fill_label_values(self, label_root, cleaned_form):
        f = label_root.find('{}File_Area_Observational'.format(NAMESPACE))
        td = f.find('{}Table_Delimited'.format(NAMESPACE))
        rd = td.find('{}Record_Delimited'.format(NAMESPACE))

        fields = rd.findall('{}Field_Delimited'.format(NAMESPACE))

        # field_count = 0

        # for field in fields:
        for field_count, field in enumerate(fields):
            name = field.find('{}name'.format(NAMESPACE)) 
            name.text = cleaned_form.get(f'name_{field_count}')
            field_number = field.find('{}field_number'.format(NAMESPACE))
            # field_number.text = str(cleaned_form.get(f'field_number_{field_count}'))
            field_number.text = str(field_count + 1)
            data_type = field.find('{}data_type'.format(NAMESPACE)) 
            data_type.text = cleaned_form.get(f'data_type_{field_count}')
            description = field.find('{}description'.format(NAMESPACE)) 
            description.text = str(cleaned_form.get(f'description_{field_count}'))

        return label_root

    def __str__(self):
        return smart_str(self.id)


# @python_2_unicode_compatible
class Table_Binary(models.Model):
    PRIMARY_RESULTS_SUMMARY_FACET_CHOICES = (
        ('Meteorology', 'Meteorology'),
        ('Structure', 'Structure')
    )

    name = models.CharField(max_length=256, blank=True)
    offset = models.IntegerField(default=1)
    records = models.IntegerField(default=1)
    fields = models.IntegerField(default=1)
    facet1 = models.CharField(max_length=256, choices=PRIMARY_RESULTS_SUMMARY_FACET_CHOICES, default="Meteorology", blank=True)
    data = models.ForeignKey(Data, on_delete=models.CASCADE, null=True)
    collection = models.ForeignKey(AdditionalCollections, on_delete = models.CASCADE, default='',)
    bundle = models.ForeignKey(Bundle, on_delete=models.CASCADE, null=True,)

    def name_label_case(self):
        name_edit = self.data.name.lower()
        name_edit = replace_all(name_edit, ' ', '_')
        name_edit = '{}.xml'.format(name_edit)
        return name_edit

    def label(self):
        """
            label returns the physical label location in ELSAs archive
        """
        print(os.path.join(self.collection.directory(), self.name_label_case()))
        return os.path.join(self.collection.directory(), self.name_label_case())

        # directory returns the file path associated with the given model.
    def directory(self):
        # data_collection_name = self.collection.collection_name.lower()
        data_name = self.data.name.lower()
        data_name = replace_all(data_name, ' ', '_')
        data_directory = os.path.join(self.collection.directory(), data_name)
        return data_directory

    def build_data_file(self):
        # Locate base case Product_Bundle template found in templates/pds4_labels/base_case/product_bundle
        source_file = os.path.join(settings.TEMPLATE_DIR, 'pds4_labels')
        source_file = os.path.join(source_file, 'base_templates')
        source_file = os.path.join(source_file, 'table_binary.xml')

        ret_name = self.data.name.lower()
        ret_name = replace_all(ret_name, ' ', '_')
        out_file = os.path.join(self.directory(), ret_name + '.xml')

        #set selected version
        update = Version()
        bundle = Bundle()

        update.version_update_old(self.data.bundle.version, source_file,out_file)
        
    def fill_base_case(self, root, cleaned_form):
            
        Table_Binary = root
        
        # Fill in Identification_Area
        print("id area")
        Identification_Area = Table_Binary.find('{}Identification_Area'.format(NAMESPACE))

        # lid
        logical_identifier = Identification_Area.find('{}logical_identifier'.format(NAMESPACE))
        logical_identifier.text = 'urn:{0}:{1}:{2}:{3}'.format(self.data.bundle.user.userprofile.agency, self.data.bundle.name_lid_case(), self.collection.collection_name.lower(), replace_all(self.name.lower(), ' ', '_')) # where agency is something like nasa:pds

        print("version")
        version_id = Identification_Area.find('{}version_id'.format(NAMESPACE))
        version_id.text = '1.0'  # Can make this better

        print("title")
        title = Identification_Area.find('{}title'.format(NAMESPACE))
        title.text = self.name

        print("info model")
        information_model_version = Identification_Area.find('information_model_version')
        #information_model_version.text = self.bundle.version.with_dots()

        Observation_Area = Table_Binary.find('{}Observation_Area'.format(NAMESPACE))
        Primary_Result_Summary = Observation_Area.find('{}Primary_Result_Summary'.format(NAMESPACE))
        Science_Facets = Primary_Result_Summary.find('{}Science_Facets'.format(NAMESPACE))
        science_facet1 = Science_Facets.find('{}facet1'.format(NAMESPACE))
        science_facet1.text = self.facet1

        f = Table_Binary.find('{}File_Area_Observational'.format(NAMESPACE))

        if self.data.header:
            header = f.find('{}Header'.format(NAMESPACE))
            local_id = header.find('{}local_identifier'.format(NAMESPACE))
            local_id.text = cleaned_form.get('local_identifier')

            offset = header.find('{}offset'.format(NAMESPACE))
            offset.text = '0' # str(cleaned_form.get('header_offset'))

            object_length = header.find('{}object_length'.format(NAMESPACE))
            object_length.text = str(cleaned_form.get('header_object_length'))

        tb = f.find('{}Table_Binary'.format(NAMESPACE))
        rb = tb.find('{}Record_Binary'.format(NAMESPACE))
        if self.name:
            name = tb.find('{}name'.format(NAMESPACE))
            name.text = self.name

        # if self.offset:
        offset = tb.find('{}offset'.format(NAMESPACE))
            # offset.text = str(self.offset)

        if self.data.header:
            print('within offset with header')
            offset.text = str(cleaned_form.get('header_object_length')) # str(cleaned_form.get('header_offset') + self.offset)
        else:
            offset.text = str(self.offset)

        if self.records:
            records = tb.find('{}records'.format(NAMESPACE))
            records.text = str(self.records)
        
        if self.fields:
            fields = rb.find('{}fields'.format(NAMESPACE))
            fields.text = str(self.fields)

        groups = rb.find('{}groups'.format(NAMESPACE))
        groups.text = '0'

        # field_binary = rb.find('{}Field_Binary'.format(NAMESPACE))
        existing = rb.findall('{}Field_Binary'.format(NAMESPACE))
        field_binary = existing[0]
        insert_at = list(rb).index(field_binary)
        for extra in existing[1:]:
            rb.remove(extra)

        for i in range(int(self.fields) - 1):
            cloned_file = copy.deepcopy(field_binary)
            # rb.append(cloned_file)
            rb.insert(insert_at + i + 1, cloned_file)
        
        return root

    def fill_label_values(self, label_root, cleaned_form):
        Table_Binary = label_root

        f = Table_Binary.find('{}File_Area_Observational'.format(NAMESPACE))
        tb = f.find('{}Table_Binary'.format(NAMESPACE))
        rb = tb.find('{}Record_Binary'.format(NAMESPACE))

        fields = rb.findall('{}Field_Binary'.format(NAMESPACE))

        # field_count = 0

        # for field in fields:
        for field_count, field in enumerate(fields):
            name = field.find('{}name'.format(NAMESPACE)) 
            name.text = cleaned_form.get(f'name_{field_count}')
            field_location = field.find('{}field_location'.format(NAMESPACE)) 
            field_location.text = cleaned_form.get(f'field_location_{field_count}')
            data_type = field.find('{}data_type'.format(NAMESPACE)) 
            data_type.text = cleaned_form.get(f'data_type_{field_count}')
            field_length = field.find('{}field_length'.format(NAMESPACE))
            field_length.text = str(cleaned_form.get(f'max_field_length_{field_count}'))
            description = field.find('{}description'.format(NAMESPACE)) 
            description.text = str(cleaned_form.get(f'description_{field_count}'))
            field_number = field.find('{}field_number'.format(NAMESPACE))
            field_number.text = str(field_count + 1)

        return label_root

    def __str__(self):
        return smart_str(self.id)


# @python_2_unicode_compatible
class Table_Fixed_Width(models.Model):
    RECORD_CHOICES = (
        ('Sample Choice', 'Sample Choice'),
    )

    PRIMARY_RESULTS_SUMMARY_FACET_CHOICES = (
        ('Meteorology', 'Meteorology'),
        ('Structure', 'Structure')
    )
    
    name = models.CharField(max_length=256, blank=True)
    offset = models.IntegerField(default=1)
    object_length = models.IntegerField(default=1)
    description = models.CharField(max_length=5000, default="unset")
    records = models.IntegerField(default=1)
    fields = models.IntegerField(default=1)
    facet1 = models.CharField(max_length=256, choices=PRIMARY_RESULTS_SUMMARY_FACET_CHOICES, default="Meteorology", blank=True)
    data = models.ForeignKey(Data, on_delete=models.CASCADE, null=True)
    collection = models.ForeignKey(AdditionalCollections, on_delete = models.CASCADE, default='',)
    bundle = models.ForeignKey(Bundle, on_delete=models.CASCADE, null=True)

    def name_label_case(self):
        name_edit = self.data.name.lower()
        name_edit = replace_all(name_edit, ' ', '_')
        name_edit = '{}.xml'.format(name_edit)
        return name_edit

    def label(self):
        """
            label returns the physical label location in ELSAs archive
        """
        print(os.path.join(self.collection.directory(), self.name_label_case()))
        return os.path.join(self.collection.directory(), self.name_label_case())

        # directory returns the file path associated with the given model.
    def directory(self):
        # data_collection_name = self.collection.collection_name.lower()
        data_name = self.data.name.lower()
        data_name = replace_all(data_name, ' ', '_')
        data_directory = os.path.join(self.collection.directory(), data_name)
        return data_directory

    def build_data_file(self):
        # Locate base case Product_Bundle template found in templates/pds4_labels/base_case/product_bundle
        source_file = os.path.join(settings.TEMPLATE_DIR, 'pds4_labels')
        source_file = os.path.join(source_file, 'base_templates')
        source_file = os.path.join(source_file, 'data_table_character.xml')

        ret_name = self.data.name.lower()
        ret_name = replace_all(ret_name, ' ', '_')
        out_file = os.path.join(self.directory(), ret_name + '.xml')
        # out_file = os.path.join(self.directory(), self.data.name + '.xml')

        #set selected version
        update = Version()
        bundle = Bundle()
        print (source_file + "<<<<<<<<")

        update.version_update_old(self.data.bundle.version, source_file,out_file)
        
    def fill_base_case(self, root, cleaned_form):

        Table_Character = root

        # Fill in Identification_Area
        print("id area")
        Identification_Area = Table_Character.find('{}Identification_Area'.format(NAMESPACE))

        # lid
        logical_identifier = Identification_Area.find('{}logical_identifier'.format(NAMESPACE))
        logical_identifier.text = 'urn:{0}:{1}:{2}:{3}'.format(self.data.bundle.user.userprofile.agency, self.data.bundle.name_lid_case(), self.collection.collection_name.lower(), replace_all(self.name.lower(), ' ', '_')) # where agency is something like nasa:pds

        print("version")
        version_id = Identification_Area.find('{}version_id'.format(NAMESPACE))
        version_id.text = '1.0'  # Can make this better

        print("title")
        title = Identification_Area.find('{}title'.format(NAMESPACE))
        title.text = self.name

        print("info model")
        information_model_version = Identification_Area.find('information_model_version')
        #information_model_version.text = self.bundle.version.with_dots()

        Observation_Area = Table_Character.find('{}Observation_Area'.format(NAMESPACE))
        Primary_Result_Summary = Observation_Area.find('{}Primary_Result_Summary'.format(NAMESPACE))
        Science_Facets = Primary_Result_Summary.find('{}Science_Facets'.format(NAMESPACE))
        science_facet1 = Science_Facets.find('{}facet1'.format(NAMESPACE))
        science_facet1.text = self.facet1

        f = Table_Character.find('{}File_Area_Observational'.format(NAMESPACE))

        if self.data.header:
            header = f.find('{}Header'.format(NAMESPACE))
            local_id = header.find('{}local_identifier'.format(NAMESPACE))
            local_id.text = cleaned_form.get('local_identifier')

            offset = header.find('{}offset'.format(NAMESPACE))
            offset.text = '0' # str(cleaned_form.get('header_offset'))

            object_length = header.find('{}object_length'.format(NAMESPACE))
            object_length.text = str(cleaned_form.get('header_object_length'))

        tc = f.find('{}Table_Character'.format(NAMESPACE))
        rc = tc.find('{}Record_Character'.format(NAMESPACE))

        if self.name:
            name = tc.find('{}name'.format(NAMESPACE))
            name.text = self.name

        # if self.offset:
        offset = tc.find('{}offset'.format(NAMESPACE))
            # offset.text = str(self.offset)

        if self.data.header:
            print('within offset with header')
            offset.text = str(cleaned_form.get('header_object_length')) # str(cleaned_form.get('header_offset') + self.offset)
        else:
            offset.text = str(self.offset)

        if self.object_length:
            object_length = tc.find('{}object_length'.format(NAMESPACE))
            object_length.text = str(self.object_length)
        
        if self.description:
            description = tc.find('{}description'.format(NAMESPACE))
            description.text = str(self.description)
        
        if self.records:
            records = tc.find('{}records'.format(NAMESPACE))
            records.text = str(self.records)
        
        if self.fields:
            fields = rc.find('{}fields'.format(NAMESPACE))
            fields.text = str(self.fields)

        groups = rc.find('{}groups'.format(NAMESPACE))
        groups.text = '0'

        # field_character = rc.find('{}Field_Character'.format(NAMESPACE))
        existing = rc.findall('{}Field_Character'.format(NAMESPACE))
        field_character = existing[0]
        insert_at = list(rc).index(field_character)
        for extra in existing[1:]:
            rc.remove(extra)

        for i in range(int(self.fields) - 1):
            cloned_file = copy.deepcopy(field_character)
            # rc.append(cloned_file)
            rc.insert(insert_at + i + 1, cloned_file)
        
        return root
    
    def fill_label_values(self, label_root, cleaned_form):
        Table_Character = label_root

        f = Table_Character.find('{}File_Area_Observational'.format(NAMESPACE))
        tc = f.find('{}Table_Character'.format(NAMESPACE))
        rc = tc.find('{}Record_Character'.format(NAMESPACE))

        fields = rc.findall('{}Field_Character'.format(NAMESPACE))

        # field_count = 0

        # for field in fields:
        for field_count, field in enumerate(fields):
            name = field.find('{}name'.format(NAMESPACE)) 
            name.text = cleaned_form.get(f'name_{field_count}')
            field_location = field.find('{}field_location'.format(NAMESPACE)) 
            field_location.text = cleaned_form.get(f'field_location_{field_count}')
            data_type = field.find('{}data_type'.format(NAMESPACE)) 
            data_type.text = str(cleaned_form.get(f'data_type_{field_count}'))
            field_number = field.find('{}field_number'.format(NAMESPACE))
            # field_number.text = str(cleaned_form.get(f'field_number_{field_count}'))
            field_number.text = str(field_count + 1)
            description = field.find('{}description'.format(NAMESPACE)) 
            description.text = str(cleaned_form.get(f'description_{field_count}'))

        return label_root

    def __str__(self):
        return smart_str(self.id)


# @python_2_unicode_compatible
class Field_Delimited(models.Model):
    name = models.CharField(max_length=256)
    field_number = models.IntegerField()
    data_type = models.CharField(max_length=256)
    unit = models.CharField(max_length=256, null=True,)
    description = models.CharField(max_length=5000)
    table = models.ForeignKey(
        Table_Delimited, on_delete=models.CASCADE, null=True,)

    def __str__(self):
        pass


# @python_2_unicode_compatible
class Field_Binary(models.Model):
    name = models.CharField(max_length=256)
    field_number = models.IntegerField()
    field_location = models.CharField(max_length=256)
    data_type = models.CharField(max_length=256)
    field_length = models.IntegerField()
    unit = models.CharField(max_length=256, null=True,)
    scaling_factor = models.IntegerField()
    value_offset = models.IntegerField()
    description = models.CharField(max_length=5000)
    table = models.ForeignKey(
        Table_Binary, on_delete=models.CASCADE, null=True,)

    def __str__(self):
        pass


# @python_2_unicode_compatible
class Field_Character(models.Model):
    name = models.CharField(max_length=256)
    field_number = models.IntegerField()
    data_type = models.CharField(max_length=256)
    field_length = models.IntegerField()
    field_location = models.CharField(max_length=256)
    description = models.CharField(max_length=5000)
    table = models.ForeignKey(
        Table_Fixed_Width, on_delete=models.CASCADE, null=True,)

    def __str__(self):
        pass


"""
8.3  Product_Observational

Root Class:Product
Role:Concrete

Class Description:A Product_Observational is a set of one or more information objects produced by an observing system.

Steward:pds
Namespace Id:pds
Version Id:1.7.0.0
          Entity         Card         Value/Class         Ind

Hierarchy        Product                           
                 . Product_Observational                           

Subclass                none                           
Attribute                none                           
Inherited Attribute        none                           
Association        file_area               1..*        File_Area_Observational         
                file_area_supplemental        0..*        File_Area_Observational_Supplemental         
                 observation_area        1        Observation_Area         
                reference_list                0..1        Reference_List         

Inherited Association        has_identification_area        1        Identification_Area         

Referenced from        none                           
"""


# @python_2_unicode_compatible
class Product_Observational(models.Model):
    DOMAIN_TYPES = [
        ('Atmosphere', 'Atmosphere'),
        ('Dynamics', 'Dynamics'),
        ('Heliosphere', 'Heliosphere'),
        ('Interior', 'Interior'),
        ('Interstellar', 'Interstellar'),
        ('Ionosphere', 'Ionosphere'),
        ('Magnetosphere', 'Magnetosphere'),
        ('Rings', 'Rings'),
        ('Surface', 'Surface'),
    ]
    DISCIPLINE_TYPES = [
        ('Atmospheres', 'Atmospheres'),
        ('Fields', 'Fields'),
        ('Flux Measurements', 'Flux Measurements'),
        ('Geosciences', 'Geosciences'),
        ('Imaging', 'Imaging'),
        ('Particles', 'Particles'),
        ('Radio Science', 'Radio Science'),
        ('Ring-Moon Systems', 'Ring-Moon Systems'),
        ('Small Bodies', 'Small Bodies'),
        ('Spectroscopy', 'Spectroscopy'),
    ]
    OBSERVATIONAL_TYPES = [

        ('Table', 'Table'),
        ('Array', 'Array'),
        #('Table Binary','Table Binary'),
        #('Table Character','Table Character'),
        #('Table Delimited','Table Delimited'),
    ]
    PROCESSING_LEVEL_TYPES = [
        ('Calibrated', 'Calibrated'),
        ('Derived', 'Derived'),
        ('Reduced', 'Reduced'),
        ('Raw', 'Raw'),
        # ('Telemetry','Telemetry'),  Executive Decision made to leave this out.  6-27-2018.
    ]
    PURPOSE_TYPES = [
        ('Calibration', 'Calibration'),
        ('Checkout', 'Checkout'),
        ('Engineering', 'Engineering'),
        ('Navigation', 'Navigation'),
        ('Observation Geometry', 'Observation Geometry'),
        ('Science', 'Science'),

    ]
    bundle = models.ForeignKey(Bundle, on_delete=models.CASCADE)
    data = models.ForeignKey(Data, on_delete=models.CASCADE)
    domain = models.CharField(
        max_length=MAX_CHAR_FIELD, choices=DOMAIN_TYPES, default='Atmosphere')
    discipline = models.CharField(
        max_length=MAX_CHAR_FIELD, choices=DISCIPLINE_TYPES, default='Atmospheres')
    processing_level = models.CharField(
        max_length=MAX_CHAR_FIELD, choices=PROCESSING_LEVEL_TYPES)
    purpose = models.CharField(
        max_length=MAX_TEXT_FIELD, choices=PURPOSE_TYPES)
    title = models.CharField(max_length=MAX_CHAR_FIELD)
    type_of = models.CharField(
        max_length=MAX_CHAR_FIELD, choices=OBSERVATIONAL_TYPES, default='Table')

    """
        name_label_case returns the title of the Product Observational in lowercase with underscores rather than spaces
    """

    def name_label_case(self):
        edit_name = self.title.lower()
        edit_name = replace_all(edit_name, ' ', '_')
        return edit_name

    """
       lid returns the lid associated with the Product_Observational label
    """

    def lid(self):
        edit_name = self.name_label_case()
        lid = 'urn:{0}:{1}:data_{2}:{3}'.format(self.bundle.user.userprofile.agency, self.bundle.name_lid_case(
        ), self.processing_level.lower(), self.name_label_case())
        return lid

    """
       label returns the physical label location in ELSAs archive
    """

    def label(self):
        edit_name = '{}.xml'.format(self.name_label_case())
        return os.path.join(self.data.directory(), edit_name)

    # Label Constructors

    def build_base_case(self):

        # Locate base case Product_Observational template found in templates/pds4_labels/base_case/
        source_file = os.path.join(PDS4_LABEL_TEMPLATE_DIRECTORY, 'base_case')
        source_file = os.path.join(source_file, 'product_observational.xml')

        # Locate collection directory and create path for new label
        edit_name = '{}.xml'.format(self.name_label_case())
        label_file = os.path.join(self.data.directory(), edit_name)

        # set selected version
        update = Version()
        update.version_update_old(self.data.bundle.version, source_file, label_file)

        return

    # Fillers
    """
        Fillers follow a set flow.
            1. Input the root element of an XML label.
                - We want the root because we can access all areas of the document through it's root.
            2. Find the areas you want to fill.
                - Always do find over a static search to ensure we are always on the right element.
                  (  ex. of static search ->    root[0] = Identification_Area in fill_base_case    )
                  Originally, ELSA used a static search for faster performance, but we found out
                  that comments in the XML label through the code off and we were pulling incorrect
                  elements.
            3. Fill those areas.
                - Fill is easy.  Just fill it.. with the information from the model it was called on,
                  self (like itself).
    """

    def fill_base_case(self, root):

        Identification_Area = root.find(
            '{}Identification_Area'.format(NAMESPACE))

        logical_identifier = Identification_Area.find(
            '{}logical_identifier'.format(NAMESPACE))
        logical_identifier.text = self.lid()
        title = Identification_Area.find('{}title'.format(NAMESPACE))
        title.text = self.title

        Observation_Area = root.find('{}Observation_Area'.format(NAMESPACE))
        Primary_Result_Summary = Observation_Area.find(
            '{}Primary_Result_Summary'.format(NAMESPACE))
        processing_level = Primary_Result_Summary.find(
            '{}processing_level'.format(NAMESPACE))
        processing_level = self.processing_level
        Science_Facets = Primary_Result_Summary.find(
            '{}Science_Facets'.format(NAMESPACE))
        domain = Science_Facets.find('{}domain'.format(NAMESPACE))
        domain.text = self.domain
        discipline_name = Science_Facets.find(
            '{}discipline_name'.format(NAMESPACE))
        discipline_name.text = self.discipline

        # ASK LYNN ABOUT THIS --------------------------------------------------------------------
        Investigation_Area = root.find(
            '{}Investigation_Area'.format(NAMESPACE))
        # ----------------------------------------------------------------------------------------

        return root

    """
        Fillers follow a set flow.
            1. Input the root element of an XML label.
                - We want the root because we can access all areas of the document through it's root.
            2. Find the areas you want to fill.
                - Always do find over a static search to ensure we are always on the right element.
                  (  ex. of static search ->    root[0] = Identification_Area in fill_base_case    )
                  Originally, ELSA used a static search for faster performance, but we found out
                  that comments in the XML label through the code off and we were pulling incorrect
                  elements.
            3. Fill those areas.
                - Fill is easy.  Just fill it.. with the information from the model it was called on,
                  self (like itself).
    """

    def fill_observational(self, label_root, observational):
        Product_Observational = label_root

        File_Area_Observational = Product_Observational.find(
            '{}File_Area_Observational'.format(NAMESPACE))

        Observational_Tag_Name = replace_all(self.type_of, ' ', '_')
        Observational_Tag = etree.SubElement(
            File_Area_Observational, Observational_Tag_Name)

        name = etree.SubElement(Observational_Tag, 'name')
        name.text = observational.name

        local_identifier = etree.SubElement(
            Observational_Tag, 'local_identifier')
        # NEED TO MAKE        local_identifier.text = observational.local_identifier()

        offset = etree.SubElement(Observational_Tag, 'offset')
        offset.attrib['unit'] = 'byte'
        offset.text = observational.offset

        object_length = etree.SubElement(Observational_Tag, 'object_length')
        object_length.attrib['unit'] = 'byte'
        object_length.text = observational.object_length

        parsing_standard_id = etree.SubElement(
            Observational_Tag, 'parsing_standard_id')
        parsing_standard_id.text = 'PDS DSV 1'

        description = etree.SubElement(Observational_Tag, 'description')
        description.text = observational.description

        records = etree.SubElement(Observational_Tag, 'records')
        records.text = observational.records

        record_delimiter = etree.SubElement(
            Observational_Tag, 'record_delimiter')
        record_delimiter.text = 'Carriage-Return Line-Feed'

        field_delimiter = etree.SubElement(
            Observational_Tag, 'field_delimiter')
        # --------------------------------FIX ME----------
        field_delimiter.text = 'Need to Fix'

        # Start Record Delimited Section
        Record_Delimited = etree.SubElement(
            Observational_Tag, 'Record_Delimited')
        fields = etree.SubElement(Record_Delimited, 'fields')
        fields.text = observational.fields
        groups = etree.SubElement(Record_Delimited, 'groups')
        groups.text = observational.groups

        # Add loop  - Ask Lynn how he wants to do this, again.

        # End
        return Product_Observational

    def fill_display_dictionary(self, root):
        """
        build_internal_reference builds and fills the Internal_Reference information within the 
        Reference_List of Product_Bundle.  The relation is used within reference_type to associate what 
        the bundle is related to, like bundle_to_document.  Therefore, relation is a model object in 
        ELSA, like Document.  The possible relations as of V1A00 are errata, document, investigation, 
        instrument, instrument_host, target, resource, associate.
        """
        print('---DEBUG---')
        print('Root: {}'.format(root))

        # Change the xml-model processing instruction  --- Needs a fix
        text = 'href=https://pds.nasa.gov/pds4/disp/v1/PDS4_DISP_1B00.sch'

        root.addprevious(etree.ProcessingInstruction('xml-model', text=text))
        print('Tree: {}'.format(etree.tostring(root)))

        return root

    """
        Returns the title of the observational product
    """
    # Meta

    def __str__(self):

        return "Product_Observational at: {}".format(self.title)


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

# @python_2_unicode_compatible
def pds_document_standard(value):
    """PDS4 name for a document's format. Older documents stored "ASCII" for plain text,
    which needs to be "7-Bit ASCII Text".
    """
    return {'ASCII': '7-Bit ASCII Text'}.get((value or '').strip(), value)


class Product_Document(models.Model):
    # Attributes
    bundle = models.ForeignKey(Bundle, on_delete=models.CASCADE)
    # acknowledgement_text = models.CharField(max_length=MAX_CHAR_FIELD) #doesn't exist on lockwood.xml
    author_list = models.CharField(max_length=MAX_CHAR_FIELD)
    copyright = models.CharField(max_length=MAX_CHAR_FIELD)
    comment = models.CharField(max_length=MAX_CHAR_FIELD, default='')
    description = models.CharField(max_length=MAX_CHAR_FIELD)
    # doi = models.CharField(max_length=MAX_CHAR_FIELD, default='')
    document_editions = models.CharField(max_length=MAX_CHAR_FIELD)
    document_name = models.CharField(max_length=MAX_CHAR_FIELD)
    # editor_list = models.CharField(max_length=MAX_CHAR_FIELD) #doesn't exist on lockwood.xml
    publication_date = models.CharField(max_length=MAX_CHAR_FIELD)
    revision_id = models.CharField(max_length=MAX_CHAR_FIELD)
    edition_name = models.CharField(max_length=MAX_CHAR_FIELD, default='')
    language = models.CharField(max_length=MAX_CHAR_FIELD, default='')
    files = models.CharField(max_length=MAX_CHAR_FIELD, default='')
    file_name = models.CharField(max_length=MAX_CHAR_FIELD, default='')
    local_id = models.CharField(max_length=MAX_CHAR_FIELD, default='')
    document_id = models.CharField(max_length=MAX_CHAR_FIELD, default='')
    name = models.CharField(max_length=MAX_CHAR_FIELD, default='')
    document_std_id = models.CharField(max_length=MAX_CHAR_FIELD, default='PDF/A')
    encoding_standard_id = models.CharField(max_length=MAX_CHAR_FIELD, default='PDF/A')

    # Meta

    def __str__(self):
        return self.document_name

    # Accessors
    """
    - absolute_url
      Returns the url to the Product Document Detail page.
    """

    def absolute_url(self):
        return reverse('build:product_document', args=[smart_str(self.bundle.id), smart_str(self.id)])

    def collection(self):
        return 'document'

    def directory(self):
        """
            Documents are found in the Document collection
        """
        collection_directory = os.path.join(
            self.bundle.directory(), 'document')
        return collection_directory

    def stored_file_size(self):
        """Bytes of the uploaded document file on disk, or None. For templates."""
        from build import document_files
        return document_files.size(self)

    def text_excerpt(self):
        """The first lines of a plain text document, for its thumbnail. For templates."""
        from build import document_files
        return document_files.text_excerpt(self)

    def name_label_case(self):
        """
            This could be improved to ensure disallowed characters for a file name are not contained
            in name.
        """
        name_edit = self.document_name.lower()
        name_edit = replace_all(name_edit, ' ', '_')
        return name_edit
    
    def label(self):
        """
            label returns the physical label location in ELSAs archive
        """
        #return os.path.join(self.directory(), self.name_label_case())
        return os.path.join(self.directory(), '{}.xml'.format(self.name_label_case()))

    def lid(self):
        return '{0}:document:{1}'.format(self.bundle.lid(), self.name_label_case())

    def reference_type(self):
        return 'document'

    # Builders

    def build_base_case(self):

        if self.bundle.bundle_type == 'External':
            source_file = os.path.join(PDS4_LABEL_TEMPLATE_DIRECTORY, 'base_templates')
            template_name = "Template_PE_document.xml"
        else:
            # Locate base case Product_Document template found in templates/pds4_labels/base_case/
            source_file = os.path.join(PDS4_LABEL_TEMPLATE_DIRECTORY, 'base_case')
            template_name = "product_document.xml"

        source_file = os.path.join(source_file, template_name)    
    

        # Locate collection directory and create path for new label
        label_file = os.path.join(self.directory(), '{}.xml'.format(self.name_label_case()))
        # set selected version
        update = Version()
        bundle = Bundle()
        update.version_update_old(self.bundle.version, source_file, label_file)

        # Copy the base case template to the correct directory
        # copyfile(source_file, label_file)

        return

    """
        Fillers follow a set flow.
            1. Input the root element of an XML label.
                - We want the root because we can access all areas of the document through it's root.
            2. Find the areas you want to fill.
                - Always do find over a static search to ensure we are always on the right element.
                  (  ex. of static search ->    root[0] = Identification_Area in fill_base_case    )
                  Originally, ELSA used a static search for faster performance, but we found out
                  that comments in the XML label through the code off and we were pulling incorrect
                  elements.
            3. Fill those areas.
                - Fill is easy.  Just fill it.. with the information from the model it was called on,
                  self (like itself).
    """

    def fill_label(self, root):

        Product_Document = root

        # Fill in Identification_Area
        Identification_Area = Product_Document.find(
            '{}Identification_Area'.format(NAMESPACE))

        logical_identifier = Identification_Area.find(
            '{}logical_identifier'.format(NAMESPACE))
        logical_identifier.text = self.lid()
# Jacob's version going to need to verify if its better
#        logical_identifier.text =  'urn:{0}:{1}:{2}:{3}'.format(self.bundle.user.userprofile.agency, self.bundle.name_lid_case(), 'document', self.document_name) # where agency is something like nasa:pds

        version_id = Identification_Area.find('{}version_id'.format(NAMESPACE))
        version_id.text = '1.0'  # Can make this better

        title = Identification_Area.find('{}title'.format(NAMESPACE))
        title.text = self.document_name

        information_model_version = Identification_Area.find(
            'information_model_version')
        #information_model_version.text = self.bundle.version.with_dots()

        # If the bundle is External, only look at the fields in the PE_document.xml file
        if self.bundle.bundle_type == 'External':
            File_Area_External = Product_Document.find('{}File_Area_External'.format(NAMESPACE))
            File = File_Area_External.find('{}File'.format(NAMESPACE))
            Encoded_External = File_Area_External.find('{}Encoded_External'.format(NAMESPACE))

            if Encoded_External is not None:
                name = Encoded_External.find('{}name'.format(NAMESPACE))
                name.text = self.file_name # Yes, this is correct.
                if self.document_std_id:
                    encoding_standard_id = Encoded_External.find(
                        '{}encoding_standard_id'.format(NAMESPACE))
                    encoding_standard_id.text = pds_document_standard(self.document_std_id)
            if File is not None:
                # Add a comment element if needed (last in File), and remove it if the comment is cleared.
                comment = File.find('{}comment'.format(NAMESPACE))
                if self.comment:
                    if comment is None:
                        comment = File.makeelement('{}comment'.format(NAMESPACE), {})
                        File.append(comment)
                    comment.text = self.comment
                elif comment is not None:
                    File.remove(comment)
                if self.file_name:
                    file_name = File.find('{}file_name'.format(NAMESPACE))
                    file_name.text = self.file_name



        # Else, default to the archive product_document fields   
        else: 
            # Fill in Document
            Document = Product_Document.find('{}Document'.format(NAMESPACE))
            Document_Edition = Document.find(
                '{}Document_Edition'.format(NAMESPACE))
            Files = Document_Edition.find('{}Document_File'.format(NAMESPACE))
            if self.revision_id:
                revision_id = Document.find('{}revision_id'.format(NAMESPACE))
                revision_id.text = self.revision_id
            if self.document_name:
                document_name = Document.find('{}document_name'.format(NAMESPACE))
                document_name.text = self.document_name
            # if self.doi:
            #     doi = etree.SubElement(Document, 'doi')
            #     doi.text = self.doi
            # author_list is deprecated in PDS4 and validate says so on every
            # document that carries one: "pds:Document/pds:author_list is
            # deprecated and should not be used." The field stays on the model and
            # on the form, because it is how a user records who wrote the document
            # and removing it would lose that; it simply no longer goes into the
            # label. The template's empty element is dropped so it does not become
            # an empty-value error in its place.
            author_list = Document.find('{}author_list'.format(NAMESPACE))
            if author_list is not None:
                Document.remove(author_list)
            # if self.editor_list:
            #     editor_list = etree.SubElement(Document, 'editor_list')
            #     editor_list.text = self.editor_list
            # if self.acknowledgement_text:
            #     acknowledgement_text = etree.SubElement(Document, 'acknowledgement_text')
            #     acknowledgement_text.text = self.acknowledgement_text
            if self.copyright:
                copyright = Document.find('{}copyright'.format(NAMESPACE))
                copyright.text = self.copyright
            if self.publication_date:  # this should always be true
                publication_date = Document.find(
                    '{}publication_date'.format(NAMESPACE))
                publication_date.text = self.publication_date
            if self.document_editions:
                document_editions = Document.find(
                    '{}document_editions'.format(NAMESPACE))
                document_editions.text = self.document_editions
            if self.description:
                description = Document.find('{}description'.format(NAMESPACE))
                description.text = self.description
            if self.edition_name:
                edition_name = Document_Edition.find(
                    '{}edition_name'.format(NAMESPACE))
                edition_name.text = self.edition_name
            # PDS4 only accepts 'English' here, and language is required, so always write it.
            language = Document_Edition.find('{}language'.format(NAMESPACE))
            if language is None:
                language = etree.SubElement(
                    Document_Edition, '{}language'.format(NAMESPACE))
            language.text = pds_document_language(self.language)
            if self.files:
                files = Document_Edition.find('{}files'.format(NAMESPACE))
                files.text = self.files
            if self.file_name:
                file_name = Files.find('{}file_name'.format(NAMESPACE))
                file_name.text = self.file_name
            if self.local_id:
                local_id = Files.find('{}local_identifier'.format(NAMESPACE))
                local_id.text = self.local_id
            if self.document_std_id:
                document_std_id = Files.find(
                    '{}document_standard_id'.format(NAMESPACE))
                document_std_id.text = pds_document_standard(self.document_std_id)

            

        # for i in range(int(self.files) - 1):
        file_count = 0
        try:
            file_count = int(self.files)
        except (TypeError, ValueError):
            file_count = 0

        # Archive documents only. External documents have File_Area_External and no Document_Edition.
        if self.bundle.bundle_type != 'External':
            for i in range(file_count - 1):
                cloned_file = copy.deepcopy(Files)
                Document_Edition.append(cloned_file)

            # PDS4 requires these three and the form doesn't, so give them defaults.
            # publication_date is required too, but the annex form doesn't ask for it.
            _require_text(Document, 'publication_date', self.publication_date,
                          timezone.localdate().isoformat())
            _require_text(Document_Edition, 'edition_name', self.edition_name, '1.0')
            _require_text(Document_Edition, 'files', self.files,
                          str(max(file_count, 1)))
            _require_text(Document_Edition, 'language',
                          pds_document_language(self.language),
                          PDS_DOCUMENT_LANGUAGE)

        # Optional fields the document has nothing for, better absent than blank.
        # Imported here to avoid a circular import through label_repair.
        from build.label_repair import prune_blank_containers
        prune_blank_containers(root)

        return root
    
    def build_internal_reference(self, root, relation):
        """
            build_internal_reference needs to be completed
        """
        pass

    def remove_xml(self, label_root):
    # Find the Document element
        Document = label_root.find('{}Document'.format(NAMESPACE))

        if Document is not None:
        # Remove the entire Document element
            Document.getparent().remove(Document)

    # Find and remove the logical_identifier that matches this document
        Identification_Area = label_root.find('{}Identification_Area'.format(NAMESPACE))
        if Identification_Area is not None:
            logical_identifier = Identification_Area.find('{}logical_identifier'.format(NAMESPACE))
            if logical_identifier is not None and logical_identifier.text == self.lid():
                Identification_Area.remove(logical_identifier)

            # Remove the title if it matches the document_name
            title = Identification_Area.find('{}title'.format(NAMESPACE))
            if title is not None and title.text == self.document_name:
                Identification_Area.remove(title)

        return label_root


"""
10.1  Alias

Root Class:Product_Components
Role:Concrete

Class Description:The Alias class provides a single alternate name and identification for this product in this or some other archive or data system.

Steward:pds
Namespace Id:pds
Version Id:1.0.0.0
          Entity         Card         Value/Class         Ind

Hierarchy        Product_Components                           
         . Alias                           

Subclass        none                           

Attribute        alternate_id        0..1                  
                alternate_title        0..1                  
                comment                0..1                  

Inherited Attribute        none                           
Association                none                           
Inherited Association        none                           

Referenced from        Alias_List                           
"""


# @python_2_unicode_compatible
class Alias(models.Model):

    bundle = models.ForeignKey(Bundle, on_delete=models.CASCADE)
    alternate_id = models.CharField(max_length=MAX_CHAR_FIELD)
    alternate_title = models.CharField(max_length=MAX_CHAR_FIELD)
    comment = models.CharField(max_length=MAX_CHAR_FIELD)
    Alias_List = ["nop"]

    # Currently, the documentation says that none of these three fields: alternate_id,
    # alternate_title, and comment are required within an Alias.
    # However, it does not make a lot of sense to add a comment within an Alias tag without ever
    # specifying an id or title.  Like what are you commenting about then, right?  So there should
    # be some precedence set like ( alternate_id exclusive or alternate_title ) or comment.
    #
    # The truth table is as follows:
    #     ( alternate_id  EXCLUSIVE OR  alternate_title )   AND    comment
    #            1                                0                  0,1    *easy to see the comment
    #            0                                1                  0,1    *will not matter now

    def __str__(self):
        if self.alternate_id:
            return self.alternate_id
        elif self.alternate_title:
            return self.alternate_title
        else:
            return self.comment

    def fill_label(self, label_root):

        # Find Identification_Area
        Identification_Area = label_root.find(
            '{}Identification_Area'.format(NAMESPACE))

        # Find Alias_List.  If no Alias_List is found, make one.
        Alias_List = Identification_Area.find('{}Alias_List'.format(NAMESPACE))
        if Alias_List is None:
            Alias_List = etree.Element('Alias_List')

        # Add Alias information
        Alias = etree.SubElement(Alias_List, 'Alias')
        if self.alternate_id:
            alternate_id = etree.SubElement(Alias, 'alternate_id')
            alternate_id.text = self.alternate_id
        if self.alternate_title:
            alternate_title = etree.SubElement(Alias, 'alternate_title')
            alternate_title.text = self.alternate_title
        if self.comment:
            comment = etree.SubElement(Alias, 'comment')
            comment.text = self.comment

        # find Modification_History
        Modification_History = Identification_Area.find('{}Modification_History'.format(NAMESPACE))

        # find citation_information
        Citation_Information = Identification_Area.find('{}Citation_Information'.format(NAMESPACE))
        if Citation_Information is not None:
            current_Alias_List = Identification_Area.find('{}Alias_List'.format(NAMESPACE))
            if current_Alias_List is not None:
                Identification_Area.remove(current_Alias_List)

            Identification_Area.insert(Identification_Area.index(Citation_Information), Alias_List)

        else:
            # PDS4 order: Alias_List goes after product_class and before Citation_Information and
            # Modification_History. Use whichever exists, otherwise append.
            if Modification_History is not None:
                Identification_Area.insert(
                    Identification_Area.index(Modification_History), Alias_List)
            else:
                Identification_Area.append(Alias_List)

        return label_root


    # added for edit alias fixing purposes - deric
    def find_alias(self, label_root):
        # Find Identification_Area
        Identification_Area = label_root.find('{}Identification_Area'.format(NAMESPACE))

        # Find Alias_List.  If no Alias_List is found, make one.
        Alias_List = Identification_Area.find('{}Alias_List'.format(NAMESPACE))
        if Alias_List is None:
            Alias_List = etree.SubElement(Identification_Area, 'Alias_List')

        # Add Alias information
        Alias = Alias_List.find('{}Alias'.format(NAMESPACE))
        if Alias is None:
            # A new Alias_List has no Alias yet. fill_label adds one, this only updates it.
            return label_root
        if self.alternate_id:
            alternate_id = Alias.find('{}alternate_id'.format(NAMESPACE))
            alternate_id.text = self.alternate_id
        if self.alternate_title:
            alternate_title = Alias.find('{}alternate_title'.format(NAMESPACE))
            alternate_title.text = self.alternate_title
        if self.comment:
            comment = Alias.find('{}comment'.format(NAMESPACE))
            comment.text = self.comment

        return label_root

    def remove_xml(self, label_root):
        Identification_Area = label_root.find('{}Identification_Area'.format(NAMESPACE))
        if Identification_Area is None:
            return label_root

        Alias_List = Identification_Area.find('{}Alias_List'.format(NAMESPACE))
        if Alias_List is None:
            # Collection labels don't have an Alias_List until an alias is written, so there may be nothing to remove.
            return label_root

        for alias in Alias_List.findall('{}Alias'.format(NAMESPACE)):
            # By tag, since alternate_id is optional and may not be first.
            alternate_id = alias.find('{}alternate_id'.format(NAMESPACE))
            if alternate_id is None or not alternate_id.text or not self.alternate_id:
                continue
            if alternate_id.text.title() == self.alternate_id.title():
                Alias_List.remove(alias)

        # An Alias_List with nothing left in it is not valid PDS4, so it goes too.
        if not len(Alias_List):
            Identification_Area.remove(Alias_List)

        return label_root

        # bundle = xmlFile.directory()+'/bundle_'+xmlFile.name_file_case()+'.xml'
        # context = xmlFile.directory()+'/context/collection_context.xml'
        # document = xmlFile.directory()+'/document/collection_document.xml'
        # xml_schema = xmlFile.directory()+'/xml_schema/collection_xml_schema.xml'

        # file_list = [bundle, context, document, xml_schema]

        # '''Identification_Area = label_root.find('{}Identification_Area'.format(NAMESPACE))
        # alias_list = Identification_Area.find('{}Alias_List'.format(NAMESPACE))

        # print Alias_List'''

        # for tree_file in file_list:
        #     tree = etree.parse(tree_file)
        #     root = tree.getroot()

        #     for item in root.getiterator():
        #         if item.text == removeTag:
        #             removeTag = item.getparent()
        #             remParent = removeTag.getparent()
        #             remParent.remove(removeTag)
        #             break
        #     tree.write(tree_file)

    def print_alias_list(self):
        for element in alias_list:
            print(element)

    class Meta(object):
        verbose_name_plural = 'Aliases'


"""
10.3  Citation_Information

Root Class:Product_Components
Role:Concrete

Class Description:The Citation_Information class provides specific fields often used in citing the product in journal articles, abstract services, and other reference contexts.

Steward:pds
Namespace Id:pds
Version Id:1.2.0.0
          Entity         Card         Value/Class         Ind

Hierarchy        Product_Components                           
                 . Citation_Information                           

Subclass        none         
                  
Attribute        author_list             0..1                  
                description              1                  
                editor_list              0..1                  
                keyword                        0..*                  
                 publication_year        1        
          
Inherited Attribute        none                           
Association                none                           
Inherited Association        none                           

Referenced from        Identification_Area        
"""


# @python_2_unicode_compatible
def _same_xml(first, second):
    """Check if two elements are the same, ignoring indentation."""
    def signature(element):
        return (element.tag, (element.text or '').strip(), sorted(element.attrib.items()),
                [signature(child) for child in element if isinstance(child.tag, str)])
    return signature(first) == signature(second)


def mirror_citation_into_data_products(bundle):
    """Make every data product label match the bundle label's citation.

    The bundle label is the source of truth. If it has a citation each product gets a copy,
    if not each product loses its own. Called on add, edit and delete.

    Returns the number of labels changed.
    """
    try:
        product_bundle = Product_Bundle.objects.get(bundle=bundle)
        source_path = product_bundle.label()
    except (Product_Bundle.DoesNotExist, AttributeError):
        return 0
    if not source_path or not os.path.exists(source_path):
        return 0

    try:
        source_root = etree.parse(source_path).getroot()
    except (etree.XMLSyntaxError, OSError):
        return 0

    source = source_root.find(
        '{0}Identification_Area/{0}Citation_Information'.format(NAMESPACE))

    # Every product label, not just NetCDF. Documents carry a citation too.
    products = list(NetCDFFile.objects.filter(bundle=bundle))
    products.extend(Product_Document.objects.filter(bundle=bundle))

    changed = 0
    for product in products:
        try:
            label_path = product.label()
        except (AttributeError, ValueError):
            continue
        if not label_path or not os.path.exists(label_path):
            continue
        try:
            target_list = open_label_with_tree(label_path)
        except (etree.XMLSyntaxError, OSError):
            continue
        target_root = target_list[1]
        identification = target_root.find('{}Identification_Area'.format(NAMESPACE))
        if identification is None:
            continue

        existing = identification.find('{}Citation_Information'.format(NAMESPACE))

        if source is None:
            if existing is None:
                continue
            identification.remove(existing)
        else:
            # Same already, so skip the write. Writing would mark validation out of date.
            if existing is not None and _same_xml(existing, source):
                continue
            replacement = copy.deepcopy(source)
            if existing is not None:
                identification.replace(existing, replacement)
            else:
                # Citation_Information goes before Modification_History, so it can't just be appended.
                modification = identification.find(
                    '{}Modification_History'.format(NAMESPACE))
                if modification is not None:
                    identification.insert(
                        identification.index(modification), replacement)
                else:
                    identification.append(replacement)

        close_label(label_path, target_root, target_list[2])
        changed += 1

    return changed


def write_organization_fields(organization, name, rorid, sequence, parent_name):
    """Write an Organization's fields in the order PDS4 requires.

    Rebuilt from scratch because the setup page leaves optional fields out of the label.
    Blank optional fields are skipped since PDS4 rejects empty elements.
    """
    for child in list(organization):
        organization.remove(child)
    etree.SubElement(organization, '{}organization_name'.format(NAMESPACE)).text = name
    if (rorid or '').strip():
        etree.SubElement(organization, '{}organization_rorid'.format(NAMESPACE)).text = rorid.strip()
    if sequence is not None:
        etree.SubElement(organization, '{}sequence_number'.format(NAMESPACE)).text = sequence
    if (parent_name or '').strip():
        parent = etree.SubElement(organization, '{}Parent_Organization'.format(NAMESPACE))
        etree.SubElement(parent, '{}parent_organization_name'.format(NAMESPACE)).text = parent_name.strip()


def _fill_organization(organization, cleaned_form, prefix, index):
    """Write one organization from the bundle page's citation editor."""
    sequence = cleaned_form.get('{}_{}_sequence_number'.format(prefix, index))
    if sequence not in (None, '') or organization.find('{}sequence_number'.format(NAMESPACE)) is not None:
        sequence = str(sequence)
    else:
        sequence = None
    write_organization_fields(
        organization,
        cleaned_form.get('{}_{}_name'.format(prefix, index)),
        cleaned_form.get('{}_{}_rorid'.format(prefix, index)),
        sequence,
        cleaned_form.get('{}_{}_parent_org_name'.format(prefix, index)))


def mirror_targets_into_products(bundle):
    """Make every product label in an External bundle list the bundle's targets.

    Archive documents are left alone. Returns the number of labels changed.
    """
    if bundle.bundle_type != 'External':
        return 0

    targets = list(bundle.targets.all())
    wanted = sorted(target.lid for target in targets)
    products = list(NetCDFFile.objects.filter(bundle=bundle))
    products.extend(Product_Document.objects.filter(bundle=bundle))

    changed = 0
    for product in products:
        try:
            label_path = product.label()
        except (AttributeError, ValueError):
            continue
        if not label_path or not os.path.exists(label_path):
            continue
        try:
            label = open_label_with_tree(label_path)
        except (etree.XMLSyntaxError, OSError):
            continue
        root = label[1]
        Context_Area = root.find('{}Context_Area'.format(NAMESPACE))
        if Context_Area is None:
            continue
        existing = Context_Area.findall('{}Target_Identification'.format(NAMESPACE))
        lids = sorted(
            element.findtext('{0}Internal_Reference/{0}lid_reference'.format(NAMESPACE))
            or '' for element in existing)
        # Skip if nothing changed. Writing would mark validation out of date.
        if lids == wanted:
            continue
        for element in existing:
            Context_Area.remove(element)
        for target in targets:
            root = target.fill_label(root)
        close_label(label_path, root, label[2])
        changed += 1
    return changed


class Citation_Information(models.Model):

    bundle = models.ForeignKey(Bundle, on_delete=models.CASCADE)
    # author_list = models.CharField(max_length=MAX_CHAR_FIELD, blank=True)
    number_of_authors_people = models.PositiveIntegerField(default=0)
    number_of_authors_organization = models.PositiveIntegerField(default=0)
    # User-added editors, in ADDITION to the two fixed ATM editors (Lynn Neakrase
    # and Lyle Huber) that ELSA always writes into the label.
    number_of_editors_people = models.PositiveIntegerField(default=0)
    number_of_editors_organization = models.PositiveIntegerField(default=0)
    publication_year = models.CharField(max_length=MAX_CHAR_FIELD)
    description = models.CharField(max_length=MAX_TEXT_FIELD)
    keyword = models.CharField(max_length=MAX_CHAR_FIELD, blank=True)
    # True once the authors and editors are in Citation_Contributor. Before that they're only
    # in the bundle label (label migration 0003 copies them over).
    contributors_saved = models.BooleanField(default=False)
    

    def _recorded_citation_element(self):
        """A copy of the bundle label's Citation_Information, or None.

        The bundle label is written first, so it always has the newest values.
        """
        try:
            product_bundle = Product_Bundle.objects.get(bundle=self.bundle)
            source_path = product_bundle.label()
        except (Product_Bundle.DoesNotExist, AttributeError):
            return None
        if not source_path or not os.path.exists(source_path):
            return None
        try:
            source_root = etree.parse(source_path).getroot()
        except (etree.XMLSyntaxError, OSError):
            return None
        source = source_root.find(
            '{0}Identification_Area/{0}Citation_Information'.format(NAMESPACE))
        return copy.deepcopy(source) if source is not None else None

    def _recorded_people(self):
        """Author and editor values already recorded for this bundle.

        Names are only in the XML, the model just has counts, so read them from the bundle label.
        Returns nothing if that label or its citation doesn't exist yet.
        """
        empty = {'author_people': [], 'author_orgs': [],
                 'editor_people': [], 'editor_orgs': []}
        try:
            product_bundle = Product_Bundle.objects.get(bundle=self.bundle)
            path = product_bundle.label()
        except (Product_Bundle.DoesNotExist, AttributeError):
            return empty
        if not path or not os.path.exists(path):
            return empty

        try:
            root = etree.parse(path).getroot()
        except (etree.XMLSyntaxError, OSError):
            # An unreadable label is a different problem and not one this can fix.
            return empty

        citation = root.find('{0}Identification_Area/{0}Citation_Information'.format(NAMESPACE))
        if citation is None:
            return empty

        def text(element, tag):
            found = element.find('{}{}'.format(NAMESPACE, tag))
            return found.text if found is not None else None

        def people(container):
            values = []
            for person in container.findall('{}Person'.format(NAMESPACE)):
                affiliation = person.find('{}Affiliation'.format(NAMESPACE))
                values.append({
                    'given_name': text(person, 'given_name'),
                    'family_name': text(person, 'family_name'),
                    'person_orcid': text(person, 'person_orcid'),
                    'organization_name': (text(affiliation, 'organization_name')
                                          if affiliation is not None else None),
                })
            return values

        def organizations(container):
            values = []
            for organization in container.findall('{}Organization'.format(NAMESPACE)):
                parent = organization.find('{}Parent_Organization'.format(NAMESPACE))
                values.append({
                    'organization_name': text(organization, 'organization_name'),
                    'organization_rorid': text(organization, 'organization_rorid'),
                    'sequence_number': text(organization, 'sequence_number'),
                    'parent_organization_name': (
                        text(parent, 'parent_organization_name')
                        if parent is not None else None),
                })
            return values

        recorded = dict(empty)
        list_author = citation.find('{}List_Author'.format(NAMESPACE))
        if list_author is not None:
            recorded['author_people'] = people(list_author)
            recorded['author_orgs'] = organizations(list_author)

        list_editor = citation.find('{}List_Editor'.format(NAMESPACE))
        if list_editor is not None:
            # The first two Persons are the fixed ATM editors. Only carry the user-added ones.
            recorded['editor_people'] = people(list_editor)[2:]
            recorded['editor_orgs'] = organizations(list_editor)

        return recorded

    # Builders
    def fill_label(self, label_root):

        # Find Identification_Area
        Identification_Area = label_root.find(
            '{}Identification_Area'.format(NAMESPACE))

        # Locate Modification_History within Identification_Area
        Modification_History = Identification_Area.find(
            '{}Modification_History'.format(NAMESPACE))

        # A label made after the citation gets a copy from the bundle label, not a rebuilt skeleton,
        # which would put back blank fields. Only the bundle label's first citation is built here.
        recorded = self._recorded_citation_element()
        if recorded is not None:
            existing = Identification_Area.find('{}Citation_Information'.format(NAMESPACE))
            if existing is not None:
                Identification_Area.replace(existing, recorded)
            elif Modification_History is not None:
                Identification_Area.insert(Identification_Area.index(Modification_History), recorded)
            else:
                Identification_Area.append(recorded)
            return label_root

        # Find Citation_Information.  If no Citation_Information is found, make one.
        Citation_Information = Identification_Area.find('{}Citation_Information'.format(NAMESPACE))
        if Citation_Information is None:
            Citation_Information = etree.Element('{}Citation_Information'.format(NAMESPACE))
        
        # Add Citation_Information information
        if Modification_History is not None:
            Identification_Area.insert(Identification_Area.index(Modification_History), Citation_Information)

        else:
            Identification_Area.append(Citation_Information)
        
        publication_year = etree.SubElement(
            Citation_Information, 'publication_year')
        publication_year.text = self.publication_year
        if self.keyword:
            # Ask how keywords are saved #
            keyword = etree.SubElement(Citation_Information, 'keyword')
            keyword.text = self.keyword
        description = etree.SubElement(Citation_Information, 'description')
        description.text = self.description

        # Use what the bundle already has so a new label gets the names, not blanks.
        recorded = self._recorded_people()

        def recorded_value(kind, index, field):
            values = recorded.get(kind) or []
            return values[index].get(field) if index < len(values) else None

        # Add Citation_Information information
        if self.number_of_authors_people > 0 or self.number_of_authors_organization > 0:
            list_author = etree.SubElement(Citation_Information, 'List_Author')

            # List_Author order: every Organization comes before every Person.
            for index in range(self.number_of_authors_organization):
                organization = etree.SubElement(list_author, 'Organization')
                write_organization_fields(
                    organization,
                    recorded_value('author_orgs', index, 'organization_name'),
                    recorded_value('author_orgs', index, 'organization_rorid'),
                    recorded_value('author_orgs', index, 'sequence_number'),
                    recorded_value('author_orgs', index, 'parent_organization_name'))

            for index in range(self.number_of_authors_people):
                author = etree.SubElement(list_author, 'Person')
                for field in ('given_name', 'family_name', 'person_orcid'):
                    element = etree.SubElement(author, field)
                    element.text = recorded_value('author_people', index, field)
                affiliation = etree.SubElement(author, 'Affiliation')
                organization_name = etree.SubElement(affiliation, 'organization_name')
                organization_name.text = recorded_value(
                    'author_people', index, 'organization_name')


        # Default editors are Lynn Neakrase and Lyle Huber -- Could be changed later on
        list_editor = etree.SubElement(Citation_Information, 'List_Editor')
        # Organizations first, as in List_Author: the schema orders them before Persons.
        for index in range(self.number_of_editors_organization):
            organization = etree.SubElement(list_editor, 'Organization')
            write_organization_fields(
                organization,
                recorded_value('editor_orgs', index, 'organization_name'),
                recorded_value('editor_orgs', index, 'organization_rorid'),
                recorded_value('editor_orgs', index, 'sequence_number'),
                recorded_value('editor_orgs', index, 'parent_organization_name'))

        for given, family, orcid in [
            ('Lynn', 'Neakrase', '0000-0002-6370-5791'),
            ('Lyle', 'Huber',    '0000-0002-1232-1982'),
        ]:
            editor = etree.SubElement(list_editor, 'Person')
            given_name_el = etree.SubElement(editor, 'given_name')
            given_name_el.text = given
            family_name_el = etree.SubElement(editor, 'family_name')
            family_name_el.text = family
            orcid_el = etree.SubElement(editor, 'person_orcid')
            orcid_el.text = orcid
            affiliation = etree.SubElement(editor, 'Affiliation')
            org_name_el = etree.SubElement(affiliation, 'organization_name')
            org_name_el.text = 'NASA Planetary Data System Atmospheres Node; New Mexico State University, Las Cruces, NM'

        # User-added editors go after the two fixed ATM editors. Filled in on the edit page.
        for index in range(self.number_of_editors_people):
            editor = etree.SubElement(list_editor, 'Person')
            for field in ('given_name', 'family_name', 'person_orcid'):
                element = etree.SubElement(editor, field)
                element.text = recorded_value('editor_people', index, field)
            affiliation = etree.SubElement(editor, 'Affiliation')
            organization_name = etree.SubElement(affiliation, 'organization_name')
            organization_name.text = recorded_value(
                'editor_people', index, 'organization_name')

        return label_root

    def sync_into_label(self, label_path):
        """Copy the bundle label's citation into the label at this path. Returns True if it wrote.

        Copies the whole element since labels may disagree on how many authors there are.
        """
        try:
            product_bundle = Product_Bundle.objects.get(bundle=self.bundle)
            source_path = product_bundle.label()
        except (Product_Bundle.DoesNotExist, AttributeError):
            return False
        if not source_path or not os.path.exists(source_path):
            return False
        if os.path.abspath(source_path) == os.path.abspath(label_path):
            return False            # the source of truth needs no copy of itself
        if not os.path.exists(label_path):
            return False

        try:
            source_root = etree.parse(source_path).getroot()
            target_list = open_label_with_tree(label_path)
        except (etree.XMLSyntaxError, OSError):
            return False

        source = source_root.find(
            '{0}Identification_Area/{0}Citation_Information'.format(NAMESPACE))
        if source is None:
            return False

        target_root = target_list[1]
        identification = target_root.find('{}Identification_Area'.format(NAMESPACE))
        if identification is None:
            return False

        replacement = copy.deepcopy(source)
        existing = identification.find('{}Citation_Information'.format(NAMESPACE))
        if existing is not None:
            identification.replace(existing, replacement)
        else:
            # Citation_Information goes before Modification_History, so it can't just be appended.
            modification = identification.find(
                '{}Modification_History'.format(NAMESPACE))
            if modification is not None:
                identification.insert(identification.index(modification), replacement)
            else:
                identification.append(replacement)

        close_label(label_path, target_root, target_list[2])
        return True


    def fill_label_values(self, label_root, cleaned_form):
        # Find Identification_Area
        Identification_Area = label_root.find(
            '{}Identification_Area'.format(NAMESPACE))
        if Identification_Area is None:
            return label_root

        # Find Citation_Information.  If no Citation_Information is found, make one.
        Citation_Information = Identification_Area.find('{}Citation_Information'.format(NAMESPACE))

        if Citation_Information is None:
            # A collection created with no citation doesn't have this element, so copy it whole from
            # the bundle label. The bundle label is first in this loop, so it already has the new values.
            replacement = self._recorded_citation_element()
            if replacement is None:
                return label_root
            modification = Identification_Area.find(
                '{}Modification_History'.format(NAMESPACE))
            if modification is not None:
                Identification_Area.insert(
                    Identification_Area.index(modification), replacement)
            else:
                Identification_Area.append(replacement)
            return label_root

        if self.number_of_authors_people > 0 or self.number_of_authors_organization > 0:
            list_author = Citation_Information.find('{}List_Author'.format(NAMESPACE))

            authors_people = list_author.findall('{}Person'.format(NAMESPACE))

            author_people_count = 0

            for author in authors_people:
                given_name = author.find('{}given_name'.format(NAMESPACE))
                given_name.text = cleaned_form.get(f'author_person_{author_people_count}_given_name')
                family_name = author.find('{}family_name'.format(NAMESPACE))
                family_name.text = cleaned_form.get(f'author_person_{author_people_count}_family_name')
                person_orcid = author.find('{}person_orcid'.format(NAMESPACE))
                person_orcid.text = cleaned_form.get(f'author_person_{author_people_count}_orcid')

                affiliation = author.find('{}Affiliation'.format(NAMESPACE))
                organization_name = affiliation.find('{}organization_name'.format(NAMESPACE))
                organization_name.text = cleaned_form.get(f'author_person_{author_people_count}_affiliation')

                author_people_count = author_people_count + 1

            authors_organization = list_author.findall('{}Organization'.format(NAMESPACE))

            authors_organization_count = 0

            for author in authors_organization:
                _fill_organization(author, cleaned_form, 'author_org', authors_organization_count)
                authors_organization_count = authors_organization_count + 1

        # Default editors are  Lynn Neakrase and Lyle Huber.
        # If the label predates this change it may lack List_Editor - so creates it.
        list_editor = Citation_Information.find('{}List_Editor'.format(NAMESPACE))
        if list_editor is None:
            list_editor = etree.SubElement(Citation_Information, 'List_Editor')
            for given, family, orcid in [
                ('Lynn', 'Neakrase', '0000-0002-6370-5791'),
                ('Lyle', 'Huber',    '0000-0002-1232-1982'),
            ]:
                editor = etree.SubElement(list_editor, 'Person')
                given_name_el = etree.SubElement(editor, 'given_name')
                given_name_el.text = given
                family_name_el = etree.SubElement(editor, 'family_name')
                family_name_el.text = family
                orcid_el = etree.SubElement(editor, 'person_orcid')
                orcid_el.text = orcid
                affiliation = etree.SubElement(editor, 'Affiliation')
                org_name_el = etree.SubElement(affiliation, 'organization_name')
                org_name_el.text = 'NASA Planetary Data System Atmospheres Node; New Mexico State University, Las Cruces, NM'
        else:
            default_editors = [
                {'given_name': 'Lynn', 'family_name': 'Neakrase', 'orcid': '0000-0002-6370-5791'},
                {'given_name': 'Lyle',  'family_name': 'Huber',   'orcid': '0000-0002-1232-1982'},
            ]
            affiliation_text = 'NASA Planetary Data System Atmospheres Node; New Mexico State University, Las Cruces, NM'
            for i, editor in enumerate(list_editor.findall('{}Person'.format(NAMESPACE))):
                if i < len(default_editors):
                    # The first two Persons are the fixed ATM editors; refresh them.
                    editor.find('{}given_name'.format(NAMESPACE)).text = default_editors[i]['given_name']
                    editor.find('{}family_name'.format(NAMESPACE)).text = default_editors[i]['family_name']
                    orcid_el = editor.find('{}person_orcid'.format(NAMESPACE))
                    if orcid_el is not None:
                        orcid_el.text = default_editors[i]['orcid']
                    affiliation = editor.find('{}Affiliation'.format(NAMESPACE))
                    if affiliation is not None:
                        org = affiliation.find('{}organization_name'.format(NAMESPACE))
                        if org is not None:
                            org.text = affiliation_text
                else:
                    # Persons after the fixed pair are user-added, filled from the edit form.
                    u = i - len(default_editors)
                    editor.find('{}given_name'.format(NAMESPACE)).text = cleaned_form.get(f'editor_person_{u}_given_name')
                    editor.find('{}family_name'.format(NAMESPACE)).text = cleaned_form.get(f'editor_person_{u}_family_name')
                    orcid_el = editor.find('{}person_orcid'.format(NAMESPACE))
                    if orcid_el is not None:
                        orcid_el.text = cleaned_form.get(f'editor_person_{u}_orcid')
                    affiliation = editor.find('{}Affiliation'.format(NAMESPACE))
                    if affiliation is not None:
                        org = affiliation.find('{}organization_name'.format(NAMESPACE))
                        if org is not None:
                            org.text = cleaned_form.get(f'editor_person_{u}_affiliation')

            for j, organization in enumerate(list_editor.findall('{}Organization'.format(NAMESPACE))):
                _fill_organization(organization, cleaned_form, 'editor_org', j)

        return label_root
    
    def remove_xml(self, label_root):
        Identification_Area = label_root.find('{}Identification_Area'.format(NAMESPACE))
        if Identification_Area is None:
            return label_root

        Citation_Information = Identification_Area.find('{}Citation_Information'.format(NAMESPACE))
        
        #Added this check to avoid the multiple deletation- Rupak (Temporary solution until we implement the new idea)
        if Citation_Information is not None and Citation_Information.getparent() is not None:
            Citation_Information.getparent().remove(Citation_Information)

        # for alias in Alias_List:
        #     if(alias[0].text.title() == self.alternate_id.title()):
        #         alias.getparent().remove(alias)

        return label_root

    # Meta
    def __str__(self):
        return 'Need to finish this.'


class Modification_History(models.Model):

    bundle = models.ForeignKey(Bundle, on_delete=models.CASCADE)

    description = models.CharField(max_length=MAX_TEXT_FIELD)

    version_id = models.CharField(max_length=MAX_CHAR_FIELD, blank=True)

    def mod_date_default():
        return datetime.date.today().strftime('%Y-%m-%d')

    modification_date = models.CharField(default=mod_date_default, max_length=100)

    # Builders
    def fill_label(self, label_root):

        # Find Identification_Area
        Identification_Area = label_root.find(
            '{}Identification_Area'.format(NAMESPACE))

        # Create Modification_History the first time there's an entry, an empty one fails validation.
        Modification_History = Identification_Area.find(
            '{}Modification_History'.format(NAMESPACE))
        if Modification_History is None:
            Modification_History = etree.SubElement(
                Identification_Area, '{}Modification_History'.format(NAMESPACE))

        # Add Modification_Detail information
        Modification_Detail = etree.SubElement(
            Modification_History, '{}Modification_Detail'.format(NAMESPACE))

        # Create these in the PDS namespace like everything else, so find() works on them.
        modification_date = etree.SubElement(
            Modification_Detail, '{}modification_date'.format(NAMESPACE))
        modification_date.text = self.modification_date
        if self.version_id:
            version_id = etree.SubElement(
                Modification_Detail, '{}version_id'.format(NAMESPACE))
            version_id.text = self.version_id
        description = etree.SubElement(
            Modification_Detail, '{}description'.format(NAMESPACE))
        description.text = self.description

        return label_root

    def remove_xml(self, label_root):
        Identification_Area = label_root.find('{}Identification_Area'.format(NAMESPACE))
        if Identification_Area is None:
            return label_root

        Modification_History = Identification_Area.find(
            '{}Modification_History'.format(NAMESPACE))
        if Modification_History is None:
            # Collection labels don't have a Modification_History until an entry is written, so there may be nothing to remove.
            return label_root

        for modification_detail in Modification_History.findall(
                '{}Modification_Detail'.format(NAMESPACE)):
            # By tag, since version_id is optional and description may not be third.
            description = modification_detail.find('{}description'.format(NAMESPACE))
            if description is None or not description.text or not self.description:
                continue
            if description.text.title() == self.description.title():
                Modification_History.remove(modification_detail)

        # A Modification_History with nothing left in it is not valid PDS4.
        if not len(Modification_History):
            Identification_Area.remove(Modification_History)

        return label_root

    # Meta
    def __str__(self):
        return 'Need to finish this.'


"""
    The Table model object can be one of the four accepted table types given in PDS4.
"""


# @python_2_unicode_compatible
class Table(models.Model):

    OBSERVATIONAL_TYPES = [
        ('Table Base', 'Table Base'),
        ('Table Binary', 'Table Binary'),
        ('Table Character', 'Table Character'),
        ('Table Delimited', 'Table Delimited'),
    ]
    product_observational = models.ForeignKey(
        Product_Observational, on_delete=models.CASCADE)
    name = models.CharField(max_length=MAX_CHAR_FIELD)
    observational_type = models.CharField(
        max_length=MAX_CHAR_FIELD, choices=OBSERVATIONAL_TYPES)
    local_identifier = models.CharField(max_length=MAX_CHAR_FIELD)
    offset = models.CharField(max_length=MAX_CHAR_FIELD)
    object_length = models.CharField(max_length=MAX_CHAR_FIELD)
    description = models.CharField(max_length=MAX_CHAR_FIELD)
    records = models.CharField(max_length=MAX_CHAR_FIELD)
    fields = models.CharField(max_length=MAX_CHAR_FIELD)
    groups = models.CharField(max_length=MAX_CHAR_FIELD)

    # meta

    def __str__(self):
        return 'Table Binary: {}'.format(self.name)


"""
    The Array model object defines a homogeneous N-dimensional array of scalars. The Array class is the parent class for all n-dimensional arrays of scalars.
"""


# @python_2_unicode_compatible
class Array(models.Model):

    ARRAY_DIMENSIONS = [
        ('Array_2D', 'Array 2D'),
        ('Array_3D', 'Array 3D'),
    ]
    ARRAY_TYPES = [
        ('Image', 'Image'),
        ('Map', 'Map'),
        ('Spectrum', 'Spectrum'),

        # jacob's tables for data
        #    OBSERVATIONAL_TYPES = [
        #        ('Table Base', 'Table Base'),
        #        ('Table Binary','Table Binary'),
        #        ('Table Character','Table Character'),
        #        ('Table Delimited','Table Delimited'),
    ]
    product_observational = models.ForeignKey(
        Product_Observational, on_delete=models.CASCADE)
    name = models.CharField(max_length=MAX_CHAR_FIELD)

    array_dimensions = models.CharField(
        max_length=MAX_CHAR_FIELD, choices=ARRAY_DIMENSIONS)
    array_type = models.CharField(
        max_length=MAX_CHAR_FIELD, choices=ARRAY_TYPES)
    local_identifier = models.CharField(max_length=MAX_CHAR_FIELD)
    offset = models.CharField(max_length=MAX_CHAR_FIELD)
    axes = models.CharField(max_length=MAX_CHAR_FIELD)
    axis_index_order = models.CharField(max_length=MAX_CHAR_FIELD)
    description = models.CharField(max_length=MAX_CHAR_FIELD)
    # Has associations @ https://pds.nasa.gov/datastandards/documents/dd/v1/PDS4_PDS_DD_1A00.html#d5e3181

    # meta

    def __str__(self):
        return 'Array: {}'.format(self.name)

    # fillers

    def build_array(self, label_root):

        # Find File_Area_Observational
        File_Area_Observational = label_root.find(
            '{}File_Area_Observational'.format(NAMESPACE))

        # Add Array to File_Area_Observational given the dimension and type of the array.

        if self.array_dimensions == 'Array_2D' and self.array_type == 'Image':
            Array = etree.SubElement(File_Area_Observational, 'Array_2D_Image')
        elif self.array_dimensions == 'Array_2D' and self.array_type == 'Map':
            Array = etree.SubElement(File_Area_Observational, 'Array_2D_Map')
        elif self.array_dimensions == 'Array_2D' and self.array_type == 'Spectrum':
            Array = etree.SubElement(
                File_Area_Observational, 'Array_2D_Spectrum')
        elif self.array_dimensions == 'Array_3D' and self.array_type == 'Image':
            Array = etree.SubElement(File_Area_Observational, 'Array_3D_Image')
        elif self.array_dimensions == 'Array_3D' and self.array_type == 'Map':
            Array = etree.SubElement(File_Area_Observational, 'Array_3D_Map')
        elif self.array_dimensions == 'Array_3D' and self.array_type == 'Spectrum':
            Array = etree.SubElement(
                File_Area_Observational, 'Array_3D_Spectrum')

        # Add Array information
        if self.offset:
            offset = etree.SubElement(Array, 'offset')
            offset.text = self.offset
        if self.axes:
            axes = etree.SubElement(Array, 'axes')
            axes.text = self.axes
        if self.axis_index_order:
            axis_index_order = etree.SubElement(Array, 'axis_index_order')
            axis_index_order.text = self.axis_index_order
        if self.description:
            description = etree.SubElement(Array, 'description')
            description.text = self.description

        return label_root


# @python_2_unicode_compatible
class DisplayDictionary(models.Model):
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
    data = models.OneToOneField(
        Data, on_delete=models.CASCADE, primary_key=True,)

    def __str__(self):
        return "Display Dictionary"

    def _write_schema_namespace(self):
        """
        write_schema_namespace
        inputs:
        outputs:
        purpose:
        """


# @python_2_unicode_compatible
class Color_Display_Settings(models.Model):
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
#    color_display_axis = models.PositiveIntegerField() # max value 255
    display_dictionary = models.OneToOneField(
        DisplayDictionary, on_delete=models.CASCADE)
    color_display_axis = models.PositiveIntegerField(
        validators=[
            MaxValueValidator(255)
        ]
    )  # max value 255
    comment_color_display = models.CharField(max_length=MAX_CHAR_FIELD)
    red_channel_band = models.PositiveIntegerField(
        validators=[
            MaxValueValidator(255)
        ]
    )  # Big integer is better for
    green_channel_band = models.PositiveIntegerField(
        validators=[
            MaxValueValidator(255)
        ]
    )  # pds4 specs for these
    blue_channel_band = models.PositiveIntegerField(
        validators=[
            MaxValueValidator(255)
        ]
    )  # bands

#    red_channel_band = models.PositiveIntegerField() # Big integer is better for
#    green_channel_band = models.PositiveIntegerField() # pds4 specs for these
#    blue_channel_band = models.PositiveIntegerField() # bands

    # Color_Display_Settings
    def __str__(self):
        return "How you actually make a dictionary >.<"


# @python_2_unicode_compatible
class Display_Direction(models.Model):
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
    HORIZONTAL_DISPLAY_DIRECTION_CHOICES = [
        ('left_to_right', 'Left to Right'),
        ('right_to_left', 'Right to Left'),
    ]
    VERTICAL_DISPLAY_DIRECTION_CHOICES = [
        ('bottom_to_top', 'Bottom to Top'),
        ('top_to_bottom', 'Top to Bottom'),
    ]
    display_dictionary = models.OneToOneField(
        DisplayDictionary, on_delete=models.CASCADE)
    comment_display_direction = models.CharField(max_length=MAX_CHAR_FIELD)
    horizontal_display_axis = models.PositiveIntegerField(
        validators=[
            MaxValueValidator(255)
        ]
    )  # max value 255

    horizontal_display_direction = models.CharField(
        max_length=13,
        choices=HORIZONTAL_DISPLAY_DIRECTION_CHOICES,
    )

    vertical_display_axis = models.PositiveIntegerField(
        validators=[
            MaxValueValidator(255)
        ]
    )  # max value 255
    vertical_display_direction = models.CharField(
        max_length=13,
        choices=HORIZONTAL_DISPLAY_DIRECTION_CHOICES,
    )

#    horizontal_display_axis = models.PositiveIntegerField() # max value 255
#    horizontal_display_direction = models.PositiveIntegerField() # max value 255
#    vertical_display_axis = models.PositiveIntegerField() # max value 255
#    vertical_display_direction = models.PositiveIntegerField() # max value 255

    # Color_Display_Settings
    def __str__(self):
        return "How you actually make a dictionary >.<"


# @python_2_unicode_compatible
class Display_Settings(models.Model):
    pass
    #Local_Internal_Reference = models.CharField(max_length=MAX_CHAR_FIELD)
    #Display_Direction = models.CharField(max_length=MAX_CHAR_FIELD)
    #Color_Display_Settings = models.CharField(max_length=MAX_CHAR_FIELD)
    #Movie_Display_Settings = models.CharField(max_length=MAX_CHAR_FIELD)

    # Color_Display_Settings
    def __str__(self):
        return "How you actually make a dictionary >.<"

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


"""
    time_display_axis = models.PositiveIntegerField() # max 255
    comment = models.CharField(max_length=MAX_CHAR_FIELD)
    frame_rate = models.FloatField() # min_value=1.0
    loop_flag = models.BooleanField()
    loop_count = models.PositiveIntegerField()
    loop_delay = models.FloatField() # min_length=0.0
    loop_back_and_forth_flag = models.BooleanField()

    #Color_Display_Settings
    def __str__(self):
        return "How you actually make a dictionary >.<"
"""


# @python_2_unicode_compatible
class Movie_Display_Settings(models.Model):
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
#    time_display_axis = models.PositiveIntegerField() # max 255
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
    display_dictionary = models.OneToOneField(
        DisplayDictionary, on_delete=models.CASCADE)
    time_display_axis = models.PositiveIntegerField(
        validators=[
            MaxValueValidator(255)
        ]
    )  # max 255
    comment = models.CharField(max_length=MAX_CHAR_FIELD)
#    frame_rate = models.FloatField() # min_value=1.0
    frame_rate = models.FloatField(
        validators=[
            MinValueValidator(1.0)
        ]
    )  # min_value=1.0
    loop_flag = models.BooleanField()
    loop_count = models.PositiveIntegerField()
#    loop_delay = models.FloatField() # min_length=0.0
    loop_delay = models.FloatField(
        validators=[
            MinValueValidator(0.0)
        ]
    )  # min_length=0.0
    loop_delay_unit = models.CharField(
        max_length=20,
        choices=LOOP_DELAY_UNIT_CHOICES,
    )
    loop_back_and_forth_flag = models.BooleanField()

    # Color_Display_Settings
    def __str__(self):
        return "How you actually make a dictionary >.<"

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


"""
@python_2_unicode_compatible
class DisplayDictionary(models.Model):

    Color_Display_Settings = models.ForeignKey(Color_Display_Settings, on_delete=models.CASCADE)
    Display_Direction = models.ForeignKey(Display_Direction, on_delete=models.CASCADE)
    Display_Settings = models.ForeignKey(Display_Settings, on_delete=models.CASCADE)
    #Movie_Display_Settings = models.ForeignKey(Movie_Display_Settings, on_delete=models.CASCADE)


    #Color_Display_Settings
    def __str__(self):
        return "How you actually make a dictionary >.<"

"""


# To handle NetCDF files

class NetCDFFile(models.Model):
    title = models.CharField(max_length=255)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    # NetCDF files are validated by magic bytes (see chocolate.is_netcdf_file), not by
    # extension, so no FileExtensionValidator here — uploads may be extensionless.
    file = models.FileField(upload_to='')
    bundle = models.ForeignKey(Bundle, on_delete=models.CASCADE, related_name='netcdf_files', null=True, blank=True)
    # Which collection this file was uploaded into. Null only for older rows.
    collection = models.ForeignKey(
        AdditionalCollections, on_delete=models.CASCADE, related_name='netcdf_files',
        null=True, blank=True)
    # Tracks whether metadata extraction / XML label generation succeeded for this file.
    processed = models.BooleanField(default=False)
    processing_error = models.TextField(blank=True, default='')

    def __str__(self):
        return self.title

    def directory(self):
        """Where this file and its label are on disk.

        Falls back to the bundle root for older rows.
        """
        if self.collection_id is not None:
            return self.collection.directory()
        return self.bundle.directory()

    def label(self):
        """The PDS4 label for this file.

        Same naming as _process_single_netcdf: drop a trailing .nc if there is one and add .xml.
        """
        base = os.path.basename(self.file.name)
        stem = base[:-3] if base.endswith('.nc') else base
        return os.path.join(self.directory(), stem + '.xml')

    def stored_path(self):
        """Where this file really is on disk, or None.

        file.path and file.url are stale after processing moves the file. Checks the collection
        directory, then the bundle root for older rows, then the upload area.
        """
        name = os.path.basename(self.file.name)
        candidates = [os.path.join(self.directory(), name)]
        if self.bundle_id is not None:
            candidates.append(os.path.join(self.bundle.directory(), name))
        try:
            candidates.append(self.file.path)
        except ValueError:
            pass
        for candidate in candidates:
            if os.path.isfile(candidate):
                return candidate
        return None


class NetCDFUpload(models.Model):
    """A NetCDF file on its way up in pieces (the tus protocol, see build/netcdf_uploads.py).

    The bytes collect in a staging file named after the id. Once every byte is in, the finish
    step turns it into a normal NetCDFFile and the row stays behind pointing at it.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='netcdf_uploads')
    bundle = models.ForeignKey(Bundle, on_delete=models.CASCADE, related_name='netcdf_uploads')
    collection = models.ForeignKey(
        AdditionalCollections, on_delete=models.CASCADE, related_name='netcdf_uploads')
    # The name the user picked, and the name it will have on disk.
    filename = models.CharField(max_length=255)
    stored_name = models.CharField(max_length=255)
    length = models.BigIntegerField()
    offset = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    netcdf_file = models.ForeignKey(
        NetCDFFile, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    class Meta:
        indexes = [models.Index(fields=['user', 'finished_at'])]

    def __str__(self):
        return '{} ({} of {} bytes)'.format(self.filename, self.offset, self.length)

    @property
    def complete(self):
        return self.offset >= self.length

    def staging_path(self):
        from build import netcdf_uploads
        return netcdf_uploads.staging_path(self.id)


@receiver(post_delete, sender=NetCDFUpload)
def remove_staged_upload(sender, instance, **kwargs):
    # Runs for cascades too, so deleting a bundle or collection mid-upload leaves nothing behind.
    # After finish the staging file is already gone, and the archive copy is not touched.
    try:
        os.remove(instance.staging_path())
    except FileNotFoundError:
        pass
    except OSError as exc:
        logging.getLogger(__name__).warning('could not remove staged upload %s: %s', instance.id, exc)


# --- AMA Discipline Dictionary user-supplied classes ---
# The NetCDF harvest fills ama:Variable and ama:Coordinate. The rest comes from these models:
# 
# ModelMetadata           -> ama:Model_Metadata
# SimulationConfiguration -> ama:Simulation_Configuration
# FileDescription         -> ama:Model_Output/ama:File_Description
# 
# All three have a per-collection default that files inherit, plus an optional per-file override.
# `bundle` has to match `collection.bundle`.
# 
# Field types follow PDS4_AMA_1O00_1300.xsd:
# - strings are ASCII_Short_String_Collapsed, minLength=1, maxLength=255
# - description is ASCII_Text_Collapsed, minLength=1
# - model_timestep / upper_boundary / lower_boundary / top_level / bottom_level are ASCII_Real
# - the four compass boundaries are ASCII_Real with ranges and a required unit
# minLength=1 means blank values have to be left out of the label.


class AMACollectionScopedMixin(models.Model):
    """Shared logic for the AMA classes: a collection default plus per-file overrides.

    `netcdf_file` NULL is the collection default. Set, it overrides that one file.
    MariaDB allows multiple NULLs in a UNIQUE index, so always use `default_for_collection()`.
    """

    class Meta(object):
        abstract = True

    @classmethod
    def default_for_collection(cls, collection):
        """Return the collection's default row, or None if nothing has been filled in."""
        if collection is None:
            return None
        return cls.objects.filter(collection=collection, netcdf_file__isnull=True).first()

    @classmethod
    def override_for_file(cls, netcdf_file):
        """Return this file's override row, or None if the file follows its collection default."""
        if netcdf_file is None:
            return None
        return cls.objects.filter(netcdf_file=netcdf_file).first()

    @classmethod
    def resolve_for_file(cls, netcdf_file):
        """The row whose values belong in this file's label: override first, else the default."""
        override = cls.override_for_file(netcdf_file)
        if override is not None:
            return override
        if netcdf_file is None:
            return None
        return cls.default_for_collection(netcdf_file.collection)

    @classmethod
    def apply_to_collection(cls, source, collection):
        """Push one row's values out to every file in the collection.

        Writes them on the default and deletes the overrides, so later edits to the default still
        reach the files. Returns how many overrides were cleared.
        """
        default = cls.default_for_collection(collection)
        if default is None:
            default = cls(collection=collection, netcdf_file=None)
        default.copy_values_from(source)
        default.bundle = collection.bundle
        default.save()

        return cls.objects.filter(
            collection=collection, netcdf_file__isnull=False).exclude(pk=default.pk).delete()[0]

    def is_default(self):
        return self.netcdf_file_id is None

    def filled_values(self):
        """Ordered {element_name: text} of non-blank values for the label, in `ELEMENT_ORDER`."""
        values = {}
        for field_name in self.ELEMENT_ORDER:
            value = getattr(self, field_name)
            if value is None:
                continue
            text = str(value).strip()
            if text == '':
                continue
            values[field_name] = text
        return values

    def has_any_value(self):
        return bool(self.filled_values())

    def copy_values_from(self, other):
        """Copy every AMA content field from `other` (leaves collection/netcdf_file alone)."""
        for field_name in self.ELEMENT_ORDER:
            setattr(self, field_name, getattr(other, field_name))


class ModelMetadata(AMACollectionScopedMixin):
    """ama:Model_Metadata: which model produced the files, per collection."""

    ELEMENT_ORDER = ('type', 'name', 'version', 'institution')
    BOUNDARY_UNIT_ATTRIBUTE = {}

    bundle = models.ForeignKey(
        Bundle, on_delete=models.CASCADE, related_name='model_metadata_set')
    collection = models.ForeignKey(
        AdditionalCollections, on_delete=models.CASCADE, related_name='model_metadata_set',
        null=True, blank=True)
    netcdf_file = models.OneToOneField(
        NetCDFFile, on_delete=models.CASCADE, related_name='model_metadata',
        null=True, blank=True)

    type = models.CharField(max_length=MAX_CHAR_FIELD, blank=True, default='')
    name = models.CharField(max_length=MAX_CHAR_FIELD, blank=True, default='')
    version = models.CharField(max_length=MAX_CHAR_FIELD, blank=True, default='')
    institution = models.CharField(max_length=MAX_CHAR_FIELD, blank=True, default='')

    def __str__(self):
        if self.netcdf_file_id is None:
            return 'Model Metadata default for {}'.format(self.collection)
        return 'Model Metadata for {}'.format(self.netcdf_file)


class SimulationConfiguration(AMACollectionScopedMixin):
    """ama:Simulation_Configuration - collection default plus optional per-file overrides."""

    ELEMENT_ORDER = (
        'horizontal_grid_type',
        'model_resolution',
        'model_resolution_unit',
        'vertical_grid_type',
        'vertical_grid_unit',
        'model_timestep',
        'model_timestep_unit',
        'upper_boundary',
        'lower_boundary',
        'northern_boundary',
        'southern_boundary',
        'eastern_boundary',
        'western_boundary',
        'start_time',
        'end_time',
        'time_unit',
        'description',
    )

    # These need a unit in the LDD. The template uses degrees, so write the same.
    BOUNDARY_UNIT_ATTRIBUTE = {
        'northern_boundary': 'deg',
        'southern_boundary': 'deg',
        'eastern_boundary': 'deg',
        'western_boundary': 'deg',
    }

    bundle = models.ForeignKey(
        Bundle, on_delete=models.CASCADE, related_name='simulation_configurations')
    collection = models.ForeignKey(
        AdditionalCollections, on_delete=models.CASCADE, related_name='simulation_configurations',
        null=True, blank=True)
    netcdf_file = models.OneToOneField(
        NetCDFFile, on_delete=models.CASCADE, related_name='simulation_configuration',
        null=True, blank=True)

    horizontal_grid_type = models.CharField(max_length=MAX_CHAR_FIELD, blank=True, default='')
    model_resolution = models.CharField(max_length=MAX_CHAR_FIELD, blank=True, default='')
    model_resolution_unit = models.CharField(max_length=MAX_CHAR_FIELD, blank=True, default='')
    vertical_grid_type = models.CharField(max_length=MAX_CHAR_FIELD, blank=True, default='')
    vertical_grid_unit = models.CharField(max_length=MAX_CHAR_FIELD, blank=True, default='')
    model_timestep = models.FloatField(null=True, blank=True)
    model_timestep_unit = models.CharField(max_length=MAX_CHAR_FIELD, blank=True, default='')
    upper_boundary = models.FloatField(null=True, blank=True)
    lower_boundary = models.FloatField(null=True, blank=True)
    northern_boundary = models.FloatField(
        null=True, blank=True,
        validators=[MinValueValidator(-90.0), MaxValueValidator(90.0)])
    southern_boundary = models.FloatField(
        null=True, blank=True,
        validators=[MinValueValidator(-90.0), MaxValueValidator(90.0)])
    eastern_boundary = models.FloatField(
        null=True, blank=True,
        validators=[MinValueValidator(-180.0), MaxValueValidator(360.0)])
    western_boundary = models.FloatField(
        null=True, blank=True,
        validators=[MinValueValidator(-180.0), MaxValueValidator(360.0)])
    start_time = models.CharField(max_length=MAX_CHAR_FIELD, blank=True, default='')
    end_time = models.CharField(max_length=MAX_CHAR_FIELD, blank=True, default='')
    time_unit = models.CharField(max_length=MAX_CHAR_FIELD, blank=True, default='')
    description = models.TextField(blank=True, default='')

    def __str__(self):
        if self.netcdf_file_id is None:
            return 'Simulation Configuration default for {}'.format(self.collection)
        return 'Simulation Configuration for {}'.format(self.netcdf_file)


class FileDescription(AMACollectionScopedMixin):
    """ama:Model_Output/ama:File_Description: collection default plus per-file overrides.

    Usually differs per file, since each one covers different times or altitudes.
    """

    ELEMENT_ORDER = (
        'top_level',
        'bottom_level',
        'level_unit',
        'start_time',
        'end_time',
        'time_unit',
        'postprocessing_methods',
    )

    BOUNDARY_UNIT_ATTRIBUTE = {}

    bundle = models.ForeignKey(
        Bundle, on_delete=models.CASCADE, related_name='file_descriptions')
    collection = models.ForeignKey(
        AdditionalCollections, on_delete=models.CASCADE, related_name='file_descriptions',
        null=True, blank=True)
    netcdf_file = models.OneToOneField(
        NetCDFFile, on_delete=models.CASCADE, related_name='file_description',
        null=True, blank=True)

    top_level = models.FloatField(null=True, blank=True)
    bottom_level = models.FloatField(null=True, blank=True)
    level_unit = models.CharField(max_length=MAX_CHAR_FIELD, blank=True, default='')
    start_time = models.CharField(max_length=MAX_CHAR_FIELD, blank=True, default='')
    end_time = models.CharField(max_length=MAX_CHAR_FIELD, blank=True, default='')
    time_unit = models.CharField(max_length=MAX_CHAR_FIELD, blank=True, default='')
    postprocessing_methods = models.CharField(max_length=MAX_CHAR_FIELD, blank=True, default='')

    def __str__(self):
        if self.netcdf_file_id is None:
            return 'File Description default for {}'.format(self.collection)
        return 'File Description for {}'.format(self.netcdf_file)

# --- PDS validation runs ---


class ValidationRun(models.Model):
    """One run of the PDS validate tool on one bundle.

    STRUCTURE skips content validation and takes seconds. FULL reads inside every data file
    and has to happen before submission.
    """

    STATUS_QUEUED = 'q'
    STATUS_RUNNING = 'r'
    STATUS_DONE = 'd'
    STATUS_FAILED = 'f'
    STATUS_CHOICES = (
        (STATUS_QUEUED, 'Queued'),
        (STATUS_RUNNING, 'Running'),
        (STATUS_DONE, 'Complete'),
        (STATUS_FAILED, 'Failed'),
    )

    TIER_STRUCTURE = 'structure'
    TIER_FULL = 'full'
    TIER_CHOICES = (
        (TIER_STRUCTURE, 'Structure only'),
        (TIER_FULL, 'Full, including data content'),
    )

    # validate's phases, in order. It prints nothing for about five seconds while compiling schemas.
    PHASE_LOADING = 'loading'
    PHASE_LABELS = 'labels'
    PHASE_CONTENT = 'content'
    PHASE_REFERENCES = 'references'
    PHASE_DONE = 'done'

    # Passes each tier makes over the products: two for structure, three for full.
    # validate prints a content counter even when it's skipped, so go by this list.
    PASSES = {
        TIER_STRUCTURE: [PHASE_LABELS, PHASE_REFERENCES],
        TIER_FULL: [PHASE_LABELS, PHASE_CONTENT, PHASE_REFERENCES],
    }

    bundle = models.ForeignKey(Bundle, on_delete=models.CASCADE, related_name='validation_runs')
    tier = models.CharField(max_length=16, choices=TIER_CHOICES, default=TIER_STRUCTURE)
    status = models.CharField(max_length=1, choices=STATUS_CHOICES, default=STATUS_QUEUED)

    requested_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    # Snapshot of the bundle as it was when this run started. Kept for runs recorded
    # before content_fingerprint existed, and as a fallback when the bundle directory
    # cannot be read; see is_stale().
    bundle_updated_at = models.DateTimeField(null=True, blank=True)

    # What the bundle's files looked like when this run started.
    #
    # Staleness used to be Bundle.updated_at moving, which sounds right and never
    # worked: updated_at is auto_now, so it only changes when Bundle.save() is
    # called, and not one of the views that edits a bundle's metadata saves the
    # bundle. Adding a modification history wrote four labels and left updated_at
    # exactly where it was, so no result was ever stale and the automatic check
    # never had a reason to fire.
    #
    # The validator reads files, so this is what a fingerprint of those files says.
    # It cannot be forgotten by a view that writes a label without telling anyone.
    content_fingerprint = models.CharField(max_length=64, blank=True, default='')

    # Progress, driven by the counters validate streams as it works.
    phase = models.CharField(max_length=16, default=PHASE_LOADING)
    products_total = models.PositiveIntegerField(default=0)
    products_done = models.PositiveIntegerField(default=0)

    error_count = models.PositiveIntegerField(default=0)
    warning_count = models.PositiveIntegerField(default=0)

    # Where the raw JSON report is, and the findings parsed from it.
    report_path = models.CharField(max_length=MAX_CHAR_FIELD, blank=True, default='')
    findings = models.JSONField(null=True, blank=True)

    # The validate version from the report, e.g. '5.0.0'. Blank if there was no report.
    # Nullable since older code may still insert rows without this column during a deploy.
    tool_version = models.CharField(max_length=32, null=True, blank=True, default='')

    # build.checker.checker_version() when this ran. A different one means ELSA's checks changed.
    # Rows from before this field have '', so they count as out of date.
    checker_version = models.CharField(max_length=64, null=True, blank=True,
                                       default=current_checker_version)

    # Why a run couldn't produce a report: validate or Java missing, a crash, a timeout.
    failure_reason = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-requested_at']
        indexes = [
            models.Index(fields=['bundle', '-requested_at']),
        ]

    def __str__(self):
        return '{} validation of {} ({})'.format(
            self.get_tier_display(), self.bundle.name, self.get_status_display())

    def is_active(self):
        """Queued or running, so a second request should join it rather than start another."""
        return self.status in (self.STATUS_QUEUED, self.STATUS_RUNNING)

    def is_stale(self):
        """True when the bundle changed after this run started.

        The findings still describe a real bundle, just not the current one. Saying so
        is the difference between a user trusting the panel and learning to ignore it:
        the alternative is showing them errors they already fixed, or a clean result
        that no longer applies.

        Measured against the files, because that is what was validated. The timestamp
        comparison this used to do could never be true: Bundle.updated_at is auto_now
        and no view that edits a bundle's metadata saves the bundle.

        Runs recorded before the fingerprint existed fall back to the timestamp, which
        is wrong in the same old way but is all those rows have; they age out.

        Also True when ELSA's checks changed after this run, see stale_reason().
        """
        return self.stale_reason() is not None

    STALE_FILES = 'files'
    STALE_CHECKS = 'checks'

    def stale_reason(self):
        """'files' if the bundle changed after this run, 'checks' if ELSA's checks did, else None.

        A run with no checker version is from before it was recorded, so it counts as 'checks'.
        """
        if self.content_fingerprint:
            current = self.bundle.content_fingerprint()
            if not current:
                # The directory is gone, so leave the last result. Re-running wouldn't help.
                return None
            if current != self.content_fingerprint:
                return self.STALE_FILES
        elif (self.bundle_updated_at is not None and self.bundle.updated_at is not None
              and self.bundle.updated_at > self.bundle_updated_at):
            return self.STALE_FILES

        # Queued and running rows get their version when they start.
        if self.status in (self.STATUS_DONE, self.STATUS_FAILED):
            from build.checker import checker_version
            if (self.checker_version or '') != checker_version():
                return self.STALE_CHECKS
        return None

    def stale_because_of_checks(self):
        return self.stale_reason() == self.STALE_CHECKS

    def duration_seconds(self):
        if self.started_at is None or self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    def percent_complete(self):
        """0-100, or None while validate is still compiling schemas.

        Each pass is an equal share of the bar.
        """
        if self.status == self.STATUS_DONE:
            return 100
        if self.phase == self.PHASE_LOADING or not self.products_total:
            return None

        passes = self.PASSES.get(self.tier, [self.PHASE_LABELS])
        if self.phase not in passes:
            return None

        finished = passes.index(self.phase)
        within = min(1.0, self.products_done / self.products_total)
        return min(100, int(100 * (finished + within) / len(passes)))

    def phase_label(self):
        """What this run is doing, in words, for the progress indicator."""
        return {
            self.PHASE_LOADING: 'Loading PDS schemas',
            self.PHASE_LABELS: 'Checking labels',
            self.PHASE_CONTENT: 'Checking data files',
            self.PHASE_REFERENCES: 'Checking references',
            self.PHASE_DONE: 'Finished',
        }.get(self.phase, self.phase)


class BundleUpgrade(models.Model):
    """One label migration ELSA ran on a bundle that changed something, or failed.

    The owner sees these under "Changes made by ELSA". The snapshot is a copy of the labels
    from just before, so the change can be undone.
    """

    STATUS_APPLIED = 'applied'
    STATUS_FAILED = 'failed'
    STATUS_RESTORED = 'restored'
    STATUS_CHOICES = (
        (STATUS_APPLIED, 'Applied'),
        (STATUS_FAILED, 'Failed'),
        (STATUS_RESTORED, 'Undone'),
    )

    bundle = models.ForeignKey(Bundle, on_delete=models.CASCADE, related_name='upgrades')
    number = models.PositiveIntegerField()
    name = models.CharField(max_length=MAX_CHAR_FIELD)
    title = models.CharField(max_length=MAX_CHAR_FIELD)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_APPLIED)
    # Short notes from the migration, and the files it changed.
    changes = models.JSONField(default=list, blank=True)
    files = models.JSONField(default=list, blank=True)
    error = models.TextField(blank=True, default='')
    # page, check, submit, sweep or staff
    source = models.CharField(max_length=16, blank=True, default='')
    snapshot = models.CharField(max_length=MAX_CHAR_FIELD, blank=True, default='')
    applied_at = models.DateTimeField(auto_now_add=True)
    # When the owner dismissed the notice.
    seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-applied_at', '-pk']

    def __str__(self):
        return '{:04d}_{} on {} ({})'.format(self.number, self.name, self.bundle_id, self.status)

    def change_summary(self):
        """The notes in plain words, repeats counted."""
        from build.label_upgrades import describe
        return describe(self.changes)

    def files_summary(self):
        """e.g. '2 labels and 1 inventory table'."""
        from build.label_upgrades import files_phrase
        return files_phrase(self.files)

    def file_entries(self):
        """The files as {'path', 'removed'}, for linking to the File Viewer."""
        return [{'path': path[:-len(' (removed)')], 'removed': True} if path.endswith(' (removed)')
                else {'path': path, 'removed': False} for path in self.files or []]


class Citation_Contributor(models.Model):
    """One author or editor of a citation, a person or an organization.

    The citation in every label is written from these (build.bundle_setup.write_citation).
    The two fixed ATM editors aren't stored, they're always added.
    """

    ROLE_CHOICES = (('author', 'Author'), ('editor', 'Editor'))
    KIND_CHOICES = (('person', 'Person'), ('organization', 'Organization'))

    citation = models.ForeignKey(Citation_Information, on_delete=models.CASCADE,
                                 related_name='contributors')
    role = models.CharField(max_length=8, choices=ROLE_CHOICES)
    kind = models.CharField(max_length=12, choices=KIND_CHOICES)
    position = models.PositiveIntegerField(default=0)
    # Person
    given_name = models.CharField(max_length=MAX_CHAR_FIELD, blank=True, default='')
    family_name = models.CharField(max_length=MAX_CHAR_FIELD, blank=True, default='')
    orcid = models.CharField(max_length=MAX_CHAR_FIELD, blank=True, default='')
    affiliation = models.CharField(max_length=MAX_CHAR_FIELD, blank=True, default='')
    # Organization
    name = models.CharField(max_length=MAX_CHAR_FIELD, blank=True, default='')
    rorid = models.CharField(max_length=MAX_CHAR_FIELD, blank=True, default='')
    sequence_number = models.CharField(max_length=MAX_CHAR_FIELD, blank=True, default='')
    parent_org_name = models.CharField(max_length=MAX_CHAR_FIELD, blank=True, default='')

    class Meta:
        ordering = ['role', 'kind', 'position', 'pk']

    PERSON_FIELDS = ('given_name', 'family_name', 'orcid', 'affiliation')
    ORG_FIELDS = ('name', 'rorid', 'sequence_number', 'parent_org_name')

    def group(self):
        return '{}_{}'.format(self.role, 'people' if self.kind == 'person' else 'orgs')

    def as_row(self):
        fields = self.PERSON_FIELDS if self.kind == 'person' else self.ORG_FIELDS
        return {field: getattr(self, field) for field in fields}

    def __str__(self):
        if self.kind == 'person':
            return '{} {} ({})'.format(self.given_name, self.family_name, self.role)
        return '{} ({})'.format(self.name, self.role)


@receiver(post_delete, sender=Bundle)
def remove_upgrade_snapshots(sender, instance, **kwargs):
    # Snapshots live outside the bundle directory, so delete them with the bundle.
    from build.label_upgrades import snapshot_dir_for
    shutil.rmtree(snapshot_dir_for(instance.pk), ignore_errors=True)
