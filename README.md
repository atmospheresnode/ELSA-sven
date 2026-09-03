# Educational Labeling System at Atmospheres (ELSA)
Most up-to-date repository for the Educational Labeling System for Atmospheres (ELSA) on Python 3.

Visit our website: https://atmos.nmsu.edu/elsa/


## **Overview**  
**ELSA** represents ongoing efforts to support Planetary Data System (PDS) data providers in preparing datasets for submission to the Atmospheres Node for archiving. This project integrates multiple facets, including:  
- Embracing the philosophies of archiving with the PDS.  
- Utilizing the PDS4 Archiving Standard for metadata and label creation.  
- Leveraging interactive online environments to interface data providers with the Atmospheres Node seamlessly.  

At its core, **ELSA** facilitates the construction of viable **PDS4 label templates** for use in archive bundles. Using a *‘top-down’ approach*, ELSA auto-populates XML label templates with bundle-specific information entered through an intuitive, online query system.  

This approach takes advantage of Atmospheres Node expertise to ensure all connections between the standard PDS4 hierarchies are accurate. Metadata (both complete and partial) is inherited between labels, while conditional options are dynamically queried from the user, ensuring a streamlined and efficient label creation process.


## **Release Notes** 
This section provides a timeline of ELSA's release history, highlighting key updates, new features, bug fixes, and improvements, with the most recent release listed first.

### **Current Version** 

> ### **Version 1.37.10 (August 28, 2026)**
- AMA Metadata: Metadata is now edited from the Files card. Open a data label and use the new "Edit AMA metadata" button.
- AMA Metadata: The three sections are now tabs rather than an accordion, and each tab shows how many fields it holds.
- AMA Metadata: Each section now says whether its values are shared with every file in the collection or belong to just this file, and that choice is now shown correctly when the section is reopened.
- AMA Metadata: Values can now be copied across from another file in the same collection.
- AMA Metadata: Model type, horizontal grid type, and vertical grid type are now picked from a list of the values PDS4 accepts, instead of being typed in. Values saved earlier that PDS4 does not accept are flagged when the section is opened.
- Files Card: Data labels now show how much of the AMA metadata they carry, and point out labels that carry none.
- AMA Metadata: Saving now shows a single message describing what changed, instead of one message per section.
- AMA Metadata: Fixed a save reporting that other files had been reset to the collection values when none had been.
- External and Archive Bundles: Fixed the collection type written into labels. Schema collections are now written as "XML Schema", and every collection in an External bundle is now typed External, including the document collection. Labels already on disk have been corrected.
- ELSA Assistant: Corrected the answers about collection types.


### **Previous Versions** 

> ### **Version 1.37.9 (August 21, 2026)**
- Files Card: Labels are now grouped under the collection they belong to, instead of being listed flat, on both External and Archive bundles.
- Files Card: Data labels now name the NetCDF file they describe, and show whether that file was processed successfully.
- Files Card: Labels are now ordered with the bundle label first, then the document collection, then collections in the order they were added.
- Files Card: A search box and filters by label type have been added.
- XML Viewer: The full path of the open label is now shown, and a Copy XML button has been added.
- External and Archive Bundles: The bundle page now loads faster, as labels are opened on demand rather than all being loaded up front.
- Review & Submit: Fixed every collection reporting zero NetCDF files.
- Bundle: Fixed an error page appearing when opening a bundle whose creation did not finish.

> ### **Version 1.37.8 (August 14, 2026)**
- External Bundle: AMA metadata is now set per collection, with defaults every file inherits and per-file values where needed.
- External Bundle: AMA metadata can now be edited directly on the bundle page.
- NetCDF Files: Fixed bulk delete selecting files from other collections and leaving files behind on disk.
- AMA Metadata: Fixed accented characters producing labels that fail PDS4 validation.
- Sign In: Fixed passkey verification failing on the live site.

