# Bundle and Collection Structure

PDS4 Information Model (v1.24 / 1O00): a Product_Bundle is an aggregate product
holding a table of references to one or more collections. Its label contains a
required Identification_Area, an optional Context_Area, a Bundle class
(bundle_type [1..1] — "Archive" for delivery to the PDS, "Supplemental"
otherwise; plus optional description), and one or more Bundle_Member_Entry
records (lid_reference, member_status, and reference_type all required).

A Collection (Product_Collection) describes a set of related basic products.
Attributes: collection_type [1..1] (e.g. Data, Document, Context, XML_Schema,
Browse, Calibration) and optional description. Basic products (data files,
documents) belong to collections; collections belong to the bundle.

Typical bundle layout: a bundle contains a data collection (the science data,
e.g. NetCDF files with their XML labels), a document collection (user guides,
descriptions), and references to context products.

In ELSA: ELSA builds this structure for you. When you create a bundle, ELSA
generates the bundle product label and collection labels; uploading NetCDF files
or documents places them in the right collection with generated PDS4 labels.
ELSA has two bundle types: "Archive" (a full PDS4 archive bundle) and "External"
(a lighter bundle, used for the Atmospheres Model Archive (AMA), where the data
is hosted externally and ELSA produces the metadata bundle).
