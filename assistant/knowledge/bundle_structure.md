<!-- watches: build/models.py#Bundle, build/models.py#Collections, build/models.py#AdditionalCollections, build/models.py#Product_Bundle, build/models.py#Product_Collection, build/forms.py#BundleForm, build/forms.py#AdditionalCollectionForm -->
<!-- fingerprint:
     build/models.py#Bundle                  = 7e1c83a0d383
     build/models.py#Collections             = efb0c8c55c09
     build/models.py#AdditionalCollections   = 597619b2a6fd
     build/models.py#Product_Bundle          = 066355abe899
     build/models.py#Product_Collection      = 0595895e042e
     build/forms.py#BundleForm               = 89db5879e013
     build/forms.py#AdditionalCollectionForm = a51e7bc09d85
-->
<!-- reviewed: 2026-09-25 -->
<!-- baseline: ab6b3482d9893d5e8b7e506fadff1e8125905f6a -->
# Bundle and Collection Structure

PDS4 Information Model (v1.24 / 1O00): a Product_Bundle is an aggregate product
holding a table of references to one or more collections. Its label contains a
required Identification_Area, an optional Context_Area, a Bundle class
(bundle_type [1..1], "Archive" for delivery to the PDS, "Supplemental"
otherwise; plus optional description), and one or more Bundle_Member_Entry
records (lid_reference, member_status, and reference_type all required).

A Collection (Product_Collection) describes a set of related basic products.
Attributes: collection_type [1..1] and optional description. The accepted values
of collection_type are exactly: Browse, Calibration, Context, Data, Document,
External, Geometry, Miscellaneous, SPICE Kernel, and XML Schema. Note the
spelling of "XML Schema" (a space, not an underscore). Basic products (data
files, documents) belong to collections; collections belong to the bundle.

Typical bundle layout: a bundle contains a data collection (the science data,
e.g. NetCDF files with their XML labels), a document collection (user guides,
descriptions), and references to context products.

In ELSA: ELSA builds this structure for you. When you create a bundle, ELSA
generates the bundle product label and collection labels; uploading NetCDF files
or documents places them in the right collection with generated PDS4 labels.
Each collection also gets an inventory table, a .csv file sitting beside the
collection label that lists every member product. ELSA writes and rewrites it
for you as products are added or removed; PDS requires it, and it must name at
least one member, so a collection with nothing in it will not validate.
ELSA has two bundle types: "Archive" (a full PDS4 archive bundle) and "External"
(a lighter bundle, used for the Atmospheres Model Archive (AMA), where the data
is hosted externally and ELSA produces the metadata bundle).

Collection types in ELSA: an Archive bundle gets a document collection (type
Document), a context collection (type Context), and a schema collection (type
XML Schema). In an External (AMA) bundle every collection is of type External,
including the document collection: its members carry AMA LIDs in the
urn:nasa:pds-ama namespace, which a Document collection does not admit. The
directory is still named "document" and its LID still ends in ":document" - only
the declared type differs.
