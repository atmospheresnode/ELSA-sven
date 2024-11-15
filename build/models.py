# Stdlib imports
# -*- coding: UTF-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

from builtins import str
from builtins import object
from enum import unique
from queue import Empty
from django.db import models
from django.conf import settings
from django.contrib.auth.models import User
from django.core.validators import MaxValueValidator, MinValueValidator
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
import shutil
import os
import copy



#    Final Variables ------------------------------------------------------------------------------------
MAX_CHAR_FIELD = 100
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
        version_number = number
        new_number = ""
        i = 0

        while(i < 4):
            if number[i].isalpha() is False:
                new_number = new_number + number[i] + "."
            else:
                if number[i] == 'D':
                    new_number = new_number + '13' + "."
                if number[i] == 'E':
                    new_number = new_number + '14' + "."
                if number[i] == 'F':
                    new_number = new_number + '15' + "."
                if number[i] == 'G':
                    new_number = new_number + '16' + "."
                if number[i] == 'H':
                    new_number = new_number + '17' + "."
                if number[i] == 'I':
                    new_number = new_number + '18' + "."
                if number[i] == 'J':
                    new_number = new_number + '19' + "."
                if number[i] == 'K':
                    new_number = new_number + '20' + "."
            i = i + 1

        '''
        # Add a period after each digit.  Ex: 1234 -> 1.2.3.4.
        for each_digit in version_number:
            print new_number
            if each_digit.isalpha() is False:
                new_number = new_number + each_digit + "."#'{0}{1}{2}'.format(new_number, each_digit, '.')
            else:
                print type(each_digit)
                if each_digit is "A":
                    print each_digit
                    new_number = '{0}{1}{2}'.format(new_number, '10', '.')
                elif each_digit is 'B':
                    new_number = '{0}{1}{2}'.format(new_number, '11', '.')
                elif each_digit is 'C':
                    new_number = '{0}{1}{2}'.format(new_number, '12', '.')
                elif each_digit is 'D':
                    new_number = '{0}{1}{2}'.format(new_number, '13', '.')
                elif each_digit is 'E':
                    new_number = '{0}{1}{2}'.format(new_number, '14', '.')
                elif each_digit is 'F':
                    new_number = '{0}{1}{2}'.format(new_number, '15', '.')
                elif each_digit is 'G':
                    new_number = '{0}{1}{2}'.format(new_number. '16', '.')
        '''
        print(new_number)

        # Remove the last period. Ex: 1.2.3.4. -> 1.2.3.4
        new_number = new_number[:-1]

        # Number is now formatted to pds standard, so return it.
        return new_number

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
        
        j=0
        i=0

        #read the bundle and collection template files and store their contents in strings.
        #If the file is invalid a statement will be printed and the function will quit.
        try: 
            fil = open(inFile,'r')

            fileText = fil.read()

            fil.close()
        except:
            print(inFile + " is an invalid file")
            return

        print(inFile)

        # change the version number
        while j <= len(fileText):
            chunk = fileText[j:j+4]
            if chunk == "AAAA":

                if i == 2:
                    fileText = list(fileText)
                    fileText[j:j+4] = self.with_dots(number)
                    fileText = "".join(fileText)
                    break

                fileText = list(fileText)
                fileText[j:j+4] = number
                fileText = "".join(fileText)
                i += 1
            j += 1
            # prevents the while loop from looping infinately should the if statement fail
            if j >= len(fileText):
                print("No keyword found. Check the templates for the phrase 'AAAA'.")
                break

        print(fileText)

        # write the new bundle and collection to the xmls

        fil = open(outFile, 'w')

        fil.write(fileText)

        fil.close()

        pass


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
        ('individual', 'individual'),
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
    targets = models.ManyToManyField("Target")

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
        if label_root.find('{}Context_Area'.format(NAMESPACE)):
            Context_Area = label_root.find('{}Context_Area'.format(NAMESPACE))
        else:
            Context_Area = label_root.find('{}Observation_Area'.format(NAMESPACE))

        Investigation_Area = Context_Area.find('{}Investigation_Area'.format(NAMESPACE))

        Investigation_Area.find('{}name'.format(NAMESPACE)).text = self.name
        Investigation_Area.find('{}type'.format(NAMESPACE)).text = self.type_of
        # Investigation_Area.name.text = self.name
        # Investigation_Area.type.text = self.type_of

        Internal_Reference = Investigation_Area.find('{}Internal_Reference'.format(NAMESPACE))
        Internal_Reference.find('{}lid_reference'.format(NAMESPACE)).text = self.lid
        Internal_Reference.find('{}reference_type'.format(NAMESPACE)).text = 'is_investigation'
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

        Investigation_Area = Context_Area.find('{}Investigation_Area'.format(NAMESPACE))

        Investigation_Area.find('{}name'.format(NAMESPACE)).text = ''
        Investigation_Area.find('{}type'.format(NAMESPACE)).text = ''
        # Investigation_Area.name.text = self.name
        # Investigation_Area.type.text = self.type_of

        Internal_Reference = Investigation_Area.find('{}Internal_Reference'.format(NAMESPACE))
        Internal_Reference.find('{}lid_reference'.format(NAMESPACE)).text = ''
        Internal_Reference.find('{}reference_type'.format(NAMESPACE)).text = ''

        Observing_System = Context_Area.find('{}Observing_System'.format(NAMESPACE))

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
        if label_root.find('{}Context_Area'.format(NAMESPACE)):
            Context_Area = label_root.find('{}Context_Area'.format(NAMESPACE))
        else:
            Context_Area = label_root.find('{}Observation_Area'.format(NAMESPACE))

        Observing_System = Context_Area.find('{}Observing_System'.format(NAMESPACE))
        

        # Add Facility to Observing System
        Observing_System_Component = etree.SubElement(
            Observing_System, 'Observing_System_Component')
        
        Observing_System.insert(2, Observing_System_Component)

        name = etree.SubElement(Observing_System_Component, 'name')
        name.text = self.name.title()
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

        Observing_System = Context_Area.find('{}Observing_System'.format(NAMESPACE))

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

    # Hold's investigations target was chosen from
    investigations = models.ManyToManyField("Investigation")

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
        if label_root.find('{}Context_Area'.format(NAMESPACE)):
            Context_Area = label_root.find('{}Context_Area'.format(NAMESPACE))
        else:
            Context_Area = label_root.find('{}Observation_Area'.format(NAMESPACE))

        # Observing_System = Context_Area.find('{}Observing_System'.format(NAMESPACE))

        # Add Facility to Observing System
        Observing_System_Component = etree.SubElement(
            Context_Area, 'Target_Identification')
        name = etree.SubElement(Observing_System_Component, 'name')
        name.text = self.name.title()
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
        reference_type.text = 'is_target'

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
        Context_Area = label_root.find('{}Context_Area'.format(NAMESPACE))

        Observing_System = Context_Area.find('{}Observing_System'.format(NAMESPACE))

        for component in Observing_System:
            if(component.tag == "{http://pds.nasa.gov/pds4/pds/v1}Target_Identification"):
                if(component[0].text.title() == self.name.title()):
                    component.getparent().remove(component)

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
        if label_root.find('{}Context_Area'.format(NAMESPACE)):
            Context_Area = label_root.find('{}Context_Area'.format(NAMESPACE))
        else:
            Context_Area = label_root.find('{}Observation_Area'.format(NAMESPACE))

        Observing_System = Context_Area.find('{}Observing_System'.format(NAMESPACE))

        # Add Facility to Observing System
        Observing_System_Component = etree.SubElement(
            Observing_System, 'Observing_System_Component')

        Observing_System.insert(1, Observing_System_Component)

        name = etree.SubElement(Observing_System_Component, 'name')
        name.text = self.name.title()
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

        Observing_System = Context_Area.find('{}Observing_System'.format(NAMESPACE))

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
        Context_Area = label_root.find('{}Context_Area'.format(NAMESPACE))

        Observing_System = Context_Area.find('{}Observing_System'.format(NAMESPACE))

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

        Observing_System = Context_Area.find('{}Observing_System'.format(NAMESPACE))

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
        if label_root.find('{}Context_Area'.format(NAMESPACE)):
            Context_Area = label_root.find('{}Context_Area'.format(NAMESPACE))
        else:
            Context_Area = label_root.find('{}Observation_Area'.format(NAMESPACE))

        Observing_System = Context_Area.find('{}Observing_System'.format(NAMESPACE))

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
        return name_edit.title()

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
        return 'urn:{0}:{1}'.format(self.user.userprofile.agency, self.name_lid_case())

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

        if os.path.isdir(self.directory()):

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
      #  if self.has_document:
        collections_list.append("document")
       # if self.has_context:
        collections_list.append("context")
       # if self.has_xml_schema:
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