> ### **Version 1.37.7 (August 07, 2026)**
- Sign In: Passkey sign-in has been introduced, using a fingerprint, face, screen lock, or security key.
- Sign In: The flow is now identifier-first, with one sign-in method per screen.
- One-Time Codes: Codes are now stored hashed, expire after 15 minutes, are limited to five attempts, and can be resent.
- Create an Account: Password strength rules, username validation, and duplicate email checks are now enforced.
- Security: Sign-in and sign-up attempts are now rate limited.
- Sign In and Account Pages: Redesigned with a shared ELSA layout, and the one-time code email is now branded.
- External Bundle: AMA metadata that cannot be read from a NetCDF file can now be entered in ELSA.

> ### **Version 1.37.6 (July 31, 2026)**
- Targets: The target menu now groups targets under the investigations PDS lists them for.
- Targets: Adding a target outside that list now shows a caution first.
- Targets: Add Target is now available whether or not an instrument host has been added.
- Context Products: Fixed corrupted context identifiers being written into labels.
- Investigations: Fixed deleting an investigation redirecting to a near-empty page.
- Create a Bundle: Bundle name and alternate Bundle ID now accept longer names.

> ### **Version 1.37.5 (July 24, 2026)**
- Archive Bundle: Bundle UI is up to date with the External Bundle UI.
- Alias: Fixed issue with created Aliases not being deleted from XML files after being updated.
- Targets: Fixed issue with targets redirecting to Document Collections on the bundle view.
- Targets: Fixed error when deleting targets.

> ### **Version 1.37.4 (July 23, 2026)**
- External Bundle: Fixed a bug related to XML not populating from uploaded netCDF files.
  
> ### **Version 1.37.3 (July 17, 2026)**
- ELSA Assistant: Bug fixes and context improvements.

> ### **Version 1.37.2 (July 10, 2026)**
- ELSA Assistant: A new AI-powered ELSA Assistant has been introduced to help users get answers to PDS4-related questions directly from the website.
- Citation Information: The Editors section has been re-introduced.
- Citation Information: Improved UI for viewing authors and organizations.
- Citation Information and Alias: Fixed the issue with users being able to add multiple citations and aliases.
- Alias: Fixed the overlay issue with users not being able to delete existing aliases.
  
> ### **Version 1.37.0 (June 12, 2026)**
- External Bundle: Target selection page now includes a note clarifying that "Laboratory Analog" refers to model/simulation data, not a physical lab sample.

> ### **Version 1.36.0 (June 05, 2026)**
- External Bundle: Fixed an issue where uploaded NetCDF files were not generating XML labels.

> ### **Version 1.35.0 (May 29, 2026)**
- Bundle Hub: A kebab menu has been added to each bundle card for quick access to bundle actions.
- External Bundle: The "Add Data Products" button has been removed from model output collections.
- Security: Several security vulnerabilities have been identified and resolved.
- Archive: Minor bug fixes.

> ### **Version 1.34.0 (May 22, 2026)**
- Beta Feedback Form: A new feedback form has been introduced for users to provide feedback on ELSA.
- Citation Information: The layout has been updated with new text.
- Document Products: Updated form design as well as small bug fixes.
  
> ### **Version 1.33.0 (May 08, 2026)**
- About page: Updated educational text content.
- Create a bundle page: Added descriptive text next to radio card buttons for bundle type selection.

> ### **Version 1.32.0 (May 01, 2026)**
- Build Bundle: UI changes to bundle type selection (radio card button has been introduced).
- About: Fixed the broken link for the PDS4 Standard.
- netCDF Upload: The .nc extension check has been removed. Server-side magic byte validation has been introduced.
- netCDF XML Generation: Fixed bug to populate XML file with coordinate metadata and new variable metadata. 

> ### **Version 1.31.0 (April 24, 2026)**
- Bundle Hub: Introduced a vertical scroller on the hub.
  
