<!-- watches: build/models.py#Target, build/models.py#Investigation, build/models.py#Instrument, build/models.py#Instrument_Host, build/models.py#Facility, build/models.py#Telescope, build/views.py#context_search*, build/views.py#delete_target, build/views.py#delete_investigation, templates/build/context -->
<!-- fingerprint:
     build/models.py#Target              = 4d5a735a2b40
     build/models.py#Investigation       = 6346b098b3f5
     build/models.py#Instrument          = 5f4031a2ca33
     build/models.py#Instrument_Host     = 803a80c9e890
     build/models.py#Facility            = 9125217e7a35
     build/models.py#Telescope           = 8517fb364fcf
     build/views.py#context_search*      = eb3e9e767306
     build/views.py#delete_target        = 266667f71ce9
     build/views.py#delete_investigation = e0c9e97bba48
     templates/build/context             = a8559e40f4ac
-->
<!-- reviewed: 2026-09-11 -->
<!-- baseline: 66e63c0801aa5cb2a7e96e0f79d946a40832d093 -->
# Targets and Context Products

PDS4 Information Model (v1.24 / 1O00): the Target_Identification class provides
detailed target identification information in a product's Context_Area.
Attributes: name [1..1] (required), type [1..*] (required, e.g. Planet,
Satellite, Comet), alternate_designation [0..*], description [0..1], and an
optional Internal_Reference pointing at the target's context product
(lid_reference + reference_type required).

type is a closed list and PDS is case-sensitive about it: 'Asteroid', not
'ASTEROID'. The PDS registry that ELSA crawls stores most of them upper case, so
ELSA converts to the PDS spelling when it writes the label. Users pick a target
from a list and never type its type, so a complaint from PDS about a target type
is never something the user can fix by choosing differently.

reference_type on a Target_Identification depends on what is referring to it:
bundle_to_target from a Product_Bundle, collection_to_target from a
Product_Collection, data_to_target from a Product_Observational, and
document_to_target from a Product_Document.

Context products are PDS4 products that describe the "context" of an
observation: investigations (missions, field campaigns, observing campaigns),
instruments, instrument hosts (spacecraft, telescopes, earth bases), targets
(planetary bodies), facilities, and telescopes. Data products reference context
products by LID rather than repeating their details.

In ELSA: choosing at least one Target is one of the three required components
before an External bundle can be submitted. Targets are picked from ELSA's
context product list (e.g. Mars, Titan, Jupiter) via the Targets section of the
bundle page. Investigations, instruments, and hosts can also be associated with
a bundle but are not required for External bundles.
