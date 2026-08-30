<!-- watches: build/views.py#build, templates/build/collections, templates/build/context -->
<!-- fingerprint:
     build/views.py#build        = 91b23b4f25cb
     templates/build/collections = bfe57a159521
     templates/build/context     = a8559e40f4ac
-->
<!-- reviewed: 2026-08-28 -->
<!-- baseline: d8df4b96320a4636b7a95ac426bec7ff1a1fa839 -->
# Archive Bundle Workflow

Archive bundles are ELSA's full PDS4 archive bundles, the data itself is
stored in the archive (unlike External/AMA bundles, where data is hosted
elsewhere). The Archive workflow has more steps than the External one:

- **Context products**: associate the bundle with an Investigation (mission,
  field campaign, observing campaign, or individual investigation), then the
  related Instrument Hosts (spacecraft, earth base), Instruments, Facilities,
  Telescopes, and Targets. ELSA's context search pares the lists down, picking
  an investigation filters instruments and targets to the ones that go with it.
  Each selected context product can also be removed again from the bundle page.
- **Identification metadata**: Alias (optional), Modification History, and
  Citation Information, same as External bundles.
- **Collections**: the bundle contains a data collection (upload science data
  files; ELSA generates PDS4 labels), a document collection (upload user
  guides and descriptions), and additional collections as needed. Collection
  descriptions can be edited.
- **Data preparation**: the data prep step helps organize files before upload.

The bundle page for an Archive bundle shows these sections with progress
tracking, and the same Build -> Review -> Submit flow applies. When unsure
which type to pick: models and externally hosted datasets are usually
External (AMA); observational data delivered to the PDS is usually Archive.
