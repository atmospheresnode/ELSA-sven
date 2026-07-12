<!-- watches: build/views.py, build/models.py -->
<!-- reviewed: 2026-07-10 -->
# Targets and Context Products

PDS4 Information Model (v1.24 / 1O00): the Target_Identification class provides
detailed target identification information in a product's Context_Area.
Attributes: name [1..1] (required), type [1..*] (required, e.g. Planet,
Satellite, Comet), alternate_designation [0..*], description [0..1], and an
optional Internal_Reference pointing at the target's context product
(lid_reference + reference_type required).

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