> ### **Version 1.30.0 (April 17, 2026)**
- Bundle Hub: Replaced the carousel with a responsive bundle grid.
- Bundle Hub: Added search and bundle type filtering.
- Bundle Hub: Bundle cards now show the last updated date.
- Bundle Hub: External bundle cards now show a status badge.
  
> ### **Version 1.29.0 (March 27, 2026)**
- Delete NetCDF: Fixed an issue where the XML file was not deleted when removing a NetCDF file.
- Delete Document: Fixed an issue where the XML file was not deleted when removing a document.
- NetCDF File Upload: Updated the status bar to display "Processing NetCDF..." while the server processes the file after upload.
- External Bundle: Implemented the Submit functionality, which sends an email with the bundle archive path and a download link.

> ### **Version 1.28.0 (March 20, 2026)**
- External Bundle: A real-time progress bar has been added for NetCDF file uploads.
- Archive Bundle: Now displays the proper collection type options when creating a new collection.
- Archive Bundle: Fixed the issue with document product XML files not having a name and overwriting each other when a new one is made.
- External and Archive Bundles: Documents and collections now use the alternate BundleID, if provided.
- External and Archive Bundles: Updated format for XML file names in the file viewer.

> ### **Version 1.27.0 (March 13, 2026)**
- External Bundle Viewer: Vertical scrolling has been added to the XML viewer.
- External Bundle: Delete feature implemented for uploaded NetCDF files.
- External Bundle: Bundle Progress card now includes a Submit button that activates when all required sections are complete.
- Document Product: Collapsible card fields have been updated to display accurate information.
  
> ### **Version 1.26.0 (March 06, 2026)**
- Archive Bundle: Implemented deletion of additional collections.
- About Page: Automated the Release Notes section to pull directly from the GitHub README.
  
> ### **Version 1.25.0 (February 27, 2026)**
- Archive Bundle View: Fixed issue with the "Add Document Products" button not working.
- Product Document Editor: Fixed issue with users not being able to edit document products.
- Product Document Editor: Correct document product forms for archive and external bundles have been added to the editor, as well as an updated color scheme.
- External Bundle: Implemented the deletion of the additional collections feature.
  
> ### **Version 1.24.0 (February 13, 2026)**
- External Bundle View: The document product list in the collections card has been updated to a collapsible list format.
- External Bundle Walkthrough: Updated text and fixed bundle flow when adding citation information.
- External Bundle View: Document Products now use the correct form for external bundles.
- 
> ### **Version 1.23.0 (February 06, 2026)**
- External Bundle: The NetCDF file upload section has been updated by combining two cards into one.
- NetCDF Uploaded Files: A scroller has been introduced, and the number of files is now displayed.

> ### **Version 1.22.0 (January 30, 2026)**
- Bundle Hub UI: The Delete Multiple Files feature now includes a confirmation modal, and an auto-vanishing success message has been introduced for deleted bundles.
- Archive Bundle UI: The Edit Collections button has been removed and relocated to an independent card under the Info card.
  
> ### **Version 1.21.0 (January 23, 2026)**
- External Bundle UI: The Edit Collections button has been removed and relocated to an independent card under the Info card.
- External Bundle UI: The Info card section has been updated, and contextual help text has been added to each card and section within the bundle.
- External Bundle UI: The AMA color scheme has been updated.
- External Bundles: Document Collections are now available for External Bundles.
- External Bundles: Document Products have an updated XML template to reflect AMA requirements.
- External Walkthrough: Document Collections have new fields to reflect the XML tags in AMA Document Products.
  
> ### **Version 1.16.0 - 1.20.0 (October 2025 - December 2025)**
- Various Bug Fixes.
- Quality of Life updates.

> ### **Version 1.15.0 (September 26, 2025)**
- About Page: The page has been redesigned and now includes a tab feature for different sections.
- Table Products: Fixed being able to access table and field pages for table products.
- Data Products: Reformatted the models of data products to be able to support more types in the future.
  
