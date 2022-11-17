#these both come built in with python 3- no harassing ICT to install stuff for us, yay
import json
import urllib.request

def test():
    #change the last part of this url (data_class:Investigation) to something else (like data_class:Target) to get different types of context products
    full_url = 'https://pds.nasa.gov/services/search/search?wt=json&q=product_class:Product_Context%20and%20data_class:Target'
    testfile=urllib.request.urlopen(full_url).read()
    data=json.loads(testfile)

    for doc in data['response']['docs']: #loop through all the documents our request found

        #uncomment to print which keywords are used in the docs- identifiers/LID's, investigation names, etc. 
        #break incluced so we don't print the keyword list 100000 times.
        #print(doc.keys())
        #break
        print(doc['data_product_type']) #prints every doc's (whatever keyword, I used 'identifier') value, swap in different keywords to see different stuff 
    
    #dict_keys(['data_class', 'objectType', 'product_class', 'file_ref_size', 'modification_date', 'file_ref_name', 
    #'identifier', 'modification_description', 'pds_model_version', 'target_name', 'form-target', 'file_ref_location', 
    #'file_ref_url', 'title', 'resLocation', 'data_product_type', 'agency_name', 'form-agency', 'target_type', 
    #'form-target-type', 'version_id', 'search_id', 'target_description', 'timestamp', 'score'])        


def fetch_ids(type):
    '''Fetches logical identifiers from context products' xml labels, thru the 
    PDS context search API. Give it a context product type (i.e. 'Target') and 
    we'll fetch a list of all targets (and then presumably compare them to 
    what's in the database already, so we can update and add as necessary).
    Note that type should be a string, and capitalized- Target, Mission, etc. 
    Returns list of LID's.'''

    url = 'https://pds.nasa.gov/services/search/search?wt=json&q=product_class:Product_Context%20and%20data_class:'+type
    testfile=urllib.request.urlopen(url).read()
    data=json.loads(testfile)

    return([doc['identifier'] for doc in data['response']['docs']])

def fetch_context_info(lid):
    '''Fetches relevant context information given a context product's 
    identifier.
    Has a kinda shitty duct tape fix in there for the fact that the API search
    results return lots of Reference files as well and it's a real asshole 
    about isolating just the actual context label. Will add more as I fix that '''
    
    context_type=lid.split(':')[4].capitalize()
    print(context_type)
    url = 'https://pds.nasa.gov/services/search/search?wt=json&q=product_class:Product_Context%20and%20data_class:Target%20and%20identifier:'+lid
    print(url)
    testfile=urllib.request.urlopen(url).read()
    data=json.loads(testfile)

    for doc in data['response']['docs']:
        print([doc[key] for key in doc.keys()])
        break #this right here is some bad duct tape. the search url isn't excluding stuff that doesn't have data_class target for whatever reason, but the real target does seem to always be the first one in the list... so... we'll see how long until this bites me in the ass
    #    print([doc[key] for key in doc.keys()])
    #    print([doc['identifier'] for doc in data['response']['docs']])
