# Running PDS validation on a deployed host

What has to exist on a server before ELSA can validate a bundle. Nothing here is
user-facing yet: Phase 1 runs validate, stores the results and shows them to staff.
Data providers see nothing until the translation layer exists.

## 1. Java 25

validate 5.0.0 is compiled to class file version 69, so any older Java fails at
startup:

    UnsupportedClassVersionError: ... class file version 69.0, this version of the
    Java Runtime only recognizes class file versions up to 61.0

which names neither Java nor a version anyone recognises (61 is Java 17, 69 is Java
25). On the current host Temurin 25 is already installed alongside 8, 11 and 17:

    /usr/lib/jvm/java-25-temurin-jdk

The system default stays whatever it is; ELSA points at 25 explicitly through
`VALIDATE_JAVA_HOME`, so nothing else on the machine is affected.

validate 4.2.0 and earlier ran on Java 17. Changing `VALIDATE_HOME` to 5.0.0 without
changing `VALIDATE_JAVA_HOME` gives the error above: every check fails as "could not
run" (which never blocks a submission), so change the two together.

## 2. The validate tool

Installed outside the repository, so ~75MB of Java binaries stays out of git history.
On atmos the team keeps every release under `/home/atmos4/PDS/` (a link to
`/export/atmos4/PDS/`), and ELSA uses `/home/atmos4/PDS/validate-5.0.0` from there.
The shared copy matches the GitHub release, apart from one commented-out line added to
`bin/validate`. Every directory on that path is world-readable, which apache needs.
On a host without that folder:

    cd /opt
    curl -sL https://github.com/NASA-PDS/validate/releases/download/v5.0.0/validate-5.0.0-bin.tar.gz | tar xz
    chmod +x /opt/validate-5.0.0/bin/validate

Check it before going further. `--version` exercises the Java path without touching
any bundle:

    JAVA_HOME=/usr/lib/jvm/java-25-temurin-jdk /home/atmos4/PDS/validate-5.0.0/bin/validate --version

From 5.0.0, validate exits non-zero when it finds no products at all. ELSA does not
read the exit code (see "How it runs"), so this changes nothing here.

Each run records the release named in its own report (`ValidationRun.tool_version`,
migration 0081), and the PDS Validation window tells the bundle's owner which release
checked it ("Last checked ... by NASA PDS Validate 5.0.0"). Moving a host to a newer
release therefore needs no code change: the next check names the new one, and older
results keep naming the release that produced them.

## 3. Settings

**The whole `elsa/` directory is gitignored** (`.gitignore` has `/elsa/*`), so
`settings.py` and `secrets.py` do not travel with the code. Everything in this
section has to be applied by hand on each host; pulling the branch will not bring it.

The code tolerates a host where this has not been done yet: every setting is read
with a default, so ELSA imports and runs and simply reports that validation is not
configured. It will not crash on a settings file that predates this work.

In `elsa/secrets.py`, per host:

    VALIDATE_HOME = '/home/atmos4/PDS/validate-5.0.0'
    VALIDATE_JAVA_HOME = '/usr/lib/jvm/java-25-temurin-jdk'

Optionally in `elsa/settings.py`, if the defaults do not suit; add after the other
path definitions near `TEMPORARY_DIR`:

    VALIDATE_WORK_DIR = os.path.join(BASE_DIR, 'validation')
    VALIDATE_TIMEOUT_SECONDS = 3600

`VALIDATE_HOME` is the release directory itself, the one containing `bin/validate`,
not its parent. That is the mistake worth guarding against, and the error message
says so if you get it wrong.

The optional settings and what they do:

- `VALIDATE_WORK_DIR` — where the schema cache and reports live. Defaults to
  `validation/` in the project root, which is gitignored. Everything in it is
  regenerable, so it does not need backing up.
- `VALIDATE_TIMEOUT_SECONDS` — how long one run may take before it is treated as
  hung. Defaults to an hour, deliberately generous because content validation
  against a large NetCDF bundle has not been timed against real data yet.
- `VALIDATE_JAVA_MAX_HEAP`: heap ceiling for each validate JVM, default `'2g'`.
  Applied through `_JAVA_OPTIONS`, not `JAVA_OPTS`: `bin/validate` ignores
  `JAVA_OPTS` and hard-codes `-Xms2048m -Xmx4096m`, and `_JAVA_OPTIONS` is the only
  thing the JVM applies after its command line. The floor is lowered to 64MB
  along with it, because a ceiling below the launcher's 2GB floor stops Java
  starting at all. Each run logs a harmless "Picked up _JAVA_OPTIONS" line.
