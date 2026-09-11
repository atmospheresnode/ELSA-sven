<!-- watches: build/views.py#submit_bundle_internal, review/views.py, submit/views.py#submit_main, submit/views.py#upload_archive, submit/views.py#upload_external, tutorial/urls.py -->
<!-- fingerprint:
     build/views.py#submit_bundle_internal = 021a16814676
     review/views.py                       = b7483ad97eb6
     submit/views.py#submit_main           = 32904e6f1c1e
     submit/views.py#upload_archive        = d4b10fbe4906
     submit/views.py#upload_external       = 1f663bead378
     tutorial/urls.py                      = 7fdbf2eaa074
-->
<!-- reviewed: 2026-09-10 -->
<!-- baseline: 60b6e509aee8ac54072f7fde271746a058312675 -->
# Review, Submission, and Getting Help

Submitting a bundle: on the bundle page, the **Review & Submit** button opens a
checklist (required components on the left, bundle content, NetCDF files and
documents, on the right). Once Modification History, Citation Information, and
at least one Target are complete, the submit button activates. Submitting
notifies the Atmospheres node staff by email; they review the bundle for PDS4
compliance and archive readiness. You can keep editing after submission and
resubmit, the bundle page shows when it was last submitted.

Submitting also starts a full PDS validation in the background, which reads
inside the data files as well as checking the labels. It does not hold up the
submission: the result is recorded so node staff have it when they look. The
quicker checks shown in the PDS Validation card while you work skip the data
files, which is what makes them fast.

**Why the Submit button may be disabled.** A bundle cannot be submitted while the
PDS Validation card still lists items under "Need your attention". Fixing them
here is the point: a problem the validation tool can see would otherwise come
back from review days later. The button is disabled when any of these is true:

- The bundle has not been checked yet. Run the check.
- A check is still running. It takes a few seconds.
- Items still need attention. Each has a Fix button that opens the right panel.
- The bundle changed after the last check, so the result no longer describes what
  would be submitted. Run the check again.

Things that do NOT block submission: items under "Worth reviewing", which are
advisory; problems caused by ELSA itself rather than by anything you entered,
which are sent to the ELSA team instead; and a check that could not run at all,
since being unable to check is not evidence of a problem. Atmospheres node staff
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
