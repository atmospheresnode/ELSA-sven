import json
import requests
import os
import sys
import datetime 
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'elsa.settings')
django.setup()

from build.models import * # might give a 'can't be resolved' warning but as long as 
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

            # ct='Instrument' 

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

                print(i)
                # skip agency for now
                if i.split(':')[4] == 'agency' or i.split(':')[4] == 'node' or i.split(':')[4] == 'personnel' or i.split(':')[4] == 'resource' or i.split(':')[4] == 'airborne':
                    continue

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


def fetch_ids(limit=5000):
    '''Fetches logical identifiers from context products' xml labels, thru the 
    PDS context search API. Give it a context product type (i.e. 'Target') and 
    this will fetch a list of all products with that type.
    type should be a string- Target, Mission, etc. Code will raise an 
    exception if the type isn't recognized.
    Returns a list of LID's.

    Said Notes: Will return all LID's as, there's only one call to function, and gets
    all LID's anyway.Limit relates to amount of context products in registry
    '''

    return_list = []

    api_url = "https://pds.nasa.gov/api/search/1/products?q=pds:Identification_Area.pds:product_class%20eq%20%22Product_Context%22"

    payload = {'limit': limit}
    response = requests.get(api_url, params=payload)

    data = json.dumps(response.json(), indent=4)
    data = json.loads(data)

    for product in data['data']:
        return_list.append(product['properties']['lid'][0])
    
    return return_list

def get_model(type):
    '''maps context product type to a model'''

    model_name_dict={'instrument_host':Instrument_Host,
                     'instrument':Instrument, 
                     'target': Target,
                     'investigation':Investigation,
                     'facility': Facility,
                     'mission': Mission,
                     'telescope': Telescope}
         
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
         What is wuth mission?

         Note: Might break in some keywords
         observing_system_components, investigations, and targets is outside of properties section
         '''

        big_keyword_dict={'target':{'lid':[''], 'pds:Target.pds:name':[''], 'pds:Target.pds:type':[''], 'vid':[''], 'ops:Label_File_Info.ops:file_ref':[''] } ,
                          'investigation':{'lid':[''], 'pds:Investigation.pds:name':[''], 'pds:Investigation.pds:type':[''], 'vid':[''], 'ops:Label_File_Info.ops:file_ref':[''], 'observing_system_components':[''], 'targets':[''] }, 
                          'instrument':{'lid':[''], 'pds:Instrument.pds:name':[''], 'pds:Instrument.pds:type':[''], 'vid':[''], 'ops:Label_File_Info.ops:file_ref':[''], 'ref_lid_instrument_host':[''] },
                          'instrument_host': {'lid':[''], 'pds:Instrument_Host.pds:name':[''], 'pds:Instrument_Host.pds:type':[''], 'vid':[''], 'ops:Label_File_Info.ops:file_ref':[''], 'investigations':[''], 'targets':[''], 'ref_lid_instrument':[''] },
                          'facility':{'lid':[''], 'pds:Facility.pds:name':[''], 'pds:Facility.pds:type':[''], 'vid':[''], 'ops:Label_File_Info.ops:file_ref':[''], 'ref_lid_instrument':[''] }, 
                          'telescope':{'lid':[''], 'title':[''], 'vid':[''], 'ops:Label_File_Info.ops:file_ref':[''], 'ref_lid_instrument':['']}
                           }
        
        self.context_info=big_keyword_dict[type]
    
    

    def fetch_context_info(self):
        '''Queries the API for info to get a Context_Product object's information.'''
        api_url = "https://pds.nasa.gov/api/search/1/products?q=lid%20eq%20%22{}%22".format(self.lid)

        response = requests.get(api_url)

        data = json.dumps(response.json(), indent=4)
        data = json.loads(data)

        for product in data['data']:
            if product['properties']['lid'][0].split(':')[4] == self.type:
                for key in self.context_info:
                    try: 
                        if key == 'observing_system_components' or key == 'investigations' or key == 'targets':
                            self.context_info[key]=product[key]
                        else:
                            self.context_info[key]=product['properties'][key] 
                    except KeyError: 
                        print('keyword '+key +' not found in xml label for '+self.lid, file=outlog)
                    pass
        
        return True
    
    def insert_into_db(self):
        '''Adds the context product to elsa's database. 
        '''

        if self.type == 'telescope':
            new_product=self.model_name(lid=self.context_info['lid'][0], 
                    name=self.context_info['title'][0], 
                    # type_of=self.context_info[self.type+'_type'][0], 
                    vid=self.context_info['vid'][0],
                    file_ref=self.context_info['ops:Label_File_Info.ops:file_ref'][0]
                    )
        else:
            try:
                new_product=self.model_name(lid=self.context_info['lid'][0], 
                    name=self.context_info['pds:{}.pds:name'.format(self.type.title())][0], 
                    type_of=self.context_info['pds:{}.pds:type'.format(self.type.title())][0], 
                    vid=self.context_info['vid'][0],
                    file_ref=self.context_info['ops:Label_File_Info.ops:file_ref'][0]
                    )

            except TypeError:
            # happens when there's a tag missing in the xml that ELSA's db
            # doesn't allow to be null/blank. Many fields do allow this
            # (since most of what we need for an internal reference is the LID).
        
                print('ERROR adding context product ' +self.lid + ' , keyword missing ^', file=outlog)
                problem_products.append(self.lid)
                return False

        if self.context_info['vid'][0] == "":
            print('ERROR adding context product ' +self.lid + ' , vid missing ^', file=outlog)
            problem_products.append(self.lid)
            return False
        
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
            
            if self.type == 'investigation' and field.name == 'instrument_hosts':
                keyword = 'observing_system_components'
            else:
                keyword = field.name

            if self.type == 'instrument' and field.name == 'instrument_hosts':
                keyword = 'ref_lid_instrument_host'

            if self.type == 'instrument_host' and field.name == 'instruments':
                keyword = 'ref_lid_instrument'
            else:
                keyword = field.name
            
            # if self.type == 'facility' and field.name == 'instruments':
            #     keyword = 'ref_lid_instrument'

            if self.type == 'facility' or self.type == 'telescope':
                keyword = 'ref_lid_instrument'

            if keyword not in self.context_info.keys():
                continue        

            if self.context_info[keyword]==['']:
                continue

            if field.name == 'facilities':
                ref_model=get_model('facility')
            else:
                ref_model=get_model(field.name[:-1])
           
            for ref_lid in self.context_info[keyword]:
                if keyword == 'observing_system_components' or keyword == 'investigations' or keyword == 'targets':
                    ref_obj=ref_model.objects.filter(lid=ref_lid['id'])

                    if ref_lid['id'] not in id_list:
                        id_list.append(ref_lid['id'])
                    
                    if [self.lid, ref_lid['id']] not in reference_pairs:
                        reference_pairs.append([self.lid, ref_lid['id']])
                else:
                    ref_obj=ref_model.objects.filter(lid=ref_lid)

                    if ref_lid not in id_list:
                        id_list.append(ref_lid)
                    
                    if [self.lid, ref_lid] not in reference_pairs:
                        reference_pairs.append([self.lid, ref_lid])
                
                continue
            # past here is code from when this function also added links between
            # references, but doing all of that after we've added all of the 
            # new lid's to the db ends up being much cleaner and harder to 
            # break (at the expense of speed, but. This is ELSA, we need 
            # robust-ness more than just about anything else)

                if not ref_obj.exists(): #not in database
                    reference_pairs.append([self.lid, ref_lid])
                    if ref_lid not in id_list:
                        id_list.append(ref_lid) 
                    continue
                    
            
                ref_obj = ref_obj[0]
                    # note about the [0] above- looks like there are 
                    # duplicate entries for some products, so we grab only the first
                attr=getattr(self.model_name.objects.filter(lid=self.lid).first(), ref_obj, None)
                attr.add(ref_obj)
                print('\tInternal reference added; '+str(new_product.lid) + ' -> '+ str(ref_lid))
                
            #print(attr.all())
            

