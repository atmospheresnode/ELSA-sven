# PDS validation: what "done" means

The checklist this feature is held to. Each item names how it is verified, so a
claim that something works can be checked rather than believed.

Two kinds of verification appear here:

- **unit** — a normal test, fast, run by `manage.py test build`.
- **corpus** — a bundle built through the real views, written to disk and handed to
  the real NASA validator, in `build/test_corpus_e2e.py`. Roughly two minutes.
  Nothing about label generation should be believed without one.

---

## 1. Labels ELSA writes are valid

The bar: a bundle built today, by any path a user can take, produces **no findings
attributable to ELSA**. Not "hidden from the user" — none.

| | how |
|---|---|
| Archive bundle, nothing done yet | corpus `archive bare` |
| Archive bundle, citation filled in | corpus `archive with citation` |
| Archive bundle with a document | corpus `archive complete` |
| External (AMA) bundle, nothing done | corpus `external bare` |
| External with a user-added collection | corpus `external with collection` |
| External with a document | corpus `external complete` |
| A target selected | corpus `external with target` |
| Real NetCDF model output uploaded | corpus `ama with data` |
| AMA dictionary content filled in | corpus `ama with data and metadata` |
| Several documents in one collection | corpus `many documents`, `two documents` |
| PDS4 version stamped correctly | unit `test_im_version` |
| Context names match what PDS publishes | unit `ContextTitleTests`, corpus |
| Target types and reference types | unit `TargetLabelTests` |
| Context_Area children in schema order | unit `ContextAreaOrderTests` |
| No empty optional element is ever written | corpus (gate 1) |

## 2. Input a real user actually produces

| | how |
|---|---|
| Accented author names | corpus `an accented author name`, unit `AccentedNameTests` |
| Unicode in a description | corpus `unicode in the description` |
| XML metacharacters (`&`, `<`, `"`, `'`) | corpus `an ampersand in the description` |
| A long bundle name | corpus `a long bundle name` |
| Invisible characters in a filename | corpus `netcdf with an invisible character` |
| A file name with no extension | corpus `document named without an extension` |
| A file name ELSA cannot repair | corpus `document with a space in its file name` |
| Two documents sharing a name | unit `DuplicateDocumentNameTests` |

## 3. What reaches the user

The bar: **everything a scientist reads either tells them what to do, or says
plainly that there is nothing to do.** Nothing in between.

| | how |
|---|---|
| Nothing reaches them unmapped | corpus (gate 2) |
| They are told exactly what they left undone | corpus (gate 3) |
| Every user-facing item has a destination | unit `EveryItemHasSomewhereToGoTests` |
| Every user-visible rule gives direction | unit `EveryUserVisibleRuleGivesDirection` |
| Advisories say they do not block | unit, same class |
| No raw PDS jargon in the translated tab | unit `test_a_raw_pds_error_code_never_reaches_the_translated_tab` |
| ELSA's own defects are never shown | unit `ContextProductsAreNotTheUsersDoing` |
| One problem reads as one item | unit `CollapsingTests` |
| Repeated items name which file | unit `BadNameFindingsTests` |

## 4. The raw output tab

| | how |
|---|---|
| Grouped, so one problem is one entry | unit `RawOutputGroupingTests` |
| Nothing PDS said is dropped | unit, same class |
| Each entry says what it became | unit, same class |
| A 3KB schema message does not fill the tab | unit `test_a_three_kilobyte_message_is_folded` |

## 5. When checks run

| | how |
|---|---|
| A change to the bundle triggers a re-check | unit `test_auto_recheck_e2e` |
| Opening the page repeatedly does not | unit, same file |
| It settles rather than repeating | unit, same file |
| The manual button always works | unit, same file |
| Results refresh without a page reload | unit `PanelRefreshesItselfTests` |
| A reload never aborts an upload | unit `test_the_panel_fetches_instead_of_reloading` |

## 6. Failure and abuse

| | how |
|---|---|
| validate missing or misconfigured | unit `test_validation_robustness` |
| A run that hangs | unit, same file |
| A crashed run does not wedge a bundle | unit, same file |
| A malformed or truncated report | unit, same file |
| Host at capacity | unit `test_validation_tiers` |
| Another user cannot read or start a run | unit `test_validation_views` |
| Staff-only views are staff-only | unit, same file |

## 7. Submission

| | how |
|---|---|
| The server refuses, not just the button | unit `test_submission_gate` |
| Staff are never blocked | unit, same file |
| A check that could not run never blocks | unit, same file |
| The node is told the verdict | unit `test_submission_email` |

## 8. Repairing what is already on disk

| | how |
|---|---|
| Old labels are brought up to current output | unit `test_label_repair` |
| Author names are never destroyed | unit, same file |
| Required elements are never dropped | unit, same file |
| It is idempotent | unit, same file |
| It reports before it writes | `manage.py repair_labels` defaults to report-only |

---

## Known gaps

Recorded rather than hidden.

- **ELSA cannot attach a file to a document.** Neither document form carries a
  `FileField` and no template offers a file input, so a document can be declared and
  its file name recorded while the file itself never exists. Every bundle containing
  a document therefore names a file that is not there, and PDS rejects it. The
  corpus records this as `document-file-missing` in `KNOWN_GAPS` rather than
  failing on it, because closing it is a feature and not a fix. It is counted
  against ELSA and reported to the node, never shown to the submitter as something
  to do, since there is nothing they can do.

- **Content validation is now on by default** and has been timed: 6s against 7s on
  a 184MB NetCDF bundle, within noise on both Archive bundles. It was skipped on the
  assumption that reading data files was expensive; it is not, because an AMA data
  product only references its file. `VALIDATE_SKIP_CONTENT` turns it off for a
  future bundle with large tables, where reading every field really would cost
  something.
- **The AMA LDD is pinned to one version.** A new AMA dictionary is not covered.
- **The corpus needs a NetCDF fixture** for two shapes; they are skipped without it.
- **Schema drift** between a live database and the models cannot be caught by any
  test, since the test database is built from migrations. `manage.py
  check_schema_drift` covers it, and belongs in the deploy checklist.
