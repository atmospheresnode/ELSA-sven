<!-- watches: build/views.py#build, build/models.py#Bundle, build/models.py#AdditionalCollections, build/models.py#NetCDFFile, templates/build/bundle/bundle.html -->
<!-- fingerprint:
     build/views.py#build                  = 6dde137f9726
     build/models.py#Bundle                = 81fb6e671b24
     build/models.py#AdditionalCollections = 6f67e205c8a6
     build/models.py#NetCDFFile            = 0259cb4196cf
     templates/build/bundle/bundle.html    = e7bae0628aea
-->
<!-- reviewed: 2026-09-10 -->
<!-- baseline: 60b6e509aee8ac54072f7fde271746a058312675 -->
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
absent. The Review & Submit button opens a checklist showing required
components, optional items, and content (uploaded NetCDF files and documents).
Submission is enabled once the three required components are complete.

The bundle page also has a PDS Validation card. It runs the official NASA PDS
validation tool against the bundle and reports anything that needs fixing, in
plain language, grouped by the card that fixes it: each item has a Fix button
that opens the right panel, and a "Why does this matter?" link that asks this
assistant to explain it. A check usually starts by itself when the results are
missing or out of date, and takes a few seconds; the "Check again" button runs
one at any time. Items listed under "Need your attention" have to be resolved
before the bundle can be submitted for review; items under "Worth reviewing" do
not block anything. Items under "Need your attention" are things to resolve; items under
"Worth reviewing" are advisory and do not stop a submission. Problems caused by
ELSA itself rather than by the user are not shown there; they go to the ELSA
team. Results are marked as out of date if the bundle is edited after a check,
and the raw tool output is available under "Technical detail".

After submission: Atmospheres node staff are notified by email and review the
bundle. The user can continue editing and resubmit at any time, the bundle page
shows the last-submitted timestamp. Bundle status appears in the Bundle Hub as
In Progress (yellow), Ready (green), or Submitted (blue).

Users upload NetCDF (.nc) files to External bundles; ELSA validates the
extension, processes the file, and generates a PDS4 XML label for each. Multiple
NetCDF files can be deleted at once with the bulk delete option.
