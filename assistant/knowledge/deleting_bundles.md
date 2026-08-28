<!-- watches: build/views.py#bundle_delete, build/views.py#bundle_delete_new, build/views.py#delete_collection, build/views.py#bulk_delete_netcdf, build/views.py#delete_product_document, friends/views.py#delete_bundles, friends/views.py#bundle_hub, templates/friends/bundle_hub.html -->
<!-- fingerprint:
     build/views.py#bundle_delete           = 344b455d3c1c
     build/views.py#bundle_delete_new       = 772fbafa6b88
     build/views.py#delete_collection       = ccc76fbf5208
     build/views.py#bulk_delete_netcdf      = 1b47f573b9c5
     build/views.py#delete_product_document = 89b21fc14bd0
     friends/views.py#delete_bundles        = 03e19e347f04
     friends/views.py#bundle_hub            = 02a643915c11
     templates/friends/bundle_hub.html      = 1769e80d5787
-->
<!-- reviewed: 2026-08-28 -->
<!-- baseline: d8df4b96320a4636b7a95ac426bec7ff1a1fa839 -->
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
