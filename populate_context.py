from __future__ import print_function


from future import standard_library
standard_library.install_aliases()
from builtins import map
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'elsa.settings')

import django
django.setup()

from build.models import *
from lxml import etree
import time
import urllib.request, urllib.error, urllib.parse, urllib.request, urllib.parse, urllib.error


# Declare Global Variables
created = []

# The following is a variable containing a string representation of the URL where JPL's Starbase
# repository holds the context products
STARBASE_CONTEXT = 'https://pds.nasa.gov/data/pds4/context-pds4/'

# The following is a variable containing a list of current version ids (vids) found for products in 
# Starbase
#     This requires upkeep by a developer
#     Below is a list of current vids
#     If this list is not up to date, this function will miss things.
CURRENT_VIDS = ['1.0', '1.1', '1.2'] # max vid so far is 1.13 




"""
Takes two lists and makes them one list of 2-tuples.
Example: list1 = [ 0, 1, 2], list2 = [ 'a', 'b', 'c'], list3 = ['elsa', 'pds', 'atmos']
		The returned list is then [ (0, 'a', 'elsa'), (1, 'b', 'pds'), (2, 'c', 'atmos') ]
"""
def list_of_tuples(list1, list2):
    return list(map(lambda x, y: (x,y), list1, list2))


"""
get_product_list(string)

input: a string containing the type of context product the user is looking for
       examples: 'instrument', 'investigation', 'target'

output: a list of all products for that context type
"""
def get_product_list(context_type):
    STARBASE_PRODUCTS = STARBASE_CONTEXT + context_type
#
    html_arr = urllib.request.urlopen(STARBASE_PRODUCTS).readlines()
#conditional here before reading the lines of the context type urls. Feel free to add new conditions before the code begins to red the xml files. -Tommy
    product_list = []
    for string in html_arr:
        if 'Collection_' in string:
	    continue
	elif '.gz' in string:
	    continue
	elif '_deprecated' in string:
	    continue
	elif '.xml' in string:
            root = etree.fromstring(string)
            product = root[1][0]
            product_list.append(product.text)
    return product_list

"""
get_product_dict(url)

input: a string representation of the url where the product label is located

output: a dictionary containing the product's url, lid, vid, and internal references.

"""
def get_product_dict(url):

    product_dict = {}
    lid_references = []
    reference_types = []

    # Turn label into a tree and get root of tree
    parser = etree.XMLParser(remove_comments=True)
    tree = etree.parse(urllib.request.urlopen(url), parser=parser)
    label_root = tree.getroot()


    # Get proper product information for dictionary
    #     Note: For each internal reference there should be exactly one lid_reference or
    #            lidvid_reference and exactly one reference_type.
    #     Note: When a lidvid_reference is given, it will be modified to be the equivalent
    #            lid_reference.
    
    # Get url for product
    product_dict['url'] = url

    # Get lid for product
    product_dict['lid'] = label_root[0][0].text
            #print elt, " = ", element.text

    # Get vid for product
    product_dict['vid'] = float(label_root[0][1].text)

    #testing out getting the title
   # product_dict['title'] = smart_str(label_root[0][2].text)
#    title = label_root.find('{}title'.format(STARBASE_PRODUCTS))
    #print title


 
    for element in label_root.iter():
        #print element
        elt = element.tag.split('}')[1] # Eliminates namespace from tag

        # Get lid_references for internal references
        if elt == 'lid_reference':

            # Append lid_reference to the list of lid_references
            lid_references.append(element.text)
            #print elt, " = ", element.text

        # Get lidvid_references for internal references
        elif elt == 'lidvid_reference':

            # Modify lidvid to be a lid reference
            e = element.text.split('::')[0]

            # Append lid_reference equivalent of lidvid_reference to the list of lid_references
            lid_references.append(e)
      
            #print elt, " = ", element.text                

        # Get reference_types for internal references
        elif elt == 'reference_type':

            # Append reference_type to the list of reference_types
            reference_types.append(element.text)
            #print elt, " = ", element.text

    # Save the internal references into the product's dictionary
    product_dict['internal_references'] = list_of_tuples(lid_references, reference_types)

    return product_dict


