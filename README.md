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
> ### **Version 1.9.5 (May 23, 2025)**
- New Walkthrough feature: Users now get a semi-guided step-by-step walkthrough as they create a new bundle.
- New Bundle Progress feature: Users can see what parts of the identification area are complete in the main bundle page.
- 


### **Previous Versions** 

> ### **Version 1.9.4 (May 16, 2025)**
- Add A Document Product form ordering fixes.
- 

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


