<!-- watches: friends/views.py, templates/friends/bundle_hub.html, build/views.py, templates/build/bundle/bundle.html -->
<!-- reviewed: 2026-07-12 -->
# Deleting Bundles (Single and Bulk) and Deleting Files

Deleting ONE bundle: open the bundle's page and click the red **Delete Bundle**
button. A confirmation modal appears; confirming removes the bundle's directory
and files on disk plus its database records. Deletion is permanent and cannot
be undone.

Deleting SEVERAL bundles at once (bulk delete): the **Bundle Hub** has a bulk
delete feature.
1. Open the Bundle Hub (username menu, top right).
2. Hover over a bundle card; a checkbox appears in its corner. Tick the
   checkbox on every bundle you want to remove (there is also a **Select all**
   checkbox above the cards).
3. Click the **Delete** button that activates when bundles are selected.
4. A confirmation modal appears; confirming deletes all selected bundles
   (files on disk and database records). A success message shows how many
   bundles were deleted.
You can only delete your own bundles.

Deleting NetCDF files within an External bundle: on the bundle page, select the
files with their checkboxes and use the bulk delete button in the NetCDF files
section. This removes both the .nc files and their generated XML labels.

There is no undo for any deletion. Download the bundle first (green Download
Bundle button) if you want a backup.