# get_internal_refs
# This function finds a specific type of internal reference from a given type of product.
# It will return a list, empty or nonempty.
#
# input:
#     product_dict : the product_dict already grabs all of the internal_references for 
#                    a product which are stored under the key 'internal_references'
#                    (See get_product_dict() for more information). 
#     source_product : the product we want to find the internal references for
#     target_product : the type of product we want the internal reference to be
# 
# output:
#     a list of objects of type target_product that are related to source_product
def get_internal_references(product_dict, source_product, target_product):

    # Declare list of internal references to be returned to the user containing
    # all of the internal references that match the given source product to target product
    # relation.
    matched_refs = []
    matched_objs = []

    # Determine the source_product to target_product relation
    relation = "{0}_to_{1}".format(source_product, target_product)

    # Get the list of internal references where for each internal reference,
    # the internal reference is a 2-tuple of (lid_reference, reference_type)
    internal_references = product_dict['internal_references']
    for internal_ref in internal_references:
        if internal_ref[1] == relation:
            # Search objects to get the object related to the internal_reference
            # of type target_product given its lid
            ir_obj = get_lid_to_object_queryset(target_product, internal_ref[0])
            if ir_obj is not None:
                matched_refs.append(ir_obj)

    # Return all the internal references that matched the given source product to target 
    # product relations
    return matched_refs


def get_or_none_lid(model, l):
    try:
        return model.objects.get(lid=l)
    except model.DoesNotExist:
        return None


# Finds the associated object of type product_type given the product's lid
def get_lid_to_object_queryset(product_type, lid):
    print(product_type)
    print(lid)
    if product_type == 'investigation':
        product = Investigation.objects.get_or_create(lid=lid)
        # If we end up creating an object at this point, we have an error in Starbase.
        # ELSA takes a top down approach while crawling. If we are finding a queryset
        # for investigation objects, then we are asking to do so in something like
        # instrument host, instrument, or target so that we can reference the product
        # back to an investigation. If we find an investigation that needs to be created at
        # this point, seeing as ELSA's crawler crawls investigation first, we have an error
        # where a product like an instrument host, instrument, target, etc., has a reference
        # to an investigation that does not exist in Starbase. This is against PDS4.
        if product[1]:
            return "STARBASE ERR: Starbase is missing an Investigation object"
        else:
            return product[0]
    elif product_type == 'instrument_host':
        return get_or_none_lid(Instrument_Host, lid)
    elif product_type == 'instrument':
        return get_or_none_lid(Instrument, lid)
    elif product_type == 'target':
        return get_or_none_lid(Target, lid)
    else:
        print("Lid to object queryset error: Uknown object")
    
# Constructs the Starbase url for a product of a given name and type
def get_starbase_url(product_type, product_name):
    return STARBASE_CONTEXT + product_type + '/' + product_name

# Gets a detail of the product given the product name found on Starbase
#   definition:
#     investigation_detail = [type_of, name, ..., version, file extension]
#
def get_product_detail(product_name):
        product_detail = product_name.split('.')
        print(product_detail)
        while False:
            product_detail[2] = float(product_detail[-3][-1:] + '.' + product_detail[-2])
            product_detail[1] = product_detail[1][:-2]
 #           return product_detail
	    pass
 	else:
            return product_detail
#	    pass
#	    if False:
#		pass

