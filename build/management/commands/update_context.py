import json
import urllib.request
import os
import sys
import datetime 
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'elsa.settings')
django.setup()

import build.models # might give a 'can't be resolved' warning but as long as 
                    # you run this thru manage.py, should work fine

from django.core.management.base import BaseCommand, CommandError

class Command(BaseCommand):
    def __init__(self, *args, **kwargs):
        super(Command, self).__init__(*args, **kwargs)

    help = "Query the PDS4 context search API for context products that aren't in ELSA's database yet, and add them to the database. more info here probably"

    def handle(self, *args, **options): #add optional type argument? or even like, a name to search for?
            '''runs automatically when we call manage.py update_context'''
            t1 = datetime.datetime.now()
            global outlog
            outlog = os.getcwd()+'/context_update_'+str(datetime.datetime.now().strftime('%m_%d_%Y'))+'.txt'
            print('\nStarting context update. Output stored at '+ outlog+'\n')
            outlog=open(outlog, 'w')

            ct='Instrument' 

            global id_list
            id_list = []
            #id_list = fetch_ids(context_type=ct)
            id_list = fetch_ids()

            global problem_products
            # to track anythings that throws an error the code can't get around,
            # so we can deal with it manually later
            problem_products = []

            global reference_pairs
            # will store internal references between context products so we can
            # go thru and link them together after adding everything to the 
            # main db tables. Not the cleanest way to handle this, but probably
            # the least likely to break later.
            reference_pairs = []

            #also for testing
            #id_list = ['urn:nasa:pds:context:instrument_host:spacecraft.ody', 'urn:nasa:pds:context:instrument_host:spacecraft.msl', 'urn:nasa:pds:context:instrument_host:spacecraft.vg2']
            count=0
            for i in id_list: 

                test_id = Context_Product(i) 
               
                in_db = test_id.model_name.objects.filter(lid=i).exists()
                fetch_worked = test_id.fetch_context_info()
                if not in_db: 
                    if fetch_worked:
                        print('Adding context product; ' + test_id.lid, file=outlog)
                        insert_worked=test_id.insert_into_db() #returns true or false 
                        if insert_worked: 
                            test_id.save_references(test_id.new_product)
                            count +=1
                
                else: #product is already in the database, but we'll still update the internal references
                    product = test_id.model_name.objects.filter(lid=i)[0]
                    test_id.save_references(product)
            
            print(str(count)+' new products added to database.\n Updating internal references...')
             
            #add internal references between products to the database
            add_reference_links(reference_pairs)      
            
            if len(problem_products)>0:
                print('\nErrors occured for the following context products-\ncheck output file for details and consider adding these manually instead.\n')
                for p in set(problem_products):
                    print(p)
            
            d=str(datetime.datetime.now() - t1)
            print('\nDone! Time elapsed: ' +d)
            pass


#code that lets us do the thing starts here


def fetch_ids(context_type=None, name=None):
    '''Fetches logical identifiers from context products' xml labels, thru the 
    PDS context search API. Give it a context product type (i.e. 'Target') and 
    this will fetch a list of all products with that type.

    type should be a string- Target, Mission, etc. Code will raise an 
    exception if the type isn't recognized.

    Returns a list of LID's.
    '''

    valid_types = ['Instrument_Host', 'Mission', 'Investigation', 'Telescope', 'Facility', 'Instrument', 'Target' ]
    
    if context_type:
        if context_type not in valid_types:
            raise Exception('Context product type not recognized:'+str(context_type))

        url = 'https://pds.nasa.gov/services/search/search?wt=json&q=product_class:Product_Context%20and%20data_class:'+context_type
        testfile=urllib.request.urlopen(url).read()
        data=json.loads(testfile)
        return_list = [doc['identifier'] for doc in data['response']['docs']]
        
    
    else: # no type provided means we just get everything- 
          # surprisingly fast somehow
        return_list=[]
        for t in valid_types:
        
            url = 'https://pds.nasa.gov/services/search/search?wt=json&q=product_class:Product_Context%20and%20data_class:'+t
            testfile=urllib.request.urlopen(url).read()
            data=json.loads(testfile)
            return_list = return_list+[doc['identifier'] for doc in data['response']['docs']]
        
    
    return(return_list)

def get_model(type):
    '''maps context product type to a model'''

    model_name_dict={'instrument_host':build.models.Instrument_Host,
                     'instrument':build.models.Instrument, 
                     'target':build.models.Target,
                     'investigation':build.models.Investigation,
                     'facility':build.models.Facility,
                     'mission':build.models.Mission,
                     'telescope': build.models.Telescope}
         
    return(model_name_dict[type])

