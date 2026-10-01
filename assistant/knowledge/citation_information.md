<!-- watches: build/models.py#Citation_Information, build/views.py#citation_information, build/views.py#edit_citation_information, build/views.py#delete_citation_information, build/forms.py#CitationInformationForm, build/forms.py#EditCitationInformationForm, templates/build/citation_information -->
<!-- fingerprint:
     build/models.py#Citation_Information       = 9455d0d634ca
     build/views.py#citation_information        = 824a33253675
     build/views.py#edit_citation_information   = 589c768f0597
     build/views.py#delete_citation_information = 42b60db2375b
     build/forms.py#CitationInformationForm     = 5f7964a89b9a
     build/forms.py#EditCitationInformationForm = a398ac165e27
     templates/build/citation_information       = b26336432488
-->
<!-- reviewed: 2026-09-25 -->
<!-- baseline: ab6b3482d9893d5e8b7e506fadff1e8125905f6a -->
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

In ELSA: the Citation Information form collects the author count, publication
year, description, and optional keywords for a bundle; author names are entered
on the next page. Two editors are always included automatically: PDS Atmospheres
node staff Lynn Neakrase and Lyle Huber (NASA PDS Atmospheres Node, New Mexico
State University), recorded with their ORCIDs; these two are fixed and cannot be
changed. Users can optionally add MORE editors of their own: enter how many
additional editor persons/organizations on the citation form, then fill in
their names on the citation details page (Editors tab). Citation Information is
required before a bundle can be submitted.

An External bundle's citation needs at least one author, a person or an
organization. PDS4 lets a citation leave authors out, so the validation tool does
not report it, but a citation with no one to credit cannot be cited or given a
DOI. The author count is set only when the citation is created (the details page
fills in names but cannot change how many there are), so for an External bundle
the form refuses a count of zero people and zero organizations and says why. A
citation that already has no authors has to be deleted and created again with at
least one.

A bundle holds one Citation Information section (PDS4 allows exactly one per
bundle). Once it exists, the modal shows the current citation with edit and
delete actions instead of the create form.

Where it is stored: the counts, publication year, description and keyword are
columns on the Citation_Information row, but the author and editor names are
held only in the XML labels. ELSA copies the citation into the bundle label,
into every collection label (its own and any the user added), and into every
data product label, so all of them agree. The bundle label is the source of
truth: adding, editing or deleting a citation mirrors it outward from there.

Author and editor names must be plain unaccented Latin letters. PDS4 holds them
in ASCII_* types, every one of which is restricted to Basic Latin, so "Raul" and
"Morales-Juberias" are refused however correctly they are spelled. The citation
form says so and suggests the unaccented spelling; this is a limitation of the
archive format rather than a judgement about the name. Descriptions are not
restricted this way and accept degrees, microns and the like.

If someone reports that an author "did not save", the form almost certainly did
submit. What used to happen is that only some labels were updated, and PDS
reported the blank in one of the others, which reads as the citation being
rejected. The thing to check is whether every label agrees, not whether the form
worked.
