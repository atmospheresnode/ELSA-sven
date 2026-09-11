<!-- watches: build/views.py#build, build/models.py#Bundle, build/models.py#AdditionalCollections, build/models.py#NetCDFFile, templates/build/bundle/bundle.html -->
<!-- fingerprint:
     build/views.py#build                  = 6dde137f9726
     build/models.py#Bundle                = f0f1ab10d36c
     build/models.py#AdditionalCollections = 6f67e205c8a6
     build/models.py#NetCDFFile            = 83f3987e85ee
     templates/build/bundle/bundle.html    = 0a7809ac2938
-->
<!-- reviewed: 2026-09-11 -->
<!-- baseline: 4e0bb4b450ce5c2b934c567a62ac47485cca1529 -->
# External Bundle Workflow (AMA)

External bundles are ELSA's lighter-weight bundle type, used for the Atmospheres
Model Archive (AMA): the data itself is hosted externally, and ELSA produces the
PDS4 metadata bundle describing it.

To make an External bundle ready to submit, three components are REQUIRED:
1. Modification History, at least one dated version entry.
2. Citation Information, authors, publication year, description. Two ATM
   node editors (Lynn Neakrase, Lyle Huber) are included automatically, and
   users may add extra editors of their own if they wish.
3. Targets, at least one target (e.g. Mars) selected from the context list.

Alias is optional (yellow "Optional" badge) and never blocks submission.

The bundle page shows a Bundle Components card (top right) with the status of
each component: green "Added" when complete, red "Missing" when required and
absent. The Review & Submit button opens a checklist showing what is being sent:
required components, optional items, and content (uploaded NetCDF files and
documents).

The last row of the Bundle Components card is "Label check", under a PDS
Validation heading. Its badge says where the bundle stands: "Passed", a red
count such as "3 to fix", "Out of date" when the bundle was edited after the
last check, or "Not checked". Clicking the row opens the PDS Validation window.

That window runs the official NASA PDS validation tool against the bundle and
has two tabs. "What to fix" opens first and reports anything that needs fixing
in plain language, grouped by the card that fixes it: each item has a Fix button
that opens the right panel, and a "Why does this matter?" link that asks this
assistant to explain it. "Validation output" is the second tab and shows every
finding exactly as the PDS tool reported it, grouped by label, including the
findings the first tab does not show. A check usually starts by itself when the
results are missing or out of date, and takes a few seconds; the "Check again"
button runs one at any time and is always available.

The check re-runs by itself whenever the bundle changes. Anything that edits a
label counts: adding, editing or deleting a citation, a modification history, an
alias, a document, a collection or a NetCDF file. Whether a result is still
current is decided by the bundle's files rather than by a timestamp, so simply
opening or reloading the page never starts a check, and a change always does. A
run makes the result match the files again, so it settles instead of repeating.

When a check finishes, the window updates itself. It does not ask anyone to
reload the page, and it must not: a reload would abort a NetCDF upload that was
in progress, which is a bug this already had once. Items listed under "Need your attention" have to be
resolved before the bundle can be submitted for review; items under "Worth
reviewing" are advisory and do not stop a submission. Problems caused by ELSA
itself rather than by the user are not shown in the first tab; they go to the
ELSA team, and they do appear in the second tab.

Submission is gated on validation. The Review & Submit window states the
validation verdict at the top, and its Submit button is disabled while
validation has findings, has not run, or is out of date. Staff are never
blocked, and a validation that could not run does not block anyone.

After submission: Atmospheres node staff are notified by email and review the
bundle. The user can continue editing and resubmit at any time, the bundle page
shows the last-submitted timestamp. Bundle status appears in the Bundle Hub as
In Progress (yellow), Ready (green), or Submitted (blue).

Users upload NetCDF (.nc) files to External bundles; ELSA validates the
extension, processes the file, and generates a PDS4 XML label for each. Multiple
NetCDF files can be deleted at once with the bulk delete option.
