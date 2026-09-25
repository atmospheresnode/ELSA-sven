<!-- watches: build/views.py#submit_bundle_internal, review/views.py, submit/views.py#submit_main, submit/views.py#upload_archive, submit/views.py#upload_external, tutorial/urls.py -->
<!-- fingerprint:
     build/views.py#submit_bundle_internal = 4a6d87e164e6
     review/views.py                       = b7483ad97eb6
     submit/views.py#submit_main           = 32904e6f1c1e
     submit/views.py#upload_archive        = d4b10fbe4906
     submit/views.py#upload_external       = 1f663bead378
     tutorial/urls.py                      = 7fdbf2eaa074
-->
<!-- reviewed: 2026-09-24 -->
<!-- baseline: ab6b3482d9893d5e8b7e506fadff1e8125905f6a -->
# Review, Submission, and Getting Help

Submitting a bundle: on the bundle page, the **Review & Submit** button opens a
window with "Before you can submit" on the left (the same list of things to fix
as the PDS Validation window, each with a Fix button) and "What you are sending"
on the right (citation, modification history, targets, alias, documents and
NetCDF files, as an inventory). If the bundle changed since its last validation
check, opening the window runs the check first and shows its progress at the
top; this takes a few seconds for a typical bundle. Once nothing is left to fix,
including Modification History, Citation Information and at least one Target
(and, for an External bundle, at least one author on the citation and at least
one processed NetCDF file), the Submit button activates. Submitting
notifies the Atmospheres node staff by email; they review the bundle for PDS4
compliance and archive readiness. You can keep editing after submission and
resubmit, the bundle page shows when it was last submitted.

Submitting does not run another check. The bundle can only be submitted when
its latest check matches it exactly, and that check already covers the data
files as well as the labels, so node staff receive its result in the
notification.

**Why the Submit button may be disabled.** A bundle cannot be submitted while the
PDS Validation card still lists items under "Need your attention". Fixing them
here is the point: a problem the validation tool can see would otherwise come
back from review days later. The button is disabled when any of these is true:

- A check is still running. It takes a few seconds, and opening Review & Submit
  starts one by itself when the bundle has changed or was never checked.
- Items still need attention. Each has a Fix button that opens the right panel.
- The bundle changed after the last check and no new check has finished yet.

Things that do NOT block submission: items under "Worth reviewing", which are
advisory and say so in as many words, since anything shown to a user has to state
either what to do or that there is nothing to do; problems caused by ELSA itself
rather than by anything you entered, which are sent to the ELSA team instead and
never shown; and a check that could not run at all, since being unable to check
is not evidence of a problem.

Context product names are one of the ELSA-side ones. validate compares the name
in a label to the title of the registered PDS context product, which PDS forms as
"<name> <type>", so the AMA investigation is published as "Atmospheric Modeling
Annex Individual Investigation". ELSA writes that published title. A user picks a
context product from a list and never types its name, so a mismatch is never
theirs to fix.

The notification the node receives when a bundle is submitted carries the
validation verdict: what is still outstanding for the submitter and which file
each item is about, what is worth a look during review, and a count of anything
ELSA caused. Nobody has to open the bundle to find out whether it is clean. Atmospheres node staff
can always submit regardless, so if something looks wrong with the check itself,
contact them rather than working around it.

There is also a Review area in ELSA with a bundle review form: reviewers fill
it out, can save a draft and resume later (the draft is remembered in the
browser), and the completed review can be generated as a DOCX or PDF document.
A separate Submit section lets users upload archive or external bundle files
directly to the node with a description; uploads appear in a submission
history and the team is emailed automatically.

Getting help:
- **Tutorial** pages: step-by-step guides on building a bundle, bundles and
  collections, and the context, data, and document collections.
- **About** page: what ELSA is, the team, and release notes for recent versions.
- **Contact** page: reach the Atmospheres node / ELSA team directly by email -
  best for account issues, review questions, or anything the assistant can't do.
- **This assistant**: can also send feedback (bug reports, suggestions,
  questions) straight to the ELSA team, just ask it to.
