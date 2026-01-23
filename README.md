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
> ### **Version 1.21.0 (January 23, 2025)**
- External Bundle UI: The Edit Collections button has been removed and relocated to an independent card under the Info card.
- External Bundle UI: The Info card section has been updated, and contextual help text has been added to each card and section within the bundle.
- External Bundle UI: The AMA color scheme has been updated.
- External Bundles: Document Collections are now available for External Bundles.
- External Bundles: Document Products have an updated XML template to reflect AMA requirements.
- External Walkthrough: Document Collections have new fields to reflect the XML tags in AMA Document Products.

### **Previous Versions** 
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