class Context_Product:
    '''It's in the name- class that represents the context product we want to
    put into ELSA's database. All it needs is the logical identifier, and then
    we can fetch info associated with that LID and populate ELSA's db.'''

    def __init__(self, lid):

        self.lid = lid
        #print('lid = ' +self.lid)
        try:
            self.type=self.lid.split(':')[4]
        except IndexError:
            print('problem lid is ' + lid)
            raise
        self.get_keyword_dict(self.type)
        self.model_name=get_model(self.type)
        
    
    def get_keyword_dict(self, type):
        '''Gets the right keywords for a given type of
         context product, since we need different info for different products.
         TODO what's the deal with missions?
         '''

        big_keyword_dict={'target':{'identifier':[''], 'target_name':[''], 'target_type':[''], 'version_id':[''], 'file_ref_url':[''] } ,
                          'investigation':{'identifier':[''], 'investigation_name':[''], 'investigation_type':[''], 'version_id':[''], 'file_ref_url':[''], 'instrument_host_ref':[''], 'target_ref':[''] }, 
                          #'mission':{},
                          'instrument':{'identifier':[''], 'instrument_name':[''], 'instrument_type':[''], 'version_id':[''], 'file_ref_url':[''], 'instrument_host_ref':[''] },
                          'instrument_host': {'identifier':[''], 'instrument_host_name':[''], 'instrument_host_type':[''], 'version_id':[''], 'file_ref_url':[''], 'investigation_ref':[''], 'target_ref':[''], 'instrument_ref':[''] },
                          'facility':{'identifier':[''], 'facility_name':[''], 'facility_type':[''], 'version_id':[''], 'file_ref_url':[''], 'instrument_ref':[''] }, 
                          'telescope':{'identifier':[''], 'title':[''], 'version_id':[''], 'file_ref_url':[''], 'instrument_ref':['']} #associate_ref is another one that we could add here
                           }
        
        self.context_info=big_keyword_dict[type]
    
    

    def fetch_context_info(self, verbose=False):
        '''Queries the API for info to get a Context_Product object's information.'''
        
        url = 'https://pds.nasa.gov/services/search/search?wt=json&q=product_class:Product_Context%20and%20data_class:'+self.type.capitalize()+'%20and%20identifier:'+self.lid
        testfile=urllib.request.urlopen(url).read()
        data=json.loads(testfile)

        # excludes extra informational files (Resources) that aren't the actual context file
        try:
            doc=[d for d in data['response']['docs'] if d['data_product_type']!=['Resource']][0]
            if verbose: # sometimes (ie when something weird is going on) I want to see all of the keywords
                print(doc.keys())

        except IndexError: 
            problem_products.append(self.lid)
            print('ERROR; product ' +self.lid+ ' not found in repository', file=outlog)
            return(False)
        #print(doc.keys())
        
        for key in self.context_info:

            try: 
                self.context_info[key]=doc[key]  
            except KeyError: 
                print('keyword '+key +' not found in xml label for '+self.lid, file=outlog)
                pass
        
        return(True)
    
    def insert_into_db(self):
        '''Adds the context product to elsa's database. 
        '''

        if self.type=='telescope':
            self.context_info['telescope_name'] = self.context_info['title']

            new_product=self.model_name(lid=self.context_info['identifier'], 
                name=self.context_info[self.type+'_name'][0], 
                #type_of=self.context_info[self.type+'_type'][0], 
                vid=self.context_info['version_id'][0],
                starbase_label=self.context_info['file_ref_url'][0]
                )

        else:
            try:
                new_product=self.model_name(lid=self.context_info['identifier'], 
                    name=self.context_info[self.type+'_name'][0], 
                    type_of=self.context_info[self.type+'_type'][0], 
                    vid=self.context_info['version_id'][0],
                    starbase_label=self.context_info['file_ref_url'][0]
                    )

            except TypeError:
            # happens when there's a tag missing in the xml that ELSA's db
            # doesn't allow to be null/blank. Many fields do allow this
            # (since most of what we need for an internal reference is the LID).
        
                print('ERROR adding context product ' +self.lid + ' , keyword missing ^', file=outlog)
                problem_products.append(self.lid)
                return(False)
        
        self.new_product=new_product
        self.new_product.save()

        return(True)


    def save_references(self, new_product):
        '''Saves all of a product's references to other products to the global
        variable reference_list, so we can link them together in the database later. 
          '''

        for field in new_product._meta.get_fields(): 
            if list(field.name)[-1]!="s": #make sure we skip non-relational fields
                continue
            
            keyword = field.name+'_ref'
            keyword = keyword.replace('s_ref', '_ref') 
            
            if keyword not in self.context_info.keys():
                continue        

            if self.context_info[keyword]==['']:
                continue

            ref_model=get_model(keyword.split('_ref')[0])
           
            for ref_lid in self.context_info[keyword]:
                ref_obj=ref_model.objects.filter(lid=ref_lid.split('::')[0])

                if ref_lid.split('::')[0] not in id_list:
                    id_list.append(ref_lid.split('::')[0])
                
                if [self.lid, ref_lid.split('::')[0]] not in reference_pairs:
                    reference_pairs.append([self.lid, ref_lid.split('::')[0]])
                
                continue
            # past here is code from when this function also added links between
            # references, but doing all of that after we've added all of the 
            # new lid's to the db ends up being much cleaner and harder to 
            # break (at the expense of speed, but. This is ELSA, we need 
            # robust-ness more than just about anything else)

                if not ref_obj.exists(): #not in database
                    reference_pairs.append([self.lid, ref_lid.split('::')[0]])
                    if ref_lid not in id_list:
                        id_list.append(ref_lid.split('::')[0]) 
                    continue
                    
            
                ref_obj = ref_obj[0]
                    # note about the [0] above- looks like there are 
                    # duplicate entries for some products, so we grab only the first
                attr.add(ref_obj)
                print('\tInternal reference added; '+str(new_product.lid) + ' -> '+ str(ref_lid.split('::')[0]))
                
            #print(attr.all())
            

