<!-- watches: build/models.py#Version, build/models.py#Bundle, build/models.py#Product_Collection, build/models.py#Product_Bundle -->
<!-- fingerprint:
     build/models.py#Version            = 4f1cc4036529
     build/models.py#Bundle             = 7e1c83a0d383
     build/models.py#Product_Collection = 0595895e042e
     build/models.py#Product_Bundle     = 066355abe899
-->
<!-- reviewed: 2026-09-25 -->
<!-- baseline: 76f3f4b06c6f49212ee49c59521969d3c7d1699c -->
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
LID. It is generated from the bundle name when the bundle is created; the create
form also offers an optional Bundle ID field for anyone who wants to set it
themselves. Products within the bundle get LIDs that extend the bundle LID
(e.g. urn:nasa:pds:mybundle:document:userguide).

Users never have to worry about which characters are allowed. ELSA cleans every
segment it builds a LID from, the bundle name or ID, the collection name, and an
uploaded file's name, replacing anything outside the permitted set with an
underscore and lowercasing the rest. A NetCDF uploaded as
"00000.atmos_average_pstd_-_Copy.nc" keeps that name on disk and in the label's
file_name, and its LID segment becomes
"00000.atmos_average_pstd_-_copy.nc". A file does not need renaming to
validate.

Terminology: the LID IS a URN, so "URN", "LID", "logical identifier", and
"Bundle ID" all refer to the same identifier for a bundle. Each bundle's
LID/URN is listed in the user's bundle context, so quote it directly when
asked; there is no need to send the user elsewhere to look it up.
