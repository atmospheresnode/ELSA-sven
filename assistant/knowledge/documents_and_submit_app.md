<!-- watches: build/models.py#Product_Document, build/views.py#document, build/views.py#product_document, build/views.py#annex_product_document, build/forms.py#ProductDocumentForm, submit/views.py, templates/build/document, templates/submit -->
<!-- fingerprint:
     build/models.py#Product_Document      = c6a56c43c704
     build/views.py#document               = 014868da93c4
     build/views.py#product_document       = 6aac0d30e39a
     build/views.py#annex_product_document = 8bb741e91ecd
     build/forms.py#ProductDocumentForm    = 9390693a46a0
     submit/views.py                       = 1146328cfb92
     templates/build/document              = 01c8059ee137
     templates/submit                      = ab2d66cdde1f
-->
<!-- reviewed: 2026-09-11 -->
<!-- baseline: 36cf57ed9df9884d9d440bd8d12e8bdd228b0e8e -->
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

Two rules on document names, both PDS4's, now checked by the document form when
it is submitted rather than later by the validator:

- The file name needs a real extension: User_Guide.pdf, not User_Guide. It must
  also start with a letter or digit and use only letters, digits, dots, dashes
  and underscores, so a space in it is refused too.
- Two documents in one bundle cannot share a name. The name becomes the product
  identifier that the collection inventory lists, and a collection cannot list
  the same identifier twice. The same name in a different bundle is fine, since
  identifiers are scoped to the bundle, and renaming a document to what it is
  already called is not a clash with itself.

Separate "Submit" uploads area: besides building bundles in ELSA, there is a
Submit section where users can upload ready-made files directly to the
Atmospheres node:
- Upload Archive: send an archive bundle file with a description.
- Upload External: send external/AMA files with a description.
Each upload is recorded in a submission history list on the Submit main page,
and the ELSA team is notified by email automatically.