#
#
#
def investigation_crawl():

    # STARBASE FIXES
    i = Investigation.objects.get_or_create(name='support_archives', type_of='mission', lid='urn:nasa:pds:context:investigation:mission.support_archives', vid=1.0)
    if i[1] == True:
        i[0].save()
        created.append(i[0])
 
    # INVESTIGATION CRAWL
    # grab the whole list of investigations from Starbase
    investigations = get_product_list('investigation')

    # for each investigation, we want to create an Investigation object in ELSA's database
    for inv in investigations:
        print("\n\nInvestigation: ", inv)


        # we need the url where the label exists
        investigation_url = get_starbase_url('investigation', inv)
        print("URL: ", investigation_url)

        # we want our crawler to grab specified information from the labels
        # these details about the investigation will be used to fill the
        # ELSA database
        #
        #   definition:
        #     investigation_detail = [type_of, name, version, file extension]
        #
       # investigation_title = get_product_detail(inv)
      #  print "title: ", investigation_detail 

        investigation_detail = get_product_detail(inv)
        print("Investigation Detail: ", investigation_detail)

        # we want the latest       
        # we want to create the object in our database if it does not already exist

        #
        #  get_or_create returns a 2-tuple containing the object and a boolean indicating whether
        #  or not the object was created.
        #    i = ( object, created )
        print("name: ", investigation_detail[1])
        print("type_of: ", investigation_detail[0])
        i = Investigation.objects.get_or_create(
                name=investigation_detail[1], 
                type_of=investigation_detail[0], 
            )
        print(i)
	
        
        # Get product dictionary
        product_dict = get_product_dict(investigation_url)

        # if the object was created, then it is the first product of its name
        # and investigation type so we want to do a few things:
        #     1. Save and append object to our created list so we can
        #        print what was created later for our own sanity
        if i[1] == True:
            i[0].vid = product_dict['vid']
            i[0].lid = product_dict['lid']
            i[0].starbase_label = investigation_url
            i[0].save()
            created.append(i[0])
            print("NEW OBJECT: ", i[0].vid, i[0].lid)


        # If the object already exists in our database, we want to make sure the database object
        # is the latest version of the product, designated by the vid (or version_id)
        else:
            # Compare if the vid in our investigation detail is greater than the vid associated
            # with our investigation object, i[0].
            if( i[0].vid < product_dict['vid'] ):
                print("HIGHER VID FOUND: ", i[0].vid, " < ", product_dict['vid'])
                print(type(i[0].vid), type(product_dict['vid']))

                # Modify object i to be the last version of i
                i[0].update_version(product_dict)
                created.append(i[0])

                print("MODIFIED OBJ: ", i[0].vid, i[0].lid)





def instrument_host_crawl():

    # STARBASE FIXES
    j = Instrument_Host.objects.get_or_create(name='unk', type_of='unk', lid='urn:nasa:pds:context:instrument_host:unk.unk', vid=1.0)
    if j[1] == True:
        j[0].save()
        created.append(j[0])
    if j[0] == False:
        print("\n\nUMMMM.. Never want to see this")

    # INSTRUMENT_HOST CRAWL
    #     1. Create Instrument Host object
    #     2. Relate Instruments and Targets to Instrument Host


    # grab the whole list of instrument hosts from Starbase
    instrument_hosts = get_product_list('instrument_host')

    # for each investigation, we want to create an Instrument_Host object in ELSA's database
    for instrument_host in instrument_hosts:
        print("\n\nInstrument host: ", instrument_host)

        # we need the url where the label exists
        instrument_host_url = get_starbase_url('instrument_host', instrument_host)
        print("URL: ", instrument_host_url)

        # we want our crawler to grab specified information from the labels
        # these details about the investigation will be used to fill the
        # ELSA database
        #
        #   definition:
        #     instrument_host_detail = [type_of, name, version, file extension]
        #
        instrument_host_detail = get_product_detail(instrument_host)
        print("Instrument_Host Detail: ", instrument_host_detail)


        # Get product dictionary
        product_dict = get_product_dict(instrument_host_url)

        #
        #  get_or_create returns a 2-tuple containing the object and a boolean indicating whether
        #  or not the object was created.
        #    i = ( object, created )
        i = Instrument_Host.objects.get_or_create(
                #investigation=investigation_set[0],
                name=instrument_host_detail[1], 
                type_of=instrument_host_detail[0], 
            )
        print(i)

        # If the object was retrieved, ie. already exists in our database, we want to make sure 
        # the database object is the latest version of the product, designated by the vid (or 
        # version_id)
        if i[1] == False :
            # Compare if the vid in our instrument host detail is greater than the vid associated
            # with our instrument host object, i.
            if( i[0].vid < product_dict['vid'] ):
                print("HIGHER VID FOUND: ", i[0].vid, " < ", product_dict['vid'])
                print(type(i[0].vid), type(product_dict['vid']))

                # Modify object i to be the last version of i
                i[0].update_version(product_dict)
                created.append(i[0])

                print("MODIFIED OBJ: ", i[0].vid, i[0].lid)


        # if the object was created, then it is the first product of its name
        # and instrument host type so we want to do a few things:
        #     1. Get the investigation that the instrument host is related to
        #     2. Get the instruments that the instrument host is related to
        #     3. Get the targets that the isntrument host is related to   
        #     4. Save and append object to our created list so we can
        #        print what was created later for our own sanity
 
        else:

            # Get investigation object(s)
            investigation_set = get_internal_references(product_dict, 'instrument_host', 'investigation')
            # Add investigation object(s) relation to instrument host object
            if len(investigation_set) == 0:
                print("No investigations found for instrument host")
            else:
                for inv in investigation_set:
                    i[0].investigations.add(inv)


            # Get instrument object(s)
            instrument_set = get_internal_references(product_dict, 'instrument_host', 'instrument')

            # Add instrument object(s) relation to instrument host object
            if len(instrument_set) == 0:
                print("No instruments found for instrument host")
            else:
                for ins in instrument_set:
                    i[0].instruments.add(ins)


            # Get target object(s)
            target_set = get_internal_references(product_dict, 'instrument_host', 'target')

            # Add target object(s) relation to instrument host object
            if len(target_set) == 0:
                print("No targets found for instrument host")
            else:
                for tar in target_set:
                    i[0].targets.add(tar)

            # Save and append instrument host object
            i[0].vid = product_dict['vid']
            i[0].lid = product_dict['lid']
            i[0].starbase_label = instrument_host_url
            i[0].save()
            created.append(i[0])
            print("New elt i: ", i[0].vid, i[0].lid)




