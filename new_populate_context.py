import json
import urllib.request

#important block for running extra scripts in the command line like this
#(I'll refactor this whole thing into a management command later)
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'elsa.settings')
import django
django.setup()
########################

import build.models

def fetch_ids(type):
    '''Fetches logical identifiers from context products' xml labels, thru the 
    PDS context search API. Give it a context product type (i.e. 'Target') and 
    this will fetch a list of all products with that type (and then presumably 
    we'll compare them to what's in the database already, so we can update and
    add as necessary).
    Note that type should be a string, and capitalized- Target, Mission, etc. 
    Returns a list of LID's.

    May eventually make this type-agnostic/make type an optional input.'''

    url = 'https://pds.nasa.gov/services/search/search?wt=json&q=product_class:Product_Context%20and%20data_class:'+type
    testfile=urllib.request.urlopen(url).read()
    data=json.loads(testfile)

    return([doc['identifier'] for doc in data['response']['docs']])

class Context_Product:
    '''It's in the name- class that represents the context product we want to
    put into ELSA's database. All it needs is the logical identifier, and then
    we can fetch info associated with that LID and populate ELSA's db.'''

    def __init__(self, lid):

        self.lid = lid
        self.type=self.lid.split(':')[4]
        self.get_keyword_dict(self.type)
        self.get_model_name(self.type)
    
    #these 'get' functions are separate just to avoid clogging up __init__ 
    # with a bunch of huge dictionaries
    def get_keyword_dict(self, type):
        '''Gets the right keywords we need for a given type of
         context product, since we need different info for different products.

         Still in the process of building this one. Several of these keywords
         aren't currently in use in the database (they all have to do with 
         references to other context products), but I'm fetching them anyway
         because I think the site and db will be better if we add them
         later. Connections between context products being the bones of the
         whole pds4 philosophy and all that.'''

        big_keyword_dict={'target':{'identifier':None, 'target_name':None, 'target_type':None, 'version_id':None } ,
                          'investigation':{'identifier':None, 'investigation_name':None, 'investigation_type':None, 'version_id':None, 'instrument_host_ref':None, 'target_ref':None }, 
                          'mission':{},
                          'instrument':{'identifier':None, 'instrument_name':None, 'instrument_type':None, 'version_id':None, 'instrument_host_ref':None },
                          'instrument_host': {'identifier':None, 'instrument_host_name':None, 'instrument_host_type':None, 'version_id':None, 'investigation_ref':None, 'target_ref':None, 'instrument_ref':None },
                          'facility':{'identifier':None, 'facility_name':None, 'facility_type':None, 'version_id':None } 
                          }
        self.context_info=big_keyword_dict[type]
    
    def get_model_name(self, type):
        model_name_dict={'instrument_host':build.models.Instrument_Host}
        self.model_name=model_name_dict[type]

    def fetch_context_info(self):
        '''Queries the API for info to get a Context_Product object's context
         information.'''
        
        url = 'https://pds.nasa.gov/services/search/search?wt=json&q=product_class:Product_Context%20and%20data_class:'+self.type.capitalize()+'%20and%20identifier:'+self.lid
        #print(url)
        testfile=urllib.request.urlopen(url).read()
        data=json.loads(testfile)

        #excludes extra informational files (Resources) that aren't the actual context file
        doc=[d for d in data['response']['docs'] if d['data_product_type']!=['Resource'] ] [0]
        #print(doc.keys())
        #print()
        for key in self.context_info:

            try: #try to find the keyword in the xml file
                self.context_info[key]=doc[key] #often a string, but sometimes a list 
            except KeyError: 
                print('\nkeyword '+key +' not found in xml label for '+self.lid+', '+key+' value will be left as None \n')
                #print(doc.keys())
                #self.context_info[key]='N/A'

        #print(self.context_info)
        
    
    def insert_into_db(self):
        '''work in progress. Last 2 commented lines will successfully insert a
        new row into the db and fill it with the info from the API query (yay)!
        (only for instrument_host because that's what I've been testing with
        today). Next step will be generalizing that to work with other types,
        probably by mapping keywords yet again.
        
        Possibly more pressing is the issue of whether or not we need to change
        the database structure at all- as it is now, references to other 
        context products aren't saved in the same place as the rest of a 
        product's info. That may not actually be a problem, but changing up 
        that backend structure to make the user experience easier when adding
        context is something worth exploring. And should probably be explored
        before I fill the db with a bunch of new stuff, since I'll probably
        have to redo a bunch of it if the table structure changes.'''
        
        #if not all(self.context_info.values()):
        #    raise ValueError('Context info missing/None, try calling fetch_context_info first.')
        
        print(self.lid)
        #print(self.context_info['identifier'], self.context_info['instrument_host_name'], self.context_info['instrument_host_type'], self.context_info['version_id'])
        #new_product=self.model_name(lid=self.context_info['identifier'], name=self.context_info['instrument_host_name'][0], type_of=self.context_info['instrument_host_type'][0], vid=self.context_info['version_id'][0])
        #new_product.save()
        



def test():
    '''Some early messing around with fetching things from the API search and
    json files. Keeping it around for now because it's useful for looking at/trying out
    specific search results.'''
    #change the last part of this url (data_class:Investigation) to something else (like data_class:Target) to get different types of context products
    full_url = 'https://pds.nasa.gov/services/search/search?wt=json&q=product_class:Product_Context%20and%20data_class:Target'
    testfile=urllib.request.urlopen(full_url).read()
    data=json.loads(testfile)

    for doc in data['response']['docs']: #loop through all the documents our request found

        #uncomment to print which keywords are used in the docs- identifiers/LID's, investigation names, etc. 
        #break incluced so we don't print the keyword list 100000 times.
        #print(doc.keys())
        #break
        print(doc['data_product_type'])       



type='Instrument_Host'

for i in fetch_ids(type):
    #print(i)
    test_id = Context_Product(i) 
    #print(test_id.lid)
    in_db = test_id.model_name.objects.filter(lid=test_id.lid).exists()
    if not in_db: #tangent, but could probably also filter out anything that's None in the db in a similar way
        test_id.fetch_context_info()
        test_id.insert_into_db()
        break