> ### **Version 1.14.0 (September 05, 2025)**
- Bundle Hub: The Select and delete feature has been introduced. Now, users can delete multiple bundles from the bundle hub.
- Bundle Hub: Separate images for Archive and External Bundles have been added to the carousel.
- Citation Information: Lookup ORCID and RORID are added to the detailed citation information page.
- Bundle ID: An alternate ID can be specified to override the default ID generated from the bundle name.
  
> ### **Version 1.9.8 (June 27, 2025)**
- Citation Information Form: Added separate tabs for authors and editors so it's easier to follow. Also, people and organizations are separated within the tabs.
- Context Products Update: Updates context product models in database for bidirectionality with related products and updated to have most
up-to-date product in registry.
- Context Products: Fixed the issue with the submit button for the contact form.
- Footer now properly sticks to the bottom of all pages.

> ### **Version 1.9.7 (June 20, 2025)**
- Review Form: The ELSA team now receives reviews in both DOCX and PDF file formats. Also, it sends the user a copy of their submission.
  
> ### **Version 1.9.6 (June 13, 2025)**
- Landing Page: Contact Us and Submit Review buttons are added.
- Review Form: Users are now able to save a draft until they submit it. It retains the input until the form is submitted and resets the form fields after submission.
- Bundle Progress Feature: Context products' status is also added to the vertical bar.
- Contact Page: Contact page has been repurposed to have a submittable form to contact an ATM node representative
- About Page: Cards for ELSA staff have been add to the bottom of the page along with an information link to a personal website

> ### **Version 1.9.5 (May 23, 2025)**
- New Walkthrough feature: Users now get a semi-guided step-by-step walkthrough as they create a new bundle.
- New Bundle Progress feature: Users can see what parts of the identification area are complete in the main bundle page.
- Data Table Products: Update binary and fixed-width table forms to include options for a header. Information also writes correctly into XML Files.
- Bundle Build Page: Updated introduction for better clarification and accuracy.
- Alias Page: Users are no longer required to enter both an Alternate ID and an Alternate Title.

> ### **Version 1.9.4 (May 16, 2025)**
- Add A Document Product form ordering fixes.
- Fixed Delimited Table Product to include header options, updated UI of form, and writing into XML file.

> ### **Version 1.9.3 (April 14, 2025)**
- Bundle_XML: Citation Information order is updated to reflect 1N00 version.
- Delete Citation Information: Fixed the error that used to happen when deleting multiple Citation Information.
- Edit Citation Information: A temporary solution prompts the user that the feature is being built.
- Edit Investigation Area: Pull-down menu is introduced before the modals pop-up.
- Citation Information Form: UI is updated to the ELSA standard.
- Table Creation: Updated design of table creation form for Delimited, Binary, and Character tables.
- Table Delimited Header: Added form options for a header for Table Delimited and the creation and writing into the XML labels.
- Bug Fixes: Fixed the bug of not being able to write into table XML files as they are not found. 

> ### **Version 1.9.2 (February 14, 2025)**
- Delete features added for host products, instruments, and targets.
- Status update feature enhanced for host products, instruments, and targets: The user will be prompted now if nothing is selected.
- Updated the context product crawler, update_context.py, to be more concise when adding context products to the database. It adds the reference links between investigations to facilities and facilities to telescopes. This includes updates on using the CTLI library for newer context products.
  
> ### **Version 1.9.1 (February 07, 2025)**
- Resolved vertical spacing inconsistencies between the header and footer for a more uniform layout.
- Enhanced the Instrument and Target selection pages to always display the selected items, eliminating excessive whitespace. Previously, these pages only showed the selection dropdown on load, leading to layout gaps.
  
> ### **Version 1.9 (January 24, 2025)**
- Added ELSA version display in the footer.
- Updated the copyright year to reflect 2025.


