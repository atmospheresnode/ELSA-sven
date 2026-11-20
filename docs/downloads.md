# Bundle downloads

`build/downloads.py` serves bundle downloads for owners (bundle page) and staff (Admin Console),
and resumable single-file downloads (NetCDF files, console file browser).

## How a bundle download works

- A bundle goes out as a plain `.tar` made while it is sent. Nothing is copied to `/tmp` or
  any other folder first, so a bundle of any size needs no extra disk space.
- The exact size is worked out from the file list before the first byte, so the browser shows
  real progress. NetCDF barely compresses, so there is no gzip: it would cost about 80 minutes
  of CPU on an 89 GB bundle to save about 13%.
- Entries use the PAX tar format, so files over 8 GiB (and long names) are fine. Owner and
  group are written as 0 with no names.
- The bytes are the same every time for the same files, so a dropped download resumes with an
  HTTP range request. Resuming needs the exact ETag in `If-Range`; anything else starts over.
- Links and special files are left out. Files are opened one folder at a time without following
  links. A file that changes, shrinks or disappears while it is being sent stops the download
  with an error instead of sending a mixed-up archive.

## Parts

`?part=` on `build:bundle_download` and `staff:bundle_download`:

| part | what is in it |
|---|---|
| `all` (default) | every file |
| `no-netcdf` | everything except `.nc` files |
| `labels` | `.xml` labels and `collection_*.csv` inventories |
| `collection&collection=<name>` | one top-level folder |

Old links without `?part=` get the whole bundle.

## Downloads at once

At most `DOWNLOAD_MAX_CONCURRENT` (default 5) downloads of `DOWNLOAD_SLOT_MIN_BYTES` (default
64 MB) or more run at once, since each one holds a web server thread until it finishes. The
next person gets a message to try again in a few minutes, and a resume gets `503` with
`Retry-After`. Slots are lock files in `DOWNLOAD_SLOT_DIR` (default
`<tmp>/elsa-download-slots-<uid>`, which is Apache's private `/tmp` on prod). A lock is let go
when the download ends, is dropped, or its process dies.

## Deploying

No migration, no new folder, no settings needed. Don't create `TEMPORARY_DIR` for downloads:
nothing uses it now, and on prod the `/temporary/` Apache alias would make it public.

## Tests

```
python3 manage.py test build.test_downloads build.test_downloads_live --settings=test_settings
```

`test_downloads_live` runs a real server with curl and Chrome, including a 9 GB sparse file
streamed into GNU tar (`ELSA_SKIP_BIG=1` leaves that out).