def add_reference_links(reference_pairs):
    '''Links up the context product's references to other products
    and adds those to the through tables* in the database.
    * https://www.sankalpjonna.com/learn-django/the-right-way-to-use-a-manytomanyfield-in-django
    https://docs.djangoproject.com/en/4.1/topics/db/examples/many_to_many/
            
    ''' 
    # print(problem_products)
    for pair in reference_pairs:
        if pair[0] in problem_products or pair[1] in problem_products: # I hate this. this should not be necessary. hamburger help me
            continue
        print(pair, file=outlog)
        
        model1 = get_model(pair[0].split(':')[4])
        model2 = get_model(pair[1].split(':')[4])
        lid1=pair[0]
        lid2=pair[1]
        obj1 = model1.objects.filter(lid=lid1).first()
        obj2 = model2.objects.filter(lid=lid2).first()

        print(model1, lid1, obj1)
        print(model2, lid2, obj2)

        if not obj1 or not obj2:
            continue

        # makes sure a manytomanyfield that links the objects actually exists.
        field1 = []
        for f in obj1._meta.get_fields():
            if f.name == pair[1].split(':')[4]+"s":
                field1.append(f)
            if f.name == pair[1].split(':')[4][:-1]+"ies":
                field1.append(f)
        # field1 = [f for f in obj1._meta.get_fields() if f.name == pair[1].split(':')[4]+"s"]
        print(field1)
        if len(field1)==1:
            
            #add reference from obj1 -> obj2 to database
            attr1=getattr(obj1, pair[1].split(':')[4]+"s", None)
            attr1.add(obj2)
            print('\tInternal reference added; '+pair[0] + ' -> '+ pair[1], file=outlog)
                
        
        # now repeat to add reference from obj2 -> obj1 
        # (if that's a valid reference, based on the fields we've defined in models.py)
        field2 = []
        for f in obj2._meta.get_fields():
            if f.name == pair[1].split(':')[4]+"s":
                field2.append(f)
            if f.name == pair[1].split(':')[4][:-1]+"ies":
                field2.append(f)
        # field2 = [f for f in obj2._meta.get_fields() if f.name == pair[0].split(':')[4]+"s"]
        if len(field2)==1:
            attr2=getattr(obj2, pair[0].split(':')[4]+"s", None)
            attr2.add(obj1)
            print('\tInternal reference added; '+pair[1] + ' -> '+ pair[0], file=outlog)
 

def test():
    '''Some early messing around with fetching things from the API search and
    json files. Keeping it around for now because it's useful for looking at/trying out
    specific search results.'''
    #change the last part of this url (data_class:Investigation) to something else (like data_class:Target) to get different types of context products
    # full_url = 'https://pds.nasa.gov/services/search/search?wt=json&q=product_class:Product_Context%20and%20data_class:Telescope'
    # testfile=urllib.request.urlopen(full_url).read()
    # data=json.loads(testfile)

    # for doc in data['response']['docs']: #loop through all the documents our request found

    #     #uncomment to print which keywords are used in the docs- identifiers/LID's, investigation names, etc. 
    #     #break incluced so we don't print the keyword list 100000 times.
    #     #print(doc.keys())
    #     #test comment to make things merge? idek
    #     print()
    #     for k in doc.keys():
    #         print(k, doc[k])
    #     #print(doc['identifier'])
    #     #break