class AdditionalCollections(models.Model):
    ADDITIONAL_COLLECTION_CHOICES = (
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

        # Append cleaned collection name to name edit for Product_Collection xml label
        name_edit = self.collection_name.lower()
        name_edit = 'collection_{}.xml'.format(name_edit)
        return name_edit

    def label(self):
        return os.path.join(self.directory(), self.name_label_case())
    
    def build_base_case(self):
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

    def fill_base_case(self, root):
        Product_Collection = root
         
        # Fill in Identification_Area
        Identification_Area = Product_Collection.find('{}Identification_Area'.format(NAMESPACE))

        #     lid
        logical_identifier = Identification_Area.find('{}logical_identifier'.format(NAMESPACE))
        logical_identifier.text = 'urn:{0}:{1}:{2}'.format(self.bundle.user.userprofile.agency, self.bundle.name_lid_case(), self.collection_name) # where agency is something like nasa:pds
        

        #     version_id --> Note:  Can be changed to be more dynamic once we implement bundle versions (which is different from PDS4 versions)
        version_id = Identification_Area.find('{}version_id'.format(NAMESPACE))
        version_id.text = '1.0'  

        #     title
        title = Identification_Area.find('{}title'.format(NAMESPACE))
        title.text = self.bundle.name_title_case()

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
        name_edit = 'bundle_{}.xml'.format(self.bundle.name_file_case())
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

        print('---DEBUG---')
        print('Root: {}'.format(root))

        Reference_List = root.find('{}Reference_List'.format(NAMESPACE))

        # Using .append

        Reference_List.append(etree.Element("Internal_Reference"))
        Internal_Reference = Reference_List.find('{}Internal_Reference'.format(NAMESPACE))

        Internal_Reference.append(etree.Element("lid_reference"))
        lid_reference = Internal_Reference.find('{}lid_reference'.format(NAMESPACE))
        lid_reference.text = relation.lid()

        Internal_Reference.append(etree.Element("reference_type"))
        lid_reference = Internal_Reference.find('{}reference_type'.format(NAMESPACE))
        lid_reference.text = 'bundle_to_{}'.format(relation.reference_type())

        # using .subelement

        # Internal_Reference = etree.SubElement(
        #     Reference_List, 'Internal_Reference')

        # lid_reference = etree.SubElement(Internal_Reference, 'lid_reference')
        # lid_reference.text = relation.lid()

        # reference_type = etree.SubElement(Internal_Reference, 'reference_type')
        # reference_type.text = 'bundle_to_{}'.format(relation.reference_type())

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


        reference_type = etree.SubElement(Bundle_Member_Entry, 'reference_type')
        reference_type.text = 'bundle_has_{}_collection'.format(collection.collection.lower())   

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

        
        Bundle_Member_Entry = etree.SubElement(root, 'Bundle_Member_Entry')

        lid_reference = etree.SubElement(Bundle_Member_Entry, 'lid_reference')
        lid_reference.text = '{}:{}'.format(self.bundle.lid(), collection.collection_name.lower())

        member_status = etree.SubElement(Bundle_Member_Entry, 'member_status')
        member_status.text = 'Primary'

        reference_type = etree.SubElement(Bundle_Member_Entry, 'reference_type')
        reference_type.text = 'bundle_has_{}_collection'.format(collection.collection_name.lower())   

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
    COLLECTION_CHOICES = (

        ('Document', 'Document'),
        ('Context', 'Context'),
        ('XML_Schema', 'XML_Schema'),
        ('Data', 'Data'),
        ('Browse', 'Browse'),
        ('Geometry', 'Geometry'),
        ('Calibration', 'Calibration'),
        ('Not_Set', 'Not_Set'),

    )
    bundle = models.ForeignKey(Bundle, on_delete=models.CASCADE)

    collection = models.CharField(max_length=MAX_CHAR_FIELD, choices=COLLECTION_CHOICES)
    
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
        name_edit = self.collection.lower()
        name_edit = 'collection_{}.xml'.format(name_edit)
        return name_edit

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
        logical_identifier.text = 'urn:{0}:{1}:{2}'.format(self.bundle.user.userprofile.agency, self.bundle.name_lid_case(
        ), self.collection)  # where agency is something like nasa:pds

        #     version_id --> Note:  Can be changed to be more dynamic once we implement bundle versions (which is different from PDS4 versions)
        version_id = Identification_Area.find('{}version_id'.format(NAMESPACE))
        version_id.text = '1.0'

        #     title
        title = Identification_Area.find('{}title'.format(NAMESPACE))
        title.text = self.bundle.name_title_case()

        collection = Product_Collection.find('{}Collection'.format(NAMESPACE))
        col_type = collection.find('{}collection_type'.format(NAMESPACE))
        col_type.text = self.collection

        #     information_model_version
        #information_model_version = Identification_Area.find('{}information_model_version'.format(NAMESPACE))
        #information_model_version = self.bundle.version.name_with_dots()

        return Product_Collection

    """
        build_internal_reference builds and fills the Internal_Reference information within the Reference_List of Product_Collection.  The relation is used within reference_type to associate what the collection is related to, like collection_to_document.  Therefore, relation is a model object in ELSA, like Document.  The possible relations as of V1A00 are resource, associate, calibration, geometry, spice kernel, document, browse, context, data, ancillary, schema, errata, bundle, personnel, investigation, instrument, instrument_host, target.
    """

    def build_internal_reference(self, root, relation):

        Reference_List = root.find('{}Reference_List'.format(NAMESPACE))

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

    name = models.CharField(max_length=256, blank=True)
    offset = models.IntegerField(default=0)
    object_length = models.IntegerField(default=1)
    description = models.CharField(max_length=5000, default="unset")
    records = models.IntegerField(default=1)
    field_delimiter = models.CharField(max_length=256, choices=DELIMITER_CHOICES, default="Comma", blank=True)
    fields = models.IntegerField(default=1)
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
        return os.path.join(self.directory(), self.name_label_case())

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
        
    def fill_base_case(self, root):

        Table_Delimited = root

        # Fill in Identification_Area
        print("id area")
        Identification_Area = Table_Delimited.find('{}Identification_Area'.format(NAMESPACE))

        # lid
        logical_identifier = Identification_Area.find('{}logical_identifier'.format(NAMESPACE))
        logical_identifier.text = 'urn:{0}:{1}:{2}'.format(self.data.bundle.user.userprofile.agency, self.data.bundle.name_lid_case(), self.name) # where agency is something like nasa:pds

        print("version")
        version_id = Identification_Area.find('{}version_id'.format(NAMESPACE))
        version_id.text = '1.0'  # Can make this better

        print("title")
        title = Identification_Area.find('{}title'.format(NAMESPACE))
        title.text = self.name

        print("info model")
        information_model_version = Identification_Area.find('information_model_version')
        #information_model_version.text = self.bundle.version.with_dots()

        f = Table_Delimited.find('{}File_Area_Observational'.format(NAMESPACE))
        td = f.find('{}Table_Delimited'.format(NAMESPACE))
        
        if self.name:
            name = td.find('{}name'.format(NAMESPACE))
            name.text = self.name

        # Probably change this to have it be part of a form
        local_identifier = td.find('{}local_identifier'.format(NAMESPACE))
        local_identifier.text = 'table'

        if self.offset:
            offset = td.find('{}offset'.format(NAMESPACE))
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
        groups = rd.find('{}fields'.format(NAMESPACE))
        groups.text = '0'

        field_delimited = rd.find('{}Field_Delimited'.format(NAMESPACE))

        for i in range(int(self.fields) - 1):
            cloned_file = copy.deepcopy(field_delimited)
            rd.append(cloned_file)
        
        return root

    def __str__(self):
        return smart_str(self.id)


# @python_2_unicode_compatible
class Table_Binary(models.Model):
    name = models.CharField(max_length=256, blank=True)
    offset = models.IntegerField(default=1)
    records = models.IntegerField(default=1)
    fields = models.IntegerField(default=1)
    data = models.ForeignKey(Data, on_delete=models.CASCADE, null=True)
    collection = models.ForeignKey(AdditionalCollections, on_delete = models.CASCADE, default='',)
    bundle = models.ForeignKey(Bundle, on_delete=models.CASCADE, null=True,)

    def name_label_case(self):
        name_edit = self.data.name.lower()
        name_edit = replace_all(name_edit, ' ', '_')
        name_edit = '{}.xml'.format(name_edit)
        return name_edit

    def label(self):
        return os.path.join(self.directory(), self.name_label_case())

    def directory(self):
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
        
    def fill_base_case(self, root):
            
        Table_Binary = root
        
        # Fill in Identification_Area
        print("id area")
        Identification_Area = Table_Binary.find('{}Identification_Area'.format(NAMESPACE))

        # lid
        logical_identifier = Identification_Area.find('{}logical_identifier'.format(NAMESPACE))
        logical_identifier.text = 'urn:{0}:{1}:{2}'.format(self.data.bundle.user.userprofile.agency, self.data.bundle.name_lid_case(), self.data.name) # where agency is something like nasa:pds

        print("version")
        version_id = Identification_Area.find('{}version_id'.format(NAMESPACE))
        version_id.text = '1.0'  # Can make this better

        print("title")
        title = Identification_Area.find('{}title'.format(NAMESPACE))
        title.text = self.name

        print("info model")
        information_model_version = Identification_Area.find('information_model_version')
        #information_model_version.text = self.bundle.version.with_dots()

        f = Table_Binary.find('{}File_Area_Observational'.format(NAMESPACE))
        tb = f.find('{}Table_Binary'.format(NAMESPACE))
        rb = tb.find('{}Record_Binary'.format(NAMESPACE))
        if self.name:
            name = tb.find('{}name'.format(NAMESPACE))
            name.text = self.name

        if self.offset:
            offset = tb.find('{}offset'.format(NAMESPACE))
            offset.text = str(self.offset)

        if self.records:
            records = tb.find('{}records'.format(NAMESPACE))
            records.text = str(self.records)
        
        if self.fields:
            fields = rb.find('{}fields'.format(NAMESPACE))
            fields.text = str(self.fields)
        
        return root

    def __str__(self):
        return smart_str(self.id)


# @python_2_unicode_compatible
class Table_Fixed_Width(models.Model):
    RECORD_CHOICES = (
        ('Sample Choice', 'Sample Choice'),
    )
    
    name = models.CharField(max_length=256, blank=True)
    offset = models.IntegerField(default=1)
    object_length = models.IntegerField(default=1)
    description = models.CharField(max_length=5000, default="unset")
    records = models.IntegerField(default=1)
    fields = models.IntegerField(default=1)
    data = models.ForeignKey(Data, on_delete=models.CASCADE, null=True)
    collection = models.ForeignKey(AdditionalCollections, on_delete = models.CASCADE, default='',)
    bundle = models.ForeignKey(Bundle, on_delete=models.CASCADE, null=True)

    def name_label_case(self):
        name_edit = self.data.name.lower()
        name_edit = replace_all(name_edit, ' ', '_')
        name_edit = '{}.xml'.format(name_edit)
        return name_edit

    def label(self):
        return os.path.join(self.directory(), self.name_label_case())

    def directory(self):
        data_collection_name = self.collection.collection_name.lower()
        data_directory = os.path.join(self.data.bundle.directory(), data_collection_name)
        return data_directory  

    def build_data_file(self):
        # Locate base case Product_Bundle template found in templates/pds4_labels/base_case/product_bundle
        source_file = os.path.join(settings.TEMPLATE_DIR, 'pds4_labels')
        source_file = os.path.join(source_file, 'base_templates')
        source_file = os.path.join(source_file, 'data_table_character.xml')

        ret_name = self.data.name.lower()
        ret_name = replace_all(ret_name, ' ', '_')
        out_file = os.path.join(self.directory(), ret_name + '.xml')
        out_file = os.path.join(self.directory(), self.data.name + '.xml')

        #set selected version
        update = Version()
        bundle = Bundle()
        print (source_file + "<<<<<<<<")

        update.version_update_old(self.data.bundle.version, source_file,out_file)
        
    def fill_base_case(self, root):

        Table_Character = root

        # Fill in Identification_Area
        print("id area")
        Identification_Area = Table_Character.find('{}Identification_Area'.format(NAMESPACE))

        # lid
        logical_identifier = Identification_Area.find('{}logical_identifier'.format(NAMESPACE))
        logical_identifier.text = 'urn:{0}:{1}:{2}'.format(self.data.bundle.user.userprofile.agency, self.data.bundle.name_lid_case(), self.data.name) # where agency is something like nasa:pds

        print("version")
        version_id = Identification_Area.find('{}version_id'.format(NAMESPACE))
        version_id.text = '1.0'  # Can make this better

        print("title")
        title = Identification_Area.find('{}title'.format(NAMESPACE))
        title.text = self.name

        print("info model")
        information_model_version = Identification_Area.find('information_model_version')
        #information_model_version.text = self.bundle.version.with_dots()

        f = Table_Character.find('{}File_Area_Observational'.format(NAMESPACE))
        tc = f.find('{}Table_Character'.format(NAMESPACE))
        rc = tc.find('{}Record_Character'.format(NAMESPACE))

        if self.name:
            name = tc.find('{}name'.format(NAMESPACE))
            name.text = self.name

        if self.offset:
            offset = tc.find('{}offset'.format(NAMESPACE))
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
        
        return root

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

        # Copy the base case template to the correct directory
        copyfile(source_file, label_file)

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
class Product_Document(models.Model):
    # Attributes
    bundle = models.ForeignKey(Bundle, on_delete=models.CASCADE)
    # acknowledgement_text = models.CharField(max_length=MAX_CHAR_FIELD) #doesn't exist on lockwood.xml
    author_list = models.CharField(max_length=MAX_CHAR_FIELD)
    copyright = models.CharField(max_length=MAX_CHAR_FIELD)
    description = models.CharField(max_length=MAX_CHAR_FIELD)
    document_editions = models.CharField(max_length=MAX_CHAR_FIELD)
    document_name = models.CharField(max_length=MAX_CHAR_FIELD)
    # doi = models.CharField(max_length=MAX_CHAR_FIELD) #in reference list but not document
    # editor_list = models.CharField(max_length=MAX_CHAR_FIELD) #doesn't exist on lockwood.xml
    publication_date = models.CharField(max_length=MAX_CHAR_FIELD)
    revision_id = models.CharField(max_length=MAX_CHAR_FIELD)
    edition_name = models.CharField(max_length=MAX_CHAR_FIELD, default='')
    language = models.CharField(max_length=MAX_CHAR_FIELD, default='')
    files = models.CharField(max_length=MAX_CHAR_FIELD, default='')
    file_name = models.CharField(max_length=MAX_CHAR_FIELD, default='')
    local_id = models.CharField(max_length=MAX_CHAR_FIELD, default='')
    document_std_id = models.CharField(max_length=MAX_CHAR_FIELD, default='PDF/A')

    #

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
        return os.path.join(self.directory(), self.name_label_case())

    def lid(self):
        return '{0}:document:{1}'.format(self.bundle.lid(), self.name_label_case())

    def reference_type(self):
        return 'document'

    # Builders

    def build_base_case(self):

        # Locate base case Product_Document template found in templates/pds4_labels/base_case/
        source_file = os.path.join(PDS4_LABEL_TEMPLATE_DIRECTORY, 'base_case')
        source_file = os.path.join(source_file, 'product_document.xml')

        # Locate collection directory and create path for new label
        label_file = os.path.join(self.directory(), self.name_label_case())

        # set selected version
        update = Version()
        bundle = Bundle()
        update.version_update_old(self.bundle.version, source_file, label_file)

        # Copy the base case template to the correct directory
#        copyfile(source_file, label_file)

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
        if self.author_list:
            author_list = Document.find('{}author_list'.format(NAMESPACE))
            author_list.text = self.author_list
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
        if self.language:
            language = Document_Edition.find('{}language'.format(NAMESPACE))
            language.text = self.language
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
            document_std_id.text = self.document_std_id

        for i in range(int(self.files) - 1):
            cloned_file = copy.deepcopy(Files)
            Document_Edition.append(cloned_file)
        
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
            Identification_Area.insert(Identification_Area.index(Modification_History), Alias_List)

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

        Alias_List = Identification_Area.find('{}Alias_List'.format(NAMESPACE))

        for alias in Alias_List:
            if alias and alias[0].text and alias[0].text.title() == self.alternate_id.title():
                alias.getparent().remove(alias)

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
class Citation_Information(models.Model):

    bundle = models.ForeignKey(Bundle, on_delete=models.CASCADE)
    author_list = models.CharField(max_length=MAX_CHAR_FIELD, blank=True)
    editor_list = models.CharField(max_length=MAX_CHAR_FIELD, blank=True)
    publication_year = models.CharField(max_length=MAX_CHAR_FIELD)
    description = models.CharField(max_length=MAX_TEXT_FIELD)
    keyword = models.CharField(max_length=MAX_CHAR_FIELD, blank=True)
    

    # Builders
    def fill_label(self, label_root):

        # Find Identification_Area
        Identification_Area = label_root.find(
            '{}Identification_Area'.format(NAMESPACE))

        # Locate Modification_History within Identification_Area
        Modification_History = Identification_Area.find(
            '{}Modification_History'.format(NAMESPACE))

        # Find Alias_List.  If no Alias_List is found, make one.
        Citation_Information = etree.Element(
            '{}Citation_Information'.format(NAMESPACE))
        
        # Add Citation_Information information
        if Modification_History is not None:
            Identification_Area.insert(Identification_Area.index(Modification_History), Citation_Information)

        else:
            Identification_Area.append(Citation_Information)


        # # Double check but I'm pretty sure Citation_Information is only added once.
        # # if Citation_Information is None:
        # Citation_Information = etree.SubElement(
        #     Identification_Area, 'Citation_Information')
        # Identification_Area.insert(0, Citation_Information)

        # Add Citation_Information information
        if self.author_list:
            author_list = etree.SubElement(Citation_Information, 'author_list')
            author_list.text = self.author_list
        if self.editor_list:
            editor_list = etree.SubElement(Citation_Information, 'editor_list')
            editor_list.text = self.editor_list
        publication_year = etree.SubElement(
            Citation_Information, 'publication_year')
        publication_year.text = self.publication_year
        if self.keyword:
            # Ask how keywords are saved #
            keyword = etree.SubElement(Citation_Information, 'keyword')
            keyword.text = self.keyword
        description = etree.SubElement(Citation_Information, 'description')
        description.text = self.description
        
        return label_root
    
    def remove_xml(self, label_root):
        Identification_Area = label_root.find('{}Identification_Area'.format(NAMESPACE))

        Citation_Information = Identification_Area.find('{}Citation_Information'.format(NAMESPACE))

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

        # Find Alias_List.  If no Alias_List is found, make one.
        Modification_History = Identification_Area.find(
            '{}Modification_History'.format(NAMESPACE))

        # Double check but I'm pretty sure Modification_History is only added once.
        # if Modification_History is None:
        # Modification_History = etree.SubElement(
        #     Identification_Area, 'Modification_History')

        # Add Modification_Detail information
        Modification_Detail = etree.SubElement(
            Modification_History, '{}Modification_Detail'.format(NAMESPACE))
        
        # Add Modification_History information
        modification_date = etree.SubElement(Modification_Detail, 'modification_date')
        modification_date.text = self.modification_date
        if self.version_id:
            version_id = etree.SubElement(Modification_Detail, 'version_id')
            version_id.text = self.version_id
        description = etree.SubElement(Modification_Detail, 'description')
        description.text = self.description
        
        return label_root

    def remove_xml(self, label_root):
        Identification_Area = label_root.find('{}Identification_Area'.format(NAMESPACE))

        Modification_History = Identification_Area.find('{}Modification_History'.format(NAMESPACE))

        # Modification_History.getparent().remove(Modification_History)

        # for tag in Modification_History.iter():
        #     modification_detail = Modification_History.find('{}Modification_Detail'.format(NAMESPACE))
        #     if tag == modification_detail:
        #         print(tag[2].text.title())
        #         print(self.description.title())
        #         print(tag)
        #         if(tag[2].text.title() == self.description.title()):
        #             tag.getparent().remove(tag)

        for modification_detail in Modification_History:
            if modification_detail[2].text.title() == self.description.title():
                modification_detail.getparent().remove(modification_detail)

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


#    To Be Garbage Here✲
