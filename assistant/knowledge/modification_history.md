<!-- watches: build/models.py#Modification_History, build/views.py#modification_history, build/views.py#delete_modification_history, build/forms.py#ModificationHistoryForm, templates/build/modification_history -->
<!-- fingerprint:
     build/models.py#Modification_History       = db2b0ba6a4fc
     build/views.py#modification_history        = 3a4a5acdf3d6
     build/views.py#delete_modification_history = b86b8beff98a
     build/forms.py#ModificationHistoryForm     = 47264907c620
     templates/build/modification_history       = e6020ba6a6d7
-->
<!-- reviewed: 2026-09-17 -->
<!-- baseline: ab6b3482d9893d5e8b7e506fadff1e8125905f6a -->
# Modification History

PDS4 Information Model (v1.24 / 1O00): the Modification_History class tracks the
history of changes made to the product once it enters the registry system. It
lives in the Identification_Area and contains one or more Modification_Detail
entries. The first, required entry documents the date the product was first
registered.

Attributes of Modification_Detail (all required):
- modification_date [1..1], the date of that round of modification.
- version_id [1..1], the product version this modification produced (e.g. 1.0).
- description [1..1], what changed in this round.

In ELSA: Modification History is required before a bundle can be submitted, and
ELSA writes the first entry itself when a bundle is created: version 1.0, the
creation date, and the description "Bundle creation". Nobody needs to add it, and
it is not a step of the guided walkthrough. It can be changed in the Modification
History window on the bundle page (for example to set the delivery date). Users
add further entries only when they deliver a revised version of a bundle that was
already archived, not for each edit made in ELSA.
