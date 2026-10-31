# Stage 7. Automatic intake from an incoming folder

## Run the watcher

Configure `.env` as explained in [stage 3](03-llm-extraction.md), then run from
the project folder:

```powershell
.\.venv\Scripts\python.exe -m document_automation watch
```

The command creates `data/incoming/` if needed. Leave the terminal running and
copy PDFs into that folder. Existing PDFs are also picked up at startup.
Use a second terminal or File Explorer to add documents. Ctrl+C stops intake.
This is a foreground command, not an installed Windows service.

Only PDFs directly inside the folder are considered. The extension is
case-insensitive; subfolders, symbolic links, and non-PDF files are ignored.
Each PDF must be at most 20 MB. Processing is sequential.

New unique PDFs use the configured model. Completed byte-for-byte duplicates
reuse SQLite results without loading model credentials or making another API
call. The live watcher never reads reference answers.

## Check the feature without a key

```powershell
.\.venv\Scripts\python.exe -m tools.demo_intake --as-of 2026-10-31
.\.venv\Scripts\python.exe -m document_automation history --database output/intake-demo/documents.sqlite3
```

The demo copies the five supplied PDFs into `output/intake-demo/incoming/`,
waits for stable files, archives them, and runs the same intake scanner.
Its processor explicitly uses reference answers instead of a model. On a fresh
demo database, expect four new documents, one duplicate, and a missing-total
document with `needs_review`.

Run the demo again: all five inputs should say `SKIPPED`, with four documents
still stored. Receipts prevent additional processing attempts after a restart.
Use `--work-dir output/another-intake-demo` for a fresh demonstration.

The database is marked `reference_fixture`, so live `watch` and `process`
cannot use it as their model-result cache. The demo writes SQLite records and
archived PDFs; it does not produce model JSON exports. No extraction-accuracy
claim can be made from reference answers.

## A bounded pass

```powershell
.\.venv\Scripts\python.exe -m document_automation watch data/incoming --once
```

`--once` takes a list of the PDFs present at startup, observes them, waits for
the quiet period, observes them again, and then exits. PDFs arriving after that
initial list wait for a later run. An empty folder is a successful no-op.
Changing or locked files and content owned by another active worker remain
pending; this mode does not wait indefinitely for them.

| Exit code | Meaning for `watch` |
| --- | --- |
| `0` | Newly processed results passed, or no new input needed processing |
| `1` | An input failed, a previous failed input was skipped, or intake stopped on an operational error |
| `2` | Invalid command syntax |
| `3` | At least one newly handled result needs review |
| `4` | Inputs remain pending when the command exits |

Errors take precedence over pending inputs, which take precedence over review.
Already imported results are skipped and do not change the new run's review
count. Ctrl+C returns the summary for the current run, including pending files.

## Options and saved files

```powershell
.\.venv\Scripts\python.exe -m document_automation watch "C:\Invoices\Incoming" --interval 1 --stable-seconds 5
```

| Option | Default | Purpose |
| --- | --- | --- |
| Positional folder | `data/incoming` | Direct incoming PDF children |
| `--database` | `data/documents.sqlite3` | Shared live result cache and intake receipts |
| `--archive-dir` | `data/intake` | Managed PDF snapshots |
| `--output-dir` | `output/intake` | Fields, metadata, and validation JSON |
| `--interval` | `1` | Seconds between continuous polls |
| `--stable-seconds` | `2` | Required period with unchanged file identity, size, and modification time |
| `--once` | Disabled | Two observations of startup inputs, then exit |
| `--retry-failed` | Disabled | Retry a previous input failure once per unchanged observed file in this run |
| `--as-of` | Date at processing time | Fixed validation reference date |
| `--env-file`, `--model`, `--response-format` | Same as `process` | Model configuration and overrides |

Intervals and quiet periods must be positive finite numbers. Increase the
quiet period for slow copies. For a reliable producer handoff, write under a
temporary extension such as `.pdf.part`, close the file, and rename it to `.pdf`
only after the complete document has been written. A quiet-period check cannot
prove that an external producer will never resume writing later.

The watcher keeps the incoming PDF and creates:

```text
data/intake/<sha256>/invoice.pdf
output/intake/<sha256>/invoice.pdf.fields.json
output/intake/<sha256>/invoice.pdf.meta.json
output/intake/<sha256>/invoice.pdf.validation.json
```

The SHA-256 directory separates different contents with the same filename.
The pipeline reads the managed snapshot, so removing or replacing an incoming
PDF later does not break the saved PDF preview. A modified existing archive
copy is reported as an error rather than silently replaced.

The incoming folder must not overlap either managed folder. All default
incoming files, archives, exports, and SQLite files are ignored by Git.

## How the functions work

