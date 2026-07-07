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
