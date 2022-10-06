#these both come built in with python 3- no harassing ICT to install stuff for us, yay
import json
import urllib.request

def fetch_ids(type):
    '''Fetches logical identifiers from context products' xml labels, thru the 
    PDS context search API. Give it a context product type (i.e. 'Target') and 
    this will fetch a list of all targets (and then presumably we'll compare 
    them to what's in the database already, so we can update and add as 
    necessary).
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
        self.context_info=self.get_keyword_dict(self.type)
    
    def get_keyword_dict(self, type):
        '''Gets the right keywords we need for a given type of
         context product, since we need different info for different products. 
         Still in the process of building this one'''

        big_keyword_dict={'target':{'identifier':None, 'target_name':None, 'target_type':None, 'version_id':None } ,
                          'investigation':{'identifier':None, 'investigation_name':None, 'investigation_type':None, 'version_id':None, 'instrument_host_ref':None, 'target_ref':None }, 
                          'mission':{},
                          'instrument':{'identifier':None, 'instrument_name':None, 'instrument_type':None, 'version_id':None, 'instrument_host_ref':None },
                          'instrument_host': {'identifier':None, 'instrument_host_name':None, 'instrument_host_type':None, 'version_id':None, 'investigation_ref':None, 'target_ref':None, 'instrument_ref':None },
                          'facility':{'identifier':None, 'facility_name':None, 'facility_type':None, 'version_id':None } 
                          }
        return(big_keyword_dict[type])
    
    def fetch_context_info(self):
        '''Fetches context information, given a context product's 
        logical identifier.'''
        
        url = 'https://pds.nasa.gov/services/search/search?wt=json&q=product_class:Product_Context%20and%20data_class:'+self.type.capitalize()+'%20and%20identifier:'+self.lid
        #print(url)
        testfile=urllib.request.urlopen(url).read()
        data=json.loads(testfile)

        #excludes extra informational files (Resources) that aren't the actual context file
        doc=[d for d in data['response']['docs'] if d['data_product_type']!=['Resource'] ] [0]
        print(doc.keys())
        print()
        for key in self.context_info:

            try: #try to find the keyword in the xml file
                self.context_info[key]=doc[key] #often a string, but sometimes a list 
            except KeyError: 
                print('\nkeyword '+key +' not found in xml label for '+self.lid+', '+key+' value will be entered as "NONE"\n')
                self.context_info[key]='NONE'

        print(self.context_info)
        
    
    def insert_into_db(self):
        if not all(self.context_info.values()):
            raise ValueError('Context info missing/None, try calling fetch_context_info first.')
        



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


test_id = Context_Product(fetch_ids('Instrument_Host')[1]) 
print(test_id.lid)
test_id.fetch_context_info()