<!-- watches: build/models.py#Product_Document, build/views.py#document, build/views.py#product_document, build/views.py#annex_product_document, build/forms.py#ProductDocumentForm, submit/views.py, templates/build/document, templates/submit -->
<!-- fingerprint:
     build/models.py#Product_Document      = c6a56c43c704
     build/views.py#document               = d050ffe8a353
     build/views.py#product_document       = 2bdd84d767ca
     build/views.py#annex_product_document = 7b97c3138d39
     build/forms.py#ProductDocumentForm    = 63796cd3b091
     submit/views.py                       = 1146328cfb92
     templates/build/document              = 01c8059ee137
     templates/submit                      = ab2d66cdde1f
-->
<!-- reviewed: 2026-09-11 -->
<!-- baseline: cd1d702dbe5b7b2c176a8142b6bce02db53b244b -->
# Uploading Documents and the Submit Uploads Area

Documents in a bundle: bundles have a document collection for user guides and
descriptive documents. On the bundle page, the document form collects the
document name, author list, publication date, description, files, and related
identification details; ELSA stores the document in the bundle's document
collection and generates its PDS4 label. External (AMA) bundles use a simpler
annex document form (document name, ID, file name, comment, document standard).
Deleting a document also removes its XML label from disk.

Adding, editing or deleting a document rewrites the collection's inventory: the
table listing its members and the record count in the collection label, both of
which PDS checks. A collection whose label says it has zero members is rejected
as empty however many documents are actually in it.

Two rules on document file names, both of which PDS enforces and neither of
which ELSA checks at entry time: the file name needs a real extension
(guide.pdf, not guide), and two documents in one collection cannot share a name,
because the name becomes the identifier the inventory lists and a collection
cannot list the same product twice.

Separate "Submit" uploads area: besides building bundles in ELSA, there is a
Submit section where users can upload ready-made files directly to the
Atmospheres node:
- Upload Archive: send an archive bundle file with a description.
- Upload External: send external/AMA files with a description.
Each upload is recorded in a submission history list on the Submit main page,
and the ELSA team is notified by email automatically.
