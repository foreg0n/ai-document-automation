# Stage 5. SQLite, history, and exact duplicates

## What is ready

The `process` command now stores its results in SQLite: page text, five fields,
model name, token usage, and the validation report. Each unique PDF receives an ID.
A repeated PDF reuses the stored result without another model call.

SQLite stores tables in a main database file. Python includes it through the
`sqlite3` module, so no separate database server is needed.

The main database is `data/documents.sqlite3`. Change it with `--database`.
The database and SQLite sidecar files are excluded from Git.

Stage 6 adds the browser workspace and separate manual revisions.
The schema upgrades to v2 while preserving original records:
[stage 6](06-workspace.md).
Stage 7 adds durable intake receipts in schema v3; current write connections
upgrade both v1 and v2 databases. See [folder intake](07-folder-intake.md).

## Demonstration without an API

```powershell
.\.venv\Scripts\python.exe -m tools.demo_storage --as-of 2026-10-31
.\.venv\Scripts\python.exe -m document_automation history --database output/storage-demo.sqlite3
.\.venv\Scripts\python.exe -m document_automation show 1 --database output/storage-demo.sqlite3
```

On the first run with a new demo database, there are four documents and five
processing attempts. The fifth PDF is an exact copy of the first and refers to
its document ID. The invoice without a total retains `needs_review`.

A repeat adds five duplicate attempts, while the unique-document count remains
four. The existing database is preserved.

The demonstration uses known reference answers. Its database is marked
`reference_fixture`; live runs use an `llm` database.
The application refuses to mix these kinds of database, keeping reference
answers out of the real model-result cache.

## Normal processing

A new document requires the model settings and key from the previous stage:

```powershell
.\.venv\Scripts\python.exe -m document_automation process data/samples --as-of 2026-10-31
```

If a completed result for the PDF already exists in this database, it is available
without `.env` or a key. New files load their settings before contacting the model.
JSON files from earlier stages are not imported automatically.

Inspect the main database:

```powershell
.\.venv\Scripts\python.exe -m document_automation history
.\.venv\Scripts\python.exe -m document_automation show 1
```

`history` shows the ID, processing state, validation status, original filename,
attempt count, and duplicate count. It lists the most recent 20 documents by
default. `--limit 50` changes the count, while `--json` selects JSON output.

`show ID` displays the selected document's stored fields, text, validation,
and attempts. These commands open an existing database for reading.
They do not create a database when the specified file is missing.

## content_sha256() and the PDF snapshot

`content_sha256()` is in `document_automation/storage.py`.
It accepts bytes and computes SHA-256, a 64-character hexadecimal content fingerprint.

The filename is not part of the calculation. Consequently, `invoice.pdf` and
`copy.pdf` with identical bytes have identical fingerprints.

In `process_pdf()`, the source PDF is read once:

```python
content = source.read_bytes()
digest = content_sha256(content)
```

The same `content` is passed to `extract_pdf_text()`. Even if the original file
changes afterward, hashing and extraction refer to the same byte snapshot.
SQLite stores the extracted text and original path, not an archive of the PDF itself.

This check detects exact content duplicates. Changing PDF metadata also changes
the bytes and hash, even when the document looks the same.
Matching invoice numbers or visual appearance alone do not count as duplicates.

## Database structure

| Table | Contents |
| --- | --- |
| `documents` | One row per SHA-256: text, fields, validation, and processing state |
| `attempts` | A record for each run, retry, error, or duplicate |
| `store_info` | Database provenance: `llm` or `reference_fixture` |

A table is a collection of records: a row is one record, and a column is one of
its properties. `documents.id` is the primary key, the document's unique ID.
`attempts.document_id` is a foreign key linking an attempt to its document.

`documents.sha256` has a `UNIQUE` constraint: SQLite will not allow two rows
with the same fingerprint in one database.