def instrument_crawl():
    # INSTRUMENT CRAWL
    # grab the whole list of instruments from Starbase
    instruments = get_product_list('instrument')

    # for each investigation, we want to create an Instrument_Host object in ELSA's database
    for instrument in instruments:
        print("\n\nInstrument: ", instrument)

        # we need the url where the label exists
        instrument_url = get_starbase_url('instrument', instrument)
        print("URL: ", instrument_url)

        # we want our crawler to grab specified information from the labels
        # these details about the investigation will be used to fill the
        # ELSA database
        #
        #   definition:
        #     instrument_detail = [type_of, name, (sometimes another name), version, file extension]
        #
        instrument_detail = get_product_detail(instrument)
        print("Instrument Detail: ", instrument_detail)

        # Get product dictionary
        product_dict = get_product_dict(instrument_url)

        #
        #  get_or_create returns a 2-tuple containing the object and a boolean indicating whether
        #  or not the object was created.
        #    i = ( object, created )
        i = Instrument.objects.get_or_create(
                #investigation=investigation_set[0],
                name=instrument_detail[1], 
                type_of=instrument_detail[0], 
            )
        print(i)
        
        # Get product dictionary by crawling the instrument host label on Starbase
        product_dict = get_product_dict(instrument_url)

        # If the object was retrieved, ie. already exists in our database, we want to make sure 
        # the database object is the latest version of the product, designated by the vid (or 
        # version_id)
        if i[1] == False :
            # Compare if the vid in our investigation detail is greater than the vid associated
            # with our investigation object, i.
            if( i[0].vid < product_dict['vid'] ):
                print("HIGHER VID FOUND: ", i[0].vid, " < ", product_dict['vid'])
                print(type(i[0].vid), type(product_dict['vid']))

                # Modify object i to be the last version of i
                i[0].update_version(product_dict)
                created.append(i[0])

                print("MODIFIED OBJ: ", i[0].vid, i[0].lid)


        # if the object was created, then it is the first product of its name
        # and investigation type so we want to:
        #     1. Save and append object to our created list so we can
        #        print what was created later for our own sanity
 
        else:
            # Save and append instrument object
            i[0].vid = product_dict['vid']
            i[0].lid = product_dict['lid']
            i[0].starbase_label = instrument_url
            i[0].save()
            created.append(i[0])
            print("New elt i: ", i[0].vid, i[0].lid)



