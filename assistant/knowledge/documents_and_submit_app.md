<!-- watches: build/models.py#Product_Document, build/views.py#document, build/views.py#product_document, build/views.py#annex_product_document, build/forms.py#ProductDocumentForm, submit/views.py, templates/build/document, templates/submit -->
<!-- fingerprint:
     build/models.py#Product_Document      = 3938f8b5aa26
     build/views.py#document               = 3a1c3f98dc02
     build/views.py#product_document       = 9360f923e650
     build/views.py#annex_product_document = 4f76187a1bf9
     build/forms.py#ProductDocumentForm    = 661a3b67a67e
     submit/views.py                       = 1146328cfb92
     templates/build/document              = 91025e910e28
     templates/submit                      = ab2d66cdde1f
-->
<!-- reviewed: 2026-09-25 -->
<!-- baseline: 76f3f4b06c6f49212ee49c59521969d3c7d1699c -->
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
as empty however many documents are actually in it. It also copies the bundle's
citation into the document's own label, which carries one of its own.

ELSA cannot attach a file to a document. The document forms record a document and
its file name, but neither carries a file upload field and no page offers one; the
only upload in ELSA is for NetCDF. So a document's label names a file that is not
in the bundle, and PDS reports error.label.missing_file for it. This is counted as
an ELSA gap and reported to node staff rather than shown to the submitter, who has
no way to act on it, and it does not block a submission. Anyone asking how to
attach a document file should be told plainly that ELSA does not do that yet.

The file name a user types is completed from the declared File Format when it has
no extension: PDF/A gives .pdf and ASCII gives .txt, so "User_Guide" becomes
"User_Guide.pdf". A name that already has an extension is left alone, including a
different one.

What ELSA fills in for a document when the form does not ask for it, because
PDS4 requires a value and an empty one is not a value: the publication date
defaults to today, the edition name to 1.0, the file count to the number of
files, and the language to English, which is the only value PDS accepts there.
Anything optional the user left blank is dropped from the label rather than
written empty. author_list is no longer written at all: PDS4 deprecated it, and
validate says so on every document that carries one. The field is still on the
form, since it is how a user records who wrote the document.

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
