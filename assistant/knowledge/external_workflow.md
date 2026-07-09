<!-- watches: build/views.py, templates/build/bundle/bundle.html, build/models.py -->
# External Bundle Workflow (AMA)

External bundles are ELSA's lighter-weight bundle type, used for the Atmospheres
Model Archive (AMA): the data itself is hosted externally, and ELSA produces the
PDS4 metadata bundle describing it.

To make an External bundle ready to submit, three components are REQUIRED:
1. Modification History, at least one dated version entry.
2. Citation Information, authors, publication year, description (editors are
   added automatically by Atmospheres node staff).
3. Targets, at least one target (e.g. Mars) selected from the context list.

Alias is optional (yellow "Optional" badge) and never blocks submission.

The bundle page shows a Bundle Components card (top right) with the status of
each component: green "Added" when complete, red "Missing" when required and
absent. The Review & Submit button opens a pre-flight checklist showing required
components, optional items, and content (uploaded NetCDF files and documents).
Submission is enabled once the three required components are complete.

After submission: Atmospheres node staff are notified by email and review the
bundle. The user can continue editing and resubmit at any time, the bundle page
shows the last-submitted timestamp. Bundle status appears in the Bundle Hub as
In Progress (yellow), Ready (green), or Submitted (blue).

Users upload NetCDF (.nc) files to External bundles; ELSA validates the
extension, processes the file, and generates a PDS4 XML label for each. Multiple
NetCDF files can be deleted at once with the bulk delete option.
