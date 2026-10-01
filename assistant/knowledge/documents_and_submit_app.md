<!-- watches: build/models.py#Product_Document, build/views.py#document, build/views.py#product_document, build/views.py#annex_product_document, build/forms.py#ProductDocumentForm, submit/views.py, templates/build/document, templates/submit -->
<!-- fingerprint:
     build/models.py#Product_Document      = 3938f8b5aa26
     build/views.py#document               = 3a1c3f98dc02
     build/views.py#product_document       = a9324b7ee4e0
     build/views.py#annex_product_document = b624d3c8f4c2
     build/forms.py#ProductDocumentForm    = 628c27309201
     submit/views.py                       = 1146328cfb92
     templates/build/document              = 76acde04fe65
     templates/submit                      = ab2d66cdde1f
-->
<!-- reviewed: 2026-09-25 -->
<!-- baseline: 7294b1cab0ba188a0fba03e8de9f5e8367bf1b5a -->
# Uploading Documents and the Submit Uploads Area

Documents in a bundle: bundles have a document collection for user guides and
descriptive documents. On the bundle page, the document form collects the
document name, author list, publication date, description, the document file
itself, and related identification details; ELSA stores the document and its file
in the bundle's document collection and generates its PDS4 label. External (AMA)
bundles use a simpler annex document form (document name, ID, comment, and the
document file). Deleting a document also removes its XML label and its file from
disk.

Adding, editing or deleting a document rewrites the collection's inventory: the
table listing its members and the record count in the collection label, both of
which PDS checks. A collection whose label says it has zero members is rejected
as empty however many documents are actually in it. It also copies the bundle's
citation into the document's own label, which carries one of its own.

Every document is uploaded with its file, in both Archive and External bundles.
The file is required when adding a document. PDS accepts only two formats for
documents, so those are the only two ELSA accepts:

- PDF/A-1 (.pdf), the archival kind of PDF. An ordinary PDF, or a newer PDF/A-2
  or PDF/A-3, is refused with instructions. To make one: in Microsoft Word on
  Windows, File > Export > Create PDF/XPS and tick "ISO 19005-1 compliant
  (PDF/A)"; in LibreOffice, File > Export as PDF, tick "Archive (PDF/A, ISO
  19005)" and choose PDF/A-1b; in Adobe Acrobat, save as PDF/A-1b. If an editor
  offers no PDF/A option, LibreOffice (free) can open the file and export it.
- Plain text (.txt) saved as UTF-8. Recorded as "7-Bit ASCII Text" when every
  character is plain ASCII and "UTF-8 Text" otherwise.

Word (.docx), Markdown, HTML and other formats are not accepted; export them to
PDF/A-1b first. The maximum size is 100 MB.

The uploaded file's own name becomes the file name in the label, so there is no
separate file name to type. Spaces become underscores and accents are dropped, so
"My Guide.pdf" is stored as My_Guide.pdf. Two documents in one bundle cannot use
the same file name.

To replace a document's file, open the document with Edit (in the Collections
card, under the document collection) and upload the new one; the old file is
removed. Leaving the upload empty on the Edit page keeps the current file.

Documents added before uploads existed have no file. The validation panel lists
each one as "Attach the file for this document"; the fix is to open that document
with Edit and upload its file. If the validation tool reports "A document's PDF is
not PDF/A-1", the file claims to be PDF/A-1 but does not conform: export it again
as PDF/A-1b from the original and upload it with Edit.

What ELSA fills in for a document when the form does not ask for it, because
PDS4 requires a value and an empty one is not a value: the publication date
defaults to today, the edition name to 1.0, the file count to the number of
files, and the language to English, which is the only value PDS accepts there.
Anything optional the user left blank is dropped from the label rather than
written empty. author_list is no longer written at all: PDS4 deprecated it, and
validate says so on every document that carries one. The field is still on the
form, since it is how a user records who wrote the document.

A rule on document names, PDS4's, checked by the document form when it is
submitted rather than later by the validator:

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
