<!-- watches: build/models.py#Modification_History, build/views.py#modification_history, build/views.py#delete_modification_history, build/forms.py#ModificationHistoryForm, templates/build/modification_history -->
<!-- fingerprint:
     build/models.py#Modification_History       = 0545a217dd04
     build/views.py#modification_history        = 23c81c748c43
     build/views.py#delete_modification_history = 7601dc6d4ebe
     build/forms.py#ModificationHistoryForm     = 47264907c620
     templates/build/modification_history       = e6020ba6a6d7
-->
<!-- reviewed: 2026-08-28 -->
<!-- baseline: d8df4b96320a4636b7a95ac426bec7ff1a1fa839 -->
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

In ELSA: Modification History is one of the three required components before an
External bundle can be submitted. For a new bundle, the first entry typically has
version 1.0 with a description like "Initial creation of the bundle". Users add
further entries when they revise a previously archived bundle.
