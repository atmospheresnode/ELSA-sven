# Running PDS validation on a deployed host

What has to exist on a server before ELSA can validate a bundle. Nothing here is
user-facing yet: Phase 1 runs validate, stores the results and shows them to staff.
Data providers see nothing until the translation layer exists.

## 1. Java 17

validate 4.2.0 is compiled to class file version 61, so Java 11 fails at startup:

    UnsupportedClassVersionError: ... class file version 61.0, this version of the
    Java Runtime only recognizes class file versions up to 55.0

which names neither Java nor a version anyone recognises. On the current host Java 17
is already installed alongside 11:

    /usr/lib/jvm/java-17-openjdk

The system default stays whatever it is; ELSA points at 17 explicitly through
`VALIDATE_JAVA_HOME`, so nothing else on the machine is affected.

## 2. The validate tool

Installed outside the repository, so ~70MB of Java binaries stays out of git history:

    cd /opt
    curl -sL https://github.com/NASA-PDS/validate/releases/download/v4.2.0/validate-4.2.0-bin.tar.gz | tar xz
    chmod +x /opt/validate-4.2.0/bin/validate

Check it before going further. `--version` exercises the Java path without touching
any bundle:

    JAVA_HOME=/usr/lib/jvm/java-17-openjdk /opt/validate-4.2.0/bin/validate --version

## 3. Settings

**The whole `elsa/` directory is gitignored** (`.gitignore` has `/elsa/*`), so
`settings.py` and `secrets.py` do not travel with the code. Everything in this
section has to be applied by hand on each host; pulling the branch will not bring it.

The code tolerates a host where this has not been done yet: every setting is read
with a default, so ELSA imports and runs and simply reports that validation is not
configured. It will not crash on a settings file that predates this work.

In `elsa/secrets.py`, per host:

    VALIDATE_HOME = '/opt/validate-4.2.0'
    VALIDATE_JAVA_HOME = '/usr/lib/jvm/java-17-openjdk'

Optionally in `elsa/settings.py`, if the defaults do not suit; add after the other
path definitions near `TEMPORARY_DIR`:

    VALIDATE_WORK_DIR = os.path.join(BASE_DIR, 'validation')
    VALIDATE_TIMEOUT_SECONDS = 3600

`VALIDATE_HOME` is the release directory itself, the one containing `bin/validate`,
not its parent. That is the mistake worth guarding against, and the error message
says so if you get it wrong.

The two optional settings and what they do:

- `VALIDATE_WORK_DIR` — where the schema cache and reports live. Defaults to
  `validation/` in the project root, which is gitignored. Everything in it is
  regenerable, so it does not need backing up.
- `VALIDATE_TIMEOUT_SECONDS` — how long one run may take before it is treated as
  hung. Defaults to an hour, deliberately generous because content validation
  against a large NetCDF bundle has not been timed against real data yet.

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
| `UnsupportedClassVersionError` in the log | `VALIDATE_JAVA_HOME` is not pointing at Java 17. |
| `exited ... without writing a report` | validate crashed. Run the command by hand to see its output. |
| Run stuck at Running | The child died without recording anything. `run_validation <id>` re-runs it in the foreground. |

A non-zero exit from validate is normal: finding errors is what the tool is for. Only
a missing report counts as the run itself having failed.

## What is deliberately not here yet

No user-facing panel, no submission gate, no translation of findings into plain
language. Phase 1 exists to gather real findings from real bundles so the rule table
that does that translation is built from what actually occurs, rather than from the
one bundle that happened to be at hand.
