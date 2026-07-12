<!-- watches: build/models.py -->
# LIDs, VIDs, and the Identification Area

PDS4 Information Model (v1.24 / 1O00): every PDS4 product label has an
Identification_Area with these required attributes:
- logical_identifier (LID) [1..1], the unique, permanent identifier of the
  product. Bundle LIDs follow the form urn:nasa:pds:<bundle_id> (all lowercase;
  letters, digits, dash, underscore, and period allowed).
- version_id (VID) [1..1], the product version, M.n form (e.g. 1.0). A LID plus
  a VID is written as a LIDVID: urn:nasa:pds:mybundle::1.0.
- title [1..1], the product's title.
- information_model_version [1..1], the PDS4 IM version the label conforms to.
- product_class [1..1], e.g. Product_Bundle, Product_Collection.

Optional components of the Identification_Area: Alias_List,
Citation_Information, Modification_History, and License_Information.

In ELSA: the Bundle ID (shown under the bundle name on the bundle page) is the
LID, auto-generated from the bundle name when the bundle is created, users do
not type it by hand. Products within the bundle get LIDs that extend the bundle
LID (e.g. urn:nasa:pds:mybundle:document:userguide).

Terminology: the LID IS a URN, so "URN", "LID", "logical identifier", and
"Bundle ID" all refer to the same identifier for a bundle. Each bundle's
LID/URN is listed in the user's bundle context, so quote it directly when
asked; there is no need to send the user elsewhere to look it up.
