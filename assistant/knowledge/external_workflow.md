<!-- watches: build/views.py#build, build/models.py#Bundle, build/models.py#AdditionalCollections, build/models.py#NetCDFFile, templates/build/bundle/bundle.html -->
<!-- fingerprint:
     build/views.py#build                  = 6dde137f9726
     build/models.py#Bundle                = 7e1c83a0d383
     build/models.py#AdditionalCollections = 597619b2a6fd
     build/models.py#NetCDFFile            = 83f3987e85ee
     templates/build/bundle/bundle.html    = 60b8deb789cb
-->
<!-- reviewed: 2026-09-25 -->
<!-- baseline: ab4b3d05a8abea19bfcea67d16b5677bf0ad42d4 -->
# External Bundle Workflow (AMA)

External bundles are ELSA's lighter-weight bundle type, used for the Atmospheres
Model Archive (AMA): the data itself is hosted externally, and ELSA produces the
PDS4 metadata bundle describing it.

To make an External bundle ready to submit, these are REQUIRED:
1. Modification History, at least one dated version entry.
2. Citation Information, with publication year, description and at least one
   author (a person or an organization). Two ATM node editors (Lynn Neakrase,
   Lyle Huber) are included automatically, and users may add extra editors of
   their own if they wish. The number of authors is chosen when the citation is
   created and cannot be changed afterwards (Edit only fills in the names), so
   the form refuses a citation with no author. A citation that already has none
   has to be deleted and created again with at least one author.
3. Targets, at least one target selected from the context list (External bundles
   offer laboratory analog targets, e.g. Mars Laboratory Analog).
4. At least one NetCDF file that ELSA has processed, uploaded into a collection
   (create a collection in the Collections card first if there is none). A file
   ELSA could not process does not count.

Alias is optional (yellow "Optional" badge) and never blocks submission.

The bundle page shows a Bundle Components card (top right) with the status of
each component: green "Added" when complete, red "Missing" when required and
absent. The Review & Submit button opens a window with two columns. On the left,
"Before you can submit" is the same list the PDS Validation window shows, with
the same Fix buttons. On the right, "What you are sending" is a plain inventory:
citation, modification history, targets, alias (optional), documents, and each
collection with its NetCDF files, each with a button to edit it. The right column
does not judge anything; whether something needs changing is the left column's job.

The last row of the Bundle Components card is "Label check", under a PDS
Validation heading. Its badge says where the bundle stands: "Passed", a red
count such as "3 to fix", or "Checking…" while a check runs in the background.
"Out of date" and "Not checked" only appear when automatic checking is switched
off. Clicking the row opens the PDS Validation window.

That window runs the official NASA PDS validation tool against the bundle and
has two tabs. "What to fix" opens first and reports anything that needs fixing
in plain language, grouped by the card that fixes it: each item has a Fix button
that opens the right panel, and a "Why does this matter?" link that asks this
assistant to explain it. "Validation output" is the second tab and shows every
finding exactly as the PDS tool reported it, grouped by label, including the
findings the first tab does not show. A check starts by itself when the
results are missing or out of date, and takes a few seconds for a typical bundle
(longer for bundles with many files); the "Check again" button runs one at any
time and is always available. Every check is of the whole bundle, because many
problems only show up across labels, and a single edit often rewrites several
labels at once. If the server is already running as many checks as it allows,
the page says it is waiting and tries again by itself rather than reporting a
failure.

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

Submission is gated on two separate things. First, ELSA's own requirements:
a bundle needs Citation Information, a Modification History entry, and at least
one target before it can go for review, and an External bundle also needs at
least one author on its citation and at least one processed NetCDF file. These
are checked against the bundle itself rather than against its labels, because
PDS4 allows all of them except the citation to be absent, so the PDS tool reports
nothing when they are. They appear in the PDS Validation window alongside the findings, marked as
ELSA's requirement, and they block everyone including staff.

Second, validation. The Review & Submit window states the validation verdict at
the top. If the bundle has changed since its last check, or was never checked,
opening the window starts a check and shows its progress there; when it
finishes, the verdict, the list and the Submit button update in place. If the
last check still matches the bundle, Submit is available straight away. The
Submit button is disabled while validation has findings, is running, or is out
of date. Here staff are never blocked, and a validation that
could not run does not block anyone.

After submission: Atmospheres node staff are notified by email and review the
bundle. The user can continue editing and resubmit at any time, the bundle page
shows the last-submitted timestamp. Bundle status appears in the Bundle Hub as
In Progress (yellow), Ready (green), or Submitted (blue).

Users upload NetCDF (.nc) files to External bundles; ELSA validates the
extension, processes the file, and generates a PDS4 XML label for each. Multiple
NetCDF files can be deleted at once with the bulk delete option.

Before an upload starts, ELSA checks that the server has enough free disk space
for the selected files. If it does not, nothing is sent and a dialog shows the
upload size and the largest upload the server can take right now, so uploading
fewer files at a time may work. The same dialog offers a ready-made report to
Team ELSA (drafted for the user, editable, sent with one click); replies go to the
email on the user's account.