def target_crawl():
    # TARGET CRAWL
    # grab the whole list of targets from Starbase
    targets = get_product_list('target')

    # for each target, we want to create a Target object in ELSA's database
    for target in targets:
        print("\n\Target: ", target)

        # we need the url where the label exists
        target_url = get_starbase_url('target', target)
        print("URL: ", target_url)

        # we want our crawler to grab specified information from the labels
        # these details about the investigation will be used to fill the
        # ELSA database
        #
        #   definition:
        #     target_detail = [type_of, name, (sometimes another name), version, file extension]
        #
        target_detail = get_product_detail(target)
        print("Target Detail: ", target_detail)

        # we want the latest       
        # we want to create the object in our database if it does not already exist

        # Get product dictionary
        product_dict = get_product_dict(target_url)

        #
        #  get_or_create returns a 2-tuple containing the object and a boolean indicating whether
        #  or not the object was created.
        #    i = ( object, created )
        t = Target.objects.get_or_create(
                #investigation=investigation_set[0],
                name=target_detail[1], 
                type_of=target_detail[0], 
            )
        print(t)
        
        # Get product dictionary by crawling the target label on Starbase
        product_dict = get_product_dict(target_url)

        # If the object was retrieved, ie. already exists in our database, we want to make sure 
        # the database object is the latest version of the product, designated by the vid (or 
        # version_id)
        if t[1] == False :
            # Compare if the vid in our target detail is greater than the vid associated
            # with our target object, i.
            if( t[0].vid < product_dict['vid'] ):
                print("HIGHER VID FOUND: ", t[0].vid, " < ", product_dict['vid'])
                print(type(t[0].vid), type(product_dict['vid']))

                # Modify object t to be the last version of t
                t[0].update_version(product_dict)
                created.append(t[0])

                print("MODIFIED OBJ: ", t[0].vid, t[0].lid)


        # if the object was created, then it is the first product of its name
        # and target type so we want to do a few things:
        #     1. Save and append object to our created list so we can
        #        print what was created later for our own sanity
 
        else:
            # Save and append target object
            t[0].vid = product_dict['vid']
            t[0].lid = product_dict['lid']
            t[0].starbase_label = target_url
            t[0].save()
            created.append(t[0])
            print("New elt t: ", t[0].vid, t[0].lid)





           


def facility_crawl():

    # STARBASE FIXES


    # FACILITY CRAWL
    #     1. Create Facility object
    #     2. Relate Instruments to Facility


    # grab the whole list of facilities from Starbase
    facilities = get_product_list('facility')

    # for each facility, we want to create a Facility object in ELSA's database
    for facility in facilities:
        print("\n\nFacility: ", facility)

        # we need the url where the label exists
        facility_url = get_starbase_url('facility', facility)
        print("URL: ", facility_url)

        # we want our crawler to grab specified information from the labels
        # these details about the investigation will be used to fill the
        # ELSA database
        #
        #   definition:
        #     facility_detail = [type_of, name, version, file extension]
        #
        facility_detail = get_product_detail(facility)
        print("Facility Detail: ", facility_detail)


        # Get product dictionary
        product_dict = get_product_dict(facility_url)

        #
        #  get_or_create returns a 2-tuple containing the object and a boolean indicating whether
        #  or not the object was created.
        #    i = ( object, created )
        f = Facility.objects.get_or_create(
                name=facility_detail[1], 
                type_of=facility_detail[0], 
            )
        print(f)

        # If the object was retrieved, ie. already exists in our database, we want to make sure 
        # the database object is the latest version of the product, designated by the vid (or 
        # version_id)
        if f[1] == False :
            # Compare if the vid in our facility detail is greater than the vid associated
            # with our facility object, f.
            if( f[0].vid < product_dict['vid'] ):
                print("HIGHER VID FOUND: ", f[0].vid, " < ", product_dict['vid'])
                print(type(f[0].vid), type(product_dict['vid']))

                # Modify object i to be the last version of i
                f[0].update_version(product_dict)
                created.append(f[0])

                print("MODIFIED OBJ: ", f[0].vid, f[0].lid)


        # if the object was created, then it is the first product of its name
        # and instrument host type so we want to do a few things:
        #     1. Get the investigation that the instrument host is related to
        #     2. Get the instruments that the instrument host is related to
        #     3. Get the targets that the isntrument host is related to   
        #     4. Save and append object to our created list so we can
        #        print what was created later for our own sanity
 
        else:



            # Get instrument object(s)
            instrument_set = get_internal_references(product_dict, 'facility', 'instrument')

            # Add instrument object(s) relation to instrument host object
            if len(instrument_set) == 0:
                print("No instruments found for instrument host")
            else:
                for ins in instrument_set:
                    f[0].instruments.add(ins)




            # Save and append instrument host object
            f[0].vid = product_dict['vid']
            f[0].lid = product_dict['lid']
            f[0].starbase_label = facility_url
            f[0].save()
            created.append(f[0])
            print("New elt f: ", f[0].vid, f[0].lid)


           