- `VALIDATE_AUTO_CHECK_DEBOUNCE_SECONDS` — how long after a check finishes before
  the page may start another by itself. Defaults to 0, meaning a change is acted
  on immediately. It is a safety valve and should stay at 0 unless a host has a
  reason: automatic checks are already self-limiting, because a run makes the
  result match the files and an unchanged bundle then starts nothing.

`VALIDATE_AUTO_CHECK_COOLDOWN_SECONDS` is obsolete and no longer read. Remove it
from any settings file that still has it. It existed because staleness was
measured against `Bundle.updated_at`, which never moved (nothing that edits a
bundle's metadata calls `Bundle.save()`), so without a long cooldown every page
load would have started a run. Staleness is now measured against the bundle's
files, so the cooldown's only remaining effect was to make someone who had just
fixed something wait five minutes to see the panel agree.

## 4. Cache the schemas

    python3 manage.py build_schema_catalog

Downloads the schemas and schematrons ELSA's labels reference and writes an OASIS
catalog. Without it validate fetches every schema from pds.nasa.gov on each run,
which puts the PDS website in the path of every submission.

Roughly 6MB and about 22 files. Re-run it after changing `VERSION_CHOICES`, since the
list of schemas is derived from it, and use `--force` to refresh cached copies.

The command still succeeds when individual schemas cannot be fetched: a version
nobody uses may simply not be published. It fails only when nothing could be cached
at all, which means the host cannot reach pds.nasa.gov.

## 5. Check it end to end

    python3 manage.py shell -c "
    from build.models import Bundle
    from build.validate_runner import start
    print(start(Bundle.objects.first()).pk)"

Then watch it at `/elsa/build/validation/runs/` — staff only. A run against a small
bundle takes about five seconds.

## 6. Repair labels written before the fix

    python3 manage.py rebuild_netcdf_labels            # report only
    python3 manage.py rebuild_netcdf_labels --apply    # rewrite them

NetCDF labels written before 2026-09-11 identify their product as belonging to
"sample_bundle" rather than to the bundle they are in, so PDS reports the product
as a missing bundle member. At the time of writing that is 19 labels across 8
bundles in production, belonging to five users.

Those labels predate the rest of the generator fixes too, so rebuilding also gives
them their schematron reference, drops the empty containers, and corrects the
information model version. Reporting is the default and writes nothing: these are
archived files, and some belong to bundles already submitted for review.

Worth doing before validation is switched on for users, or their first check will
report something they cannot fix.

## 7. Following new releases

NASA publishes validate releases a few times a year. ELSA finds each one, checks it,
and tells you; a person decides when to switch.

**How a host follows releases.** Instead of naming a versioned directory, a host's
`VALIDATE_HOME` names a link that ELSA manages, and Java is chosen per release:

    VALIDATE_HOME = '<VALIDATE_WORK_DIR>/validate-current'
    VALIDATE_JAVA_HOME = 'auto'

`'auto'` reads the Java version a release needs from its own jar and uses the oldest
installed Java (under `/usr/lib/jvm`) that can run it. That is what makes a release
needing a newer Java, as 5.0.0 did, a switch rather than two settings changed in step.
If no suitable Java is installed, checks fail as "could not run" with a reason naming
the Java needed, and never block a submission.

Releases are unpacked under `VALIDATE_WORK_DIR/releases/` (`VALIDATE_RELEASES_DIR`),
one directory per version, about 90MB each; the downloaded archive is deleted once
unpacked. Nothing is ever unpacked over a release in use.

Old releases are removed after every `switch` and `rollback`, and when the monthly
check installs a new one. Kept: the release in use, the one before it (what
`rollback` returns to; `VALIDATE_RELEASES_KEEP_PREVIOUS` raises that), and a newer
release installed but not yet switched to. So at most three, about 300MB. Only
directories ELSA installed are touched, and nothing at all is removed when nothing
says which release is in use. `validate_release prune --dry-run` shows the decision.
Anything older is in the team's collection under `/home/atmos4/PDS/`.

**The command.**

    python3 manage.py validate_release status            # what runs here, what is out
    python3 manage.py validate_release install 5.1.0     # download + verify only
    python3 manage.py validate_release qualify 5.1.0     # compare with the one in use
    python3 manage.py validate_release switch 5.1.0      # the human step
    python3 manage.py validate_release rollback          # undo the last switch

`install` checks the download against the SHA-256 GitHub states for it and refuses a
release that has none. `switch` refuses a release that does not start (it runs
`--version` and validates an empty directory under the Java it will get), and one the
web server could not read; `--private` allows the latter on a development host whose
server runs as you. The link is replaced in one rename, so a check starting at that
moment gets one release or the other. Every switch is recorded in
`VALIDATE_WORK_DIR/upgrades/history.json`, which is what `rollback` reads.

**The monthly check.** `validate_release check` is meant for cron. When GitHub has a
stable release newer than the one in use, it installs and verifies it, makes sure a
suitable Java exists, then compares the two releases over every bundle under
`VALIDATE_QUALIFY_ROOTS` (read-only), and runs the validation test suites with each
where the host has them. Differences are judged on what a user would see: the
plain-language items, anything unmapped, and above all whether the submission gate
flips. The exit code is ignored, as ELSA ignores it. It then emails
`VALIDATE_UPGRADE_RECIPIENTS` once: "ready" when nothing changes, "needs a look" with a
bundle-by-bundle report when something does, or what is blocking it (a Java to
install, a bad download). It never switches anything. A blocked release is retried
every month and reported again only when it moves on. A month with nothing new costs
one request to GitHub and downloads nothing. Recipients are Rupak only for now.

`--trial --release 5.0.0 --baseline 4.2.0` sends a sample of the real email (subject
marked [Trial]) comparing two installed releases; like `--no-email`, it records
nothing and removes nothing, so it cannot stop a real report being sent later.

On atmos it runs from the development checkout, which can read production's bundles:

    # crontab -e, as rupakdey: 07:00 on the 1st of each month
    0 7 1 * * cd /home/rupakdey/elsa/ELSA-sven && nice -n 10 /home/rupakdey/elsa/Virtual_Env/bin/python manage.py validate_release check >> validation/upgrades/cron.log 2>&1

A full comparison of about 300 bundles takes around half an hour with two JVMs
(`VALIDATE_QUALIFY_WORKERS`, default `VALIDATE_MAX_CONCURRENT`), plus a few minutes
per release for the test suites.

**Moving a host onto the link, once.** On production, as sajomont:

    cd /export/atmos1/htdocs/elsa
    chmod o-w validation
    /usr/local/venvs/ELSA_3.11/bin/python manage.py validate_release switch 5.0.0 --install

`chmod o-w` comes first because `switch` refuses a link that every user could
replace: the web server runs whatever it points at. `validation/` was left
world-writable so apache could create `tmp/`; that exists now (owned by apache), and
`reports/`, the other directory apache writes, keeps its own permissions.

Then, still as sajomont, in `elsa/secrets.py`:

    VALIDATE_HOME = '/export/atmos1/htdocs/elsa/validation/validate-current'
    VALIDATE_JAVA_HOME = 'auto'

and `touch elsa/wsgi.py`. After that, a switch on production is the `switch` line
alone, and secrets.py never changes again.

## How it runs

A view creates a `ValidationRun` row and launches `manage.py run_validation <id>` as
a detached child, then returns immediately. The child updates its own row; the page
polls `validate/status/`.

Detached, in its own session, on purpose: under mod_wsgi a worker can be recycled
mid-request, and a thread doing the waiting would go with it. A validation started
just before a deploy still finishes and still records its result.

A second request for a bundle already being validated joins the run in flight rather
than starting a second JVM.

## When something goes wrong

Failures are written to `ValidationRun.failure_reason` and shown in the staff view,
so a run that could not start is distinguishable from one that ran and found errors.
The common ones:

| What you see | What it means |
|---|---|
| `VALIDATE_HOME is not set` | Settings missing; see step 3. |
| `No validate launcher at ...` | `VALIDATE_HOME` points at the parent, not the release directory. |
| `UnsupportedClassVersionError` in the log | `VALIDATE_JAVA_HOME` is older than the release needs: Java 25 for validate 5.0.0. |
| `exited ... without writing a report` | validate crashed. Run the command by hand to see its output. |
| Run stuck at Running | The child died without recording anything. `run_validation <id>` re-runs it in the foreground. |

A non-zero exit from validate is normal: finding errors is what the tool is for. Only
a missing report counts as the run itself having failed.

## What is deliberately not here yet

No user-facing panel, no submission gate, no translation of findings into plain
language. Phase 1 exists to gather real findings from real bundles so the rule table
that does that translation is built from what actually occurs, rather than from the
one bundle that happened to be at hand.