def add_reference_links(reference_pairs):
    '''Links up the context product's references to other products
    and adds those to the through tables* in the database.

    * https://www.sankalpjonna.com/learn-django/the-right-way-to-use-a-manytomanyfield-in-django
    https://docs.djangoproject.com/en/4.1/topics/db/examples/many_to_many/
            
    ''' 
    
    for pair in reference_pairs:
        if pair[0] in problem_products or pair[1] in problem_products: # I hate this. this should not be necessary. hamburger help me
            continue
        #print(pair, file=outlog)
        
        model1 = get_model(pair[0].split(':')[4])
        model2 = get_model(pair[1].split(':')[4])
        lid1=pair[0]
        lid2=pair[1]

        obj1 = list(model1.objects.filter(lid=lid1))[0]
        obj2 = list(model2.objects.filter(lid=lid2))[0]

        # makes sure a manytomanyfield that links the objects actually exists.
        field1 = [f for f in obj1._meta.get_fields() if f.name == pair[1].split(':')[4]+"s"]
        if len(field1)==1:
            
            #add reference from obj1 -> obj2 to database
            attr1=getattr(obj1, pair[1].split(':')[4]+"s", None)
            attr1.add(obj2)
            print('\tInternal reference added; '+pair[0] + ' -> '+ pair[1], file=outlog)
                
        
        # now repeat to add reference from obj2 -> obj1 
        # (if that's a valid reference, based on the fields we've defined in models.py)
        field2 = [f for f in obj2._meta.get_fields() if f.name == pair[0].split(':')[4]+"s"]
        if len(field2)==1:
            attr2=getattr(obj2, pair[0].split(':')[4]+"s", None)
            attr2.add(obj1)
            print('\tInternal reference added; '+pair[1] + ' -> '+ pair[0], file=outlog)
 

def test():
    '''Some early messing around with fetching things from the API search and
    json files. Keeping it around for now because it's useful for looking at/trying out
    specific search results.'''
    #change the last part of this url (data_class:Investigation) to something else (like data_class:Target) to get different types of context products
    full_url = 'https://pds.nasa.gov/services/search/search?wt=json&q=product_class:Product_Context%20and%20data_class:Telescope'
    testfile=urllib.request.urlopen(full_url).read()
    data=json.loads(testfile)

    for doc in data['response']['docs']: #loop through all the documents our request found

        #uncomment to print which keywords are used in the docs- identifiers/LID's, investigation names, etc. 
        #break incluced so we don't print the keyword list 100000 times.
        #print(doc.keys())
        #test comment to make things merge? idek
        print()
        for k in doc.keys():
            print(k, doc[k])
        #print(doc['identifier'])
        #break