def telescope_crawl():

    # STARBASE FIXES


    # FACILITY CRAWL
    #     1. Create Telescope object
    #     2. Relate Facility to Telescope


    # grab the whole list of facilities from Starbase
    telescopes = get_product_list('telescope')

    # for each telescope, we want to create a Telescope object in ELSA's database
    for telescope in telescopes:
        print("\n\nTelescope: ", telescope)

        # we need the url where the label exists
        telescope_url = get_starbase_url('telescope', telescope)
        print("URL: ", telescope_url)

        # we want our crawler to grab specified information from the labels
        # these details about the investigation will be used to fill the
        # ELSA database
        #
        #   definition:
        #     telescope_detail = [type_of, name, version, file extension]
        #
        telescope_detail = get_product_detail(telescope)
        print("Telescope Detail: ", telescope_detail)


        # Get product dictionary
        product_dict = get_product_dict(telescope_url)

        #
        #  get_or_create returns a 2-tuple containing the object and a boolean indicating whether
        #  or not the object was created.
        #    i = ( object, created )
        t = Telescope.objects.get_or_create(
                name=telescope_detail[0], 
            )
        print(t)

        # If the object was retrieved, ie. already exists in our database, we want to make sure 
        # the database object is the latest version of the product, designated by the vid (or 
        # version_id)
        if t[1] == False :
            # Compare if the vid in our telescope detail is greater than the vid associated
            # with our telescope object, f.
            if( t[0].vid < product_dict['vid'] ):
                print("HIGHER VID FOUND: ", t[0].vid, " < ", product_dict['vid'])
                print(type(t[0].vid), type(product_dict['vid']))

                # Modify object i to be the last version of i
                t[0].update_version(product_dict)
                created.append(t[0])

                print("MODIFIED OBJ: ", t[0].vid, t[0].lid)


        # if the object was created, then it is the first product of its name
        # and instrument host type so we want to do a few things:
        #     1. Get the facility that the target is related to
        #     4. Save and append object to our created list so we can
        #        print what was created later for our own sanity
 
        else:



            # Get facility object(s)
            facility_set = get_internal_references(product_dict, 'telescope', 'facility')

            # Add facility object(s) relation to telescope object
            if len(facility_set) == 0:
                print("No instruments found for instrument host")
            else:
                for fac in facility_set:
                    t[0].facilities.add(fac)




            # Save and append instrument host object
            t[0].vid = product_dict['vid']
            t[0].lid = product_dict['lid']
            t[0].starbase_label = telescope_url
            t[0].save()
            created.append(t[0])
            print("New elt t: ", t[0].vid, t[0].lid)




































"""
This is the script to autopopulate context products.
The context types covered in this script are:
    - investigation
"""
def populate():

    # created is a list of elements that were created by this population script
    # currently created is empty bc nothing has been added to it.
    investigation_crawl()
    instrument_crawl()
    target_crawl()
    instrument_host_crawl()
    facility_crawl()
    telescope_crawl()


    for elt in created:
        print("New object: {0}".format(elt.name))


if __name__ == '__main__':
    print("\nSTARTING ELSA POPULATION SCRIPT -- CONTEXT MODELS.")
#    print("January 2019 (k)")
    s1 = time.time()
    populate()
    s2 = time.time()
    print("POPULATION SUCCESSFUL")
    print(("Time Elapsed: ", (s2 - s1), "\nNumber of objects added: ", len(created)))
