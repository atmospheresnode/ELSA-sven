# Citation Information

PDS4 Information Model (v1.24 / 1O00): the Citation_Information class provides the
fields used when citing the product in journal articles, abstract services, and
other reference contexts. It is an optional component of the Identification_Area
of a product label.

Attributes (per the PDS4 IM):
- author_list [0..1], semicolon-separated list of people to cite as authors.
  Format for each name: SURNAME, GIVEN NAME(S); initials may be used in place of
  given names (e.g. "Smith, Jane A.; Doe, John").
- editor_list [0..1], same format, for editors of the product.
- publication_year [1..1], REQUIRED. The year the product should be considered
  published; generally the year the data were declared Certified or Archived.
- doi [0..1], Digital Object Identifier assigned by a DOI registration agency.
- keyword [0..*], free-form keywords describing the product.
- description [1..1], REQUIRED. A terse description used during citation.
- Funding_Acknowledgement [0..*], cites a funding source (attributes:
  funding_source required, funding_year, funding_award,
  funding_acknowledgement_text required).
- List_Author / List_Editor [0..*], structured alternatives to the plain-text
  lists. Each contains Person entries (given_name and family_name required,
  person_orcid optional, plus optional Affiliation with organization_name) and/or
  Organization entries (organization_name required, organization_rorid optional).

In ELSA: the Citation Information form collects the author list, publication year,
description, and optional keywords for a bundle. The editors are added
automatically by ELSA, PDS Atmospheres node staff (Lynn Neakrase and Lyle Huber,
NASA PDS Atmospheres Node, New Mexico State University) are recorded as editors
with their ORCIDs, so users do not enter editors themselves. Citation Information
is one of the three required components before an External bundle can be
submitted.
