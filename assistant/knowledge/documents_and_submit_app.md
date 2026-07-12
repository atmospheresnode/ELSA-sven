<!-- watches: build/views.py, build/forms.py, submit/views.py, templates/submit -->
<!-- reviewed: 2026-07-10 -->
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
