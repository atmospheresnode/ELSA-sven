# Label upgrades

Labels are files on disk, so a fix to how ELSA writes them only reaches new bundles. Label
migrations carry the fix to bundles that already exist.

## How it works

- `build/label_migrations/NNNN_name.py` are numbered fixes, applied once per bundle, in order.
- Each bundle remembers the last one it had (`Bundle.label_revision`). New bundles start at the
  newest, since today's code already writes correct labels.
- A bundle is upgraded when its owner opens it, before each validation check, when it's
  submitted, and by the nightly `upgrade_bundles --apply`.
- Before each migration the bundle's labels and inventory tables are copied to
  `LABEL_UPGRADE_DIR` (default `label_upgrades/`, kept 90 days). If the migration fails or leaves a
  label that won't parse, the copy is put back, the bundle stays where it was and staff get one
  email.
- A bundle whose update failed can't be submitted until it goes through (`build/preflight.py`,
  `label-update-failed`). The owner sees a warning saying the team has been told. Staff find these
  bundles with the "Label update failed" filter on the Admin Console Bundles page.
- Every change is recorded (`BundleUpgrade`). The owner sees a notice once and can always open
  "Changes made by ELSA" on the bundle page, where each file opens in the File Viewer. Changes
  made while they weren't looking also go under the bell, and seeing them on the page clears it.
- Submitted bundles are left alone until the owner submits again or staff press "Update labels
  now" on the Admin Console bundle page. Staff can preview the change first, and undo the newest
  change there too.

## When you change how a label is written

1. Fix the code that writes the label, so new bundles are right.
2. Add `build/label_migrations/NNNN_short_name.py` with the next number:

   ```python
   title = 'What changed, in words a user understands'
   bundle_types = ('External',)   # optional, leave out for both

   def apply(bundle):
       # Fix this bundle's labels. Safe to run twice. Labels and inventory tables only.
       # Raise if it can't finish. Return short notes, e.g. ['Time_Coordinates'].
       return []
   ```

   Owners read the notes, so give each new kind of note plain words in
   `label_upgrades.PLAIN_NOTES`. An element name like `Time_Coordinates` is shown as an empty
   field that was removed.

3. Run the label tests. `build.test_label_snapshots` fails on purpose when the labels ELSA
   writes change, and tells you to refresh the snapshot once the migration is in.
   `build.test_label_upgrade_e2e` checks the migrations change nothing in a bundle written
   today and that an aged bundle validates like a new one.

## Validation results

A check is out of date when the bundle's files change or when ELSA's checks do: the validate
release, the schema catalog, `validate_report.py`, `validate_rules.py`, `label_repair.py`, the
validate command line, or a new label migration (`build/checker.py`). An out of date pass doesn't
let a bundle through the submission gate, and the bundle page runs the check again by itself.

## Deploying

1. `python3 manage.py migrate` (build 0082 to 0084, staff 0012). Every existing bundle starts at
   revision 0 and every existing validation result counts as out of date once. 0084 lets the new
   tables hold names latin1 can't, like Dvořák.
2. Make `LABEL_UPGRADE_DIR` (default `label_upgrades/` in the checkout) writable by `apache`,
   the same way `validation/` is. If it isn't, page views can't take the lock and nothing upgrades
   by itself.
3. `python3 manage.py upgrade_bundles` to see what's pending, `--preview` on a bundle or two, then
   `--apply` as `apache`.
4. Add the nightly cron line below.

## Commands

```
python3 manage.py upgrade_bundles                          # what's pending, writes nothing
python3 manage.py upgrade_bundles --bundle 836 --preview   # the diff, on a copy
python3 manage.py upgrade_bundles --apply                  # optional after a deploy, the cron does it
python3 manage.py upgrade_bundles --apply --bundle 836 --include-submitted
python3 manage.py upgrade_bundles --undo 42
```

Run it as the user the web server runs as (`apache` on atmos). The labels belong to that user,
and so do the lock and snapshot files page views use. Nightly, in apache's crontab, capped so it
doesn't hog the shared server:

```
30 2 * * * cd /export/atmos1/htdocs/elsa && mkdir -p label_upgrades && /usr/local/venvs/ELSA_3.11/bin/python manage.py upgrade_bundles --apply --max-minutes 30 >> label_upgrades/sweep.log 2>&1
```