### FolderIntake.candidates() and poll()

`candidates()` lists direct PDF children in a stable order. `poll()` is an
iterator: consume it to observe and process files. It remembers each file's
device/inode identity, size, modification time, and the monotonic time when that
signature was first seen. A changed signature restarts the quiet period.
Successfully handled files do not create new attempts on every poll.

`time.monotonic()` measures elapsed time without depending on wall-clock
changes. Validation uses the local calendar date separately, or `--as-of`.

### read_stable_snapshot()

This function checks the opened file's metadata, reads at most 20 MB plus one
byte, then checks both the open file and its directory entry again. A changed,
removed, replaced, or locked source is deferred. A bounded read prevents a
growing input from consuming unbounded memory.

The signature excludes `ctime`: Windows/Python 3.12 can expose different
`ctime` values through `stat()` and `fstat()` for the same unchanged file.
Identity, size, and modification time are compared consistently instead.

### archive_snapshot()

This function writes a temporary file in the hash directory, closes it, and
publishes it with `os.replace()`. Processing starts only after publication.
An existing matching copy is reused. Only the helper's own temporary file is
removed; incoming files are never moved or deleted.

### process_pdf()

The existing pipeline reserves the snapshot hash, reads text, requests five
fields, validates them, stores the canonical result, and writes three JSON
files. Duplicates reuse the stored original result and validation date.
Manual review revisions remain separate from that original extraction.

Intake owns its output tree and allows those JSON exports to be regenerated.
If an export fails after the SQLite commit, an explicit retry can recreate
the exports from the stored result without another model call.

### intake_receipt() and record_intake()

SQLite schema v3 adds `intake_receipts`, keyed by resolved incoming path
(normalized for Windows case handling) and
SHA-256. A completed receipt links to matching completed document content.
A failed receipt stores only the exception type, not an exception body or key.
Receipts survive restarts and prevent repeated attempts for unchanged inputs.
Completed receipts cannot be downgraded by a later failure.

Write connections upgrade v1/v2 databases transactionally to v3, keeping
original documents, attempts, and human review revisions. Read-only commands
can still inspect old v1/v2 databases without migrating them. Provenance checks
happen before a migration, preserving fixture/live isolation.

### run_watch()

The CLI consumes intake events, prints saved paths and review reasons, and
returns a summary. It polls continuously or performs the bounded `--once`
pass. Settings are loaded lazily by the pipeline only for new unique content.

## Failures and recovery

A damaged PDF or malformed model response produces `FAILED`; later inputs can
continue. That unchanged path/content is checkpointed and subsequently shows
`SKIPPED_FAILED`. Fixing the input to produce different bytes makes it a new
candidate. To retry the same bytes after fixing an external cause, run:

```powershell
.\.venv\Scripts\python.exe -m document_automation watch --once --retry-failed
```

API, configuration, and database errors stop the command. They are not retried
automatically or checkpointed as terminal input failures. Correct the cause and
restart explicitly. A failed request without a committed model result can make
another paid call on that restart. Unreadable or oversized inputs whose bytes
could not be fully hashed are suppressed for the current run only.

An active document reservation is deferred, not treated as an input failure.
After an interrupted worker, its existing 15-minute reservation may have to
expire before another worker can recover it. If a crash happens after the
result commit but before the intake receipt, recovery uses the content cache.
There is no distributed exactly-once guarantee around an external API call.

Run one watcher per incoming folder and keep its output tree dedicated to
intake. The workspace can run in a second process against the same live
database; refresh its history to see imported documents:

```powershell
.\.venv\Scripts\python.exe -m document_automation serve --port 8765
```

The reference demo can be inspected separately:

```powershell
.\.venv\Scripts\python.exe -m document_automation serve --demo --database output/intake-demo/documents.sqlite3 --port 8765
```

That command also runs the existing stage 6 fixture seeder, which adds duplicate
attempts while retaining the intake records and reviews.

## Verification

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Stage 7 tests use real PDFs, SQLite files, controlled model replies, and a
controlled monotonic clock. They cover quiet periods, file changes during
reads, continuous arrival, restart receipts, content deduplication, same-name
versions, malformed inputs, explicit retries, export recovery, active claims,
size limits, directory selection, CLI exit codes, and schema migration.
No automated test calls an external model.

Verified in the current execution environment: 146 tests in total, 132 passed
and 14 HTTP tests skipped because local TCP/socketpair connections are blocked.
All 27 stage 7 tests passed. The reference demonstration produced four documents
and one duplicate; its repeat skipped all five inputs without adding attempts.

Browser/HTTP limitations recorded in [stage 6](06-workspace.md) still apply;
folder intake itself does not require HTTP or a browser. Stage 8 will add
Docker packaging and a reproducible end-to-end demonstration.
