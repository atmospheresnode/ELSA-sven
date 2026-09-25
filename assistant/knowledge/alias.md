<!-- watches: build/models.py#Alias, build/views.py#alias, build/views.py#alias_edit, build/views.py#alias_delete, build/forms.py#AliasForm, templates/build/alias -->
<!-- fingerprint:
     build/models.py#Alias       = a70503dab702
     build/views.py#alias        = 394473fc18c1
     build/views.py#alias_edit   = bd3dbf12eb04
     build/views.py#alias_delete = 72d8092f7a75
     build/forms.py#AliasForm    = d4ac348db2ea
     templates/build/alias       = 3d835782d8f8
-->
<!-- reviewed: 2026-09-17 -->
<!-- baseline: ab6b3482d9893d5e8b7e506fadff1e8125905f6a -->
# Alias and Alias List

PDS4 Information Model (v1.24 / 1O00): the Alias class provides a single
alternate name and identification for the product, as used in this or some other
archive or data system. Aliases live in the Alias_List of a product's
Identification_Area.

Attributes of Alias (all optional):
- alternate_id [0..1], an additional identifier used in other archives/systems.
- alternate_title [0..1], an alternate name/title for the product.
- comment [0..1], explanation of the alias, e.g. which system uses it.

In ELSA: the Alias is optional for a bundle, it does NOT block submission. It is
still recommended when the dataset is known by another name elsewhere (for
example, a model run name used in a publication, or an identifier from another
archive). On the bundle page the Alias row shows a yellow "Optional" badge when
empty rather than a red "Missing" badge. Bundle ID (the LID / logical_identifier)
is different from an Alias: the Bundle ID is the unique, permanent PDS4
identifier auto-generated from the bundle name, while an Alias is an optional
extra name recorded for cross-reference.

A bundle holds one alias in ELSA. The Alias modal shows the create form only
while no alias exists; once one is saved, the modal shows the current alias
with edit and delete actions instead, and asks you to edit or delete the
existing alias rather than create another one.