Fields and nested reports are stored as JSON text. `"1234.50"` remains a string;
it is not converted to `float`. Storage methods do not receive the API key.
Errors store the exception type name rather than a raw API error body.

## DocumentStore.claim()

Briefly opens a transaction and searches for the fingerprint.

- If no record exists, it creates a document and attempt in `processing` state.
- If a result is already `completed`, it adds a `duplicate` attempt with the same ID.
- If processing failed, it permits another attempt under the existing document ID.
- If another process is still working on the PDF, it returns a clear busy error.

A transaction groups operations that either commit together or are rolled back.
`BEGIN IMMEDIATE` acquires a write reservation, and `commit()` saves the changes.
On failure, `rollback()` cancels uncommitted changes.

Queries use parameters:

```python
connection.execute("SELECT * FROM documents WHERE sha256 = ?", (digest,))
```

The `?` is a placeholder for data. Values are supplied separately, so their
contents do not become SQL commands. The comma in `(digest,)` creates a
one-element tuple: the query's parameter sequence.

The transaction ends before the API call. Network waiting therefore does not
hold a SQLite write lock throughout the model request.

## Reservations and recovery

Active processing has a 15-minute lease and a random ownership token.
After an interrupted process, a later attempt can reclaim an expired lease.
The earlier unfinished attempt is marked `LeaseExpired`.

`complete()` checks the token, preventing an old worker from overwriting
a result owned by a newer worker.

This is a recovery mechanism for the local application. It does not guarantee
exactly-once API billing after a crash following request submission or when a
worker runs beyond its lease. While an ordinary active reservation remains
valid, another worker gets a busy error and does not call the model.

## complete(), fail(), and export state

`complete()` saves text, fields, metadata, validation, and the finished attempt
in one transaction. Only afterward does `process_pdf()` export JSON files.

If writing the files fails, the result remains in SQLite. History shows a
completed extraction with `export_status: "failed"`. Retrying with `--overwrite`
restores exports from the database without another model call.

`fail()` marks unfinished processing as unsuccessful. It preserves history and
does not turn an already committed result into a processing failure merely
because export failed. A processing error permits a retry rather than creating
a false completed duplicate.

`completed` means processing finished. It does not mean all field checks passed:
the same record can have `validation_status: "needs_review"`.

`DocumentStore` closes its connection when its `with` block exits.
In contrast, the standard `with sqlite3.connect(...)` manages a transaction
without closing the connection itself. Closing releases the database file,
which is particularly important on Windows.

## Repeated runs and JSON files

For a duplicate without output files, three exports are created under its new
incoming filename. Metadata includes `duplicate_of`, the stored document ID.

If any export file already exists, the application preserves the existing files
and reports that it reused the SQLite result. The canonical saved result remains
available through `show ID`.

`--overwrite` replaces JSON exports from the cache. It does not call the model
again. The original model, validation date, and rules version are preserved.
For a new local check, use `validate`; its separate report does not change the
historical processing record in SQLite.

To deliberately compare another model on the same PDF, choose another
`--database` and a separate `--output-dir`.

`--dry-run` writes only a request preview, without database or duplicate records.
`extract` and `validate` also retain their separate behavior.

## ProcessResult

`process_pdf()` returns a `ProcessResult` object containing output paths,
warnings, a validation report, a document ID, and a duplicate flag.
The CLI reads those properties and prints `OK`, `NEEDS_REVIEW`, `DUPLICATE`,
or `PREVIEW`.

A reused result can still require manual review: `DUPLICATE` does not imply
`passed`. Exit code 3 retains its stage 4 meaning for such results;
an operation error takes precedence and returns code 1.

## Verification

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tools/verify_samples.py
```

Tests use real SQLite files and check reopening, exact duplicates, two connections,
ownership loss, transaction rollback, retries after errors, and export recovery.
They also cover processing five PDFs with four controlled API replies, followed
by a repeat with no additional model requests.
Tests do not call an external LLM.
