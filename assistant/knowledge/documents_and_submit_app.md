<!-- watches: build/models.py#Product_Document, build/views.py#document, build/views.py#product_document, build/views.py#annex_product_document, build/forms.py#ProductDocumentForm, submit/views.py, templates/build/document, templates/submit -->
<!-- fingerprint:
     build/models.py#Product_Document      = c6a56c43c704
     build/views.py#document               = 9d612a4101c8
     build/views.py#product_document       = 292c1dbdf274
     build/views.py#annex_product_document = a54af87bac59
     build/forms.py#ProductDocumentForm    = 63796cd3b091
     submit/views.py                       = 1146328cfb92
     templates/build/document              = 01c8059ee137
     templates/submit                      = ab2d66cdde1f
-->
<!-- reviewed: 2026-08-28 -->
<!-- baseline: d8df4b96320a4636b7a95ac426bec7ff1a1fa839 -->
# Uploading Documents and the Submit Uploads Area

Documents in a bundle: bundles have a document collection for user guides and
descriptive documents. On the bundle page, the document form collects the
document name, author list, publication date, description, files, and related
identification details; ELSA stores the document in the bundle's document
collection and generates its PDS4 label. External (AMA) bundles use a simpler
annex document form (document name, ID, file name, comment, document standard).
Deleting a document also removes its XML label from disk.

Separate "Submit" uploads area: besides building bundles in ELSA, there is a
Submit section where users can upload ready-made files directly to the
Atmospheres node:
- Upload Archive: send an archive bundle file with a description.
- Upload External: send external/AMA files with a description.
Each upload is recorded in a submission history list on the Submit main page,
and the ELSA team is notified by email automatically.
