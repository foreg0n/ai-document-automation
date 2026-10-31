# AI Document Automation

A portfolio project built in eight small stages: invoice upload, text extraction,
structured AI extraction, validation, human review, storage, and CSV export.

**Current stage: 6 / 8 - local upload, history, human review, and CSV workspace.**

The stage 3 integration is implemented and tested with controlled API responses.
A live provider run still needs your API key and model ID; no real-model
accuracy results are claimed.

## Open the local workspace

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m document_automation serve --demo --port 8765
```

Open **http://127.0.0.1:8765/** in your browser. Keep the terminal running;
press Ctrl+C to stop the server. The demo uses `output/ui-demo.sqlite3`, reference
answers, and the included PDF samples. No API key or model calls are used in
demo mode. Existing review edits survive a restart.

For your own documents, run without `--demo` after configuring `.env`:

```powershell
.\.venv\Scripts\python.exe -m document_automation serve --port 8765
```

The live workspace uses `data/documents.sqlite3`; new unique PDFs use the
configured model, while duplicates reuse stored results. Use `--database` to
choose another database, or `--port` if your chosen port is already in use.

## Quick start (Windows / PowerShell)

Python 3.12 or newer. Run these commands from the project root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m document_automation extract data/samples
```

No activation or execution-policy changes are needed. `requirements.txt` pins
the PDFium reader, FastAPI/Starlette, and Uvicorn for the local web server.
No API keys, LLM calls, or paid services are used in stages 1-2.
After dependency installation, text extraction runs locally without network access.

The five included PDFs produce five TXT and five JSON files in `output/text/`.
Generated results and the virtual environment are ignored by Git.

To run the same demo again, explicitly allow replacing its previous output:

```powershell
.\.venv\Scripts\python.exe -m document_automation extract data/samples --overwrite
```

## Extract your own PDF

```powershell
.\.venv\Scripts\python.exe -m document_automation extract "C:\path\invoice.pdf" --output-dir output/my-invoice
```

You can also pass a folder. Only PDFs directly inside it are processed;
subfolders are not searched. The extension is case-insensitive.
Paths containing spaces should be quoted.

For each successful input, the CLI creates:

- `invoice.pdf.txt`: the combined text of all pages, in UTF-8.
- `invoice.pdf.json`: source filename, page count, combined text,
  numbered pages, and warnings.

Example JSON, abbreviated:

```json
{
  "schema_version": 1,
  "source_file": "01_invoice_pln.pdf",
  "page_count": 1,
  "text": "AI DOCUMENT AUTOMATION / SYNTHETIC DATA\nINVOICE...",
  "pages": [
    {"page_number": 1, "text": "AI DOCUMENT AUTOMATION / SYNTHETIC DATA\nINVOICE..."}
  ],
  "warnings": []
}
```

This is the stage 2 text format, separate from stage 3 invoice-field results.
Dates and amounts retain the source document's spelling.

Existing output is preserved unless `--overwrite` is supplied.
When processing different input folders with matching filenames, use different
output directories to keep their results separate.

## Stage 3: extract invoice fields with an LLM

The new `process` command reads a PDF and sends its page texts to a configured
Chat Completions API. It never reads the ground truth files.
The HTTP client uses Python's standard library; no new packages are needed.

For an offline preview, with no API key or network calls:

```powershell
.\.venv\Scripts\python.exe -m document_automation process data/samples --dry-run --model preview-only
```

This creates five `*.request.json` files in `output/invoices/`, clearly marked
`dry_run`. They contain the request body, not generated invoice fields.
`preview-only` is a placeholder used for previewing, not a live model ID.
Add `--overwrite` to replace earlier previews.

For a live run, copy `.env.example` to `.env` if `.env` does not exist, and edit:

```dotenv
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=YOUR_EXACT_MODEL_ID
LLM_API_KEY=YOUR_API_KEY
LLM_RESPONSE_FORMAT=json_schema
LLM_TIMEOUT_SECONDS=60
```

Replace both placeholders with values from your provider account.
OpenRouter is an example configuration. Other compatible Chat Completions
API roots can be configured through `LLM_BASE_URL`.
The model and its endpoint must support the selected response format.
CLI overrides (`--model`, `--response-format`) take precedence over environment
variables; environment variables take precedence over `.env`.

```powershell
.\.venv\Scripts\python.exe -m document_automation process data/samples/01_invoice_pln.pdf
```

A live run sends document text to the configured provider and can incur API
charges. A new unique PDF makes one request, with no automatic retries;
completed byte-for-byte duplicates reuse the stored result.
Use a single PDF for your first live check before processing the whole folder.

Successful live results are committed to SQLite and exported as three files under `output/invoices/`:

- `invoice.pdf.fields.json`: exactly five nullable string fields.
- `invoice.pdf.meta.json`: source filename, page count, model, available token
  usage, text-reader warnings, and the current validation status.
- `invoice.pdf.validation.json`: deterministic check results and review reasons.

Example field format (illustration, not a recorded live response):

```json
{
  "supplier": "Example Supplier",
  "invoice_number": "EXAMPLE-001",
  "invoice_date": "2026-10-20",
  "total_amount": null,
  "currency": "PLN"
}
```

Missing values must be JSON `null`. Amounts are strings, not binary floating-point
numbers. The prompt asks for normalized dates and amounts; stage 4 checks their
formats and basic plausibility after the response has been parsed.

Use `--response-format json_object` explicitly if the selected endpoint supports
JSON mode but not JSON Schema. No automatic format fallback is performed.
Both modes are checked locally for exactly five keys and string-or-null values.
Malformed, duplicate-key, truncated, refused, and tool-call responses are rejected.

Output conflicts are checked before contacting the API. `--overwrite` explicitly
allows replacing previous results. As in stage 2, the output files are not a
single transaction: a failure during overwrite can require rerunning the command.
In stage 5 the database result is committed before exporting, so an export retry
can reuse that result without another model call.
Do not run concurrent commands writing to the same output directory.

Inputs above 50,000 extracted characters are rejected rather than silently
truncated. This is a character guard, not a guarantee that every model's context
window will fit the request. The completion limit is 2,048 tokens.
An API/network error stops the batch; the summary reports remaining files as
skipped. File errors and invalid model output allow later documents to continue.

The `.env` file, generated outputs, and the virtual environment are ignored by Git.
The simple `.env` reader accepts `KEY=value`, matching single/double quotes, and
full-line comments. It does not support `export`, interpolation, or inline comments.

## Stage 4: deterministic validation

`process` now automatically checks parsed invoice fields and preserves the
original values. A report has status `passed` or `needs_review`, a reference
date, a rules version, and a list of issues with `code`, `field`, and `message`.
The same fields, reader warnings, and reference date produce the same report.

| Check | Demo policy |
| --- | --- |
| Required values | All five fields must be present; null or placeholders need review |
| Date | Exact YYYY-MM-DD spelling, a real calendar date, no date after the reference date |
| Amount | Plain decimal string with two places; zero and negative totals need review |
| Currency | Three uppercase letters; only PLN, EUR, USD are supported initially |
| Reader warnings | Any page without extracted text requires review |

These are project rules, not universal accounting rules. A valid currency outside
the demo list, a future-dated invoice, or a credit note may be legitimate and
still require review. `passed` means the implemented rules found no issue; it
does not prove that the model copied the correct facts from the document.
No values are automatically repaired, inferred, or approved for payment.

Recheck existing stage 3 fields without API calls or a key:

```powershell
.\.venv\Scripts\python.exe -m document_automation validate output/invoices --as-of 2026-10-30
```

You can pass one JSON file instead of a folder. Folders are searched only for
`*.fields.json`, without recursion. Reports go to `output/validation/` by default.
Use `--output-dir` to choose another directory and `--overwrite` to replace
previous reports. Original fields and companion metadata remain unchanged.

When a matching `*.meta.json` exists alongside a `*.fields.json` file, its source
filename and warnings are checked. Corrupt or mismatched metadata is an error.
If metadata is absent, only the supplied fields are checked and the report
records `text_warnings_checked: false`. A separate report records the latest
recheck; the original metadata describes its original processing run.

Both `process` and `validate` accept `--as-of YYYY-MM-DD`. If omitted, they use
the local computer date once per batch. An explicit reference date makes later
rechecks reproducible. Request previews are not validated invoice results.

For a runnable offline demonstration with the included reference answers:

```powershell
.\.venv\Scripts\python.exe -m tools.demo_validation --as-of 2026-10-30 --overwrite
```

This creates five reports in `output/validation-demo/`, marked
`input_kind: "reference_fixture"`. Four pass the rules; the missing-total case
needs review. The duplicate passes these field rules because duplicate detection
belongs to stage 5. This demonstration does not run or evaluate a model.

## Stage 5: SQLite and duplicates

Live `process` runs use `data/documents.sqlite3` by default. Change this with
`--database PATH`. The database contains extracted page text, the five fields,
model/usage metadata, the original validation report, SHA-256, source paths,
and an attempt history. Amounts remain decimal strings. SQLite is included with
Python; no extra package or database server is needed.

```powershell
.\.venv\Scripts\python.exe -m document_automation history
.\.venv\Scripts\python.exe -m document_automation show 1
```

These commands read an existing database without loading `.env` or calling an
API. `history --json` returns JSON; `history --limit 50` changes the default
20-document limit (maximum 1000). `show ID` includes fields, text, validation,
and the document's attempts. Neither command creates a missing database.

Deduplication uses the SHA-256 of the same byte snapshot used for text extraction.
Renaming the file does not change the hash. Editing PDF metadata or other bytes
does change it, even if the pages look identical. Invoice numbers and visually
similar documents are not used for duplicate detection.

`documents` has one row per hash; `attempts` records new runs, failures, retries,
and duplicates. A completed result is reused before API settings are loaded,
including a result whose validation status is `needs_review`.
Failed processing can be retried and does not count as a completed duplicate.
`--dry-run`, `extract`, and standalone `validate` do not write to this database.
Existing JSON results from earlier stages are not automatically imported.

For a duplicate, missing exports can be created under the incoming filename.
If any of its export files already exists, the default keeps the existing
exports and reports use of the database result. `--overwrite` replaces the
exports from the cached result; it does not request another model response.
The original model, validation date, and rules version are preserved.
Use `validate` for a new local check. To deliberately try another model on the
same bytes, choose a separate `--database` and `--output-dir` for that experiment.

Short SQLite transactions reserve a unique hash before calling the API. Another
worker is refused while that document is processing. After an interrupted run,
a reservation can be reclaimed after 15 minutes; the previous worker's ownership
token cannot overwrite the new result. The transaction is not held during the
network request. This lease is a local recovery mechanism, not a guarantee of
exactly-once API billing across crashes or unusually long-running workers.

Results are committed before JSON exports. Attempt history distinguishes a
completed extraction with a failed export from a failed extraction.
Re-export a saved result with `--overwrite` after fixing a write problem.
The database stores extracted text and the original PDF path, not a PDF archive.

### Offline storage demonstration

```powershell
.\.venv\Scripts\python.exe -m tools.demo_storage --as-of 2026-10-31
.\.venv\Scripts\python.exe -m document_automation history --database output/storage-demo.sqlite3
.\.venv\Scripts\python.exe -m document_automation show 1 --database output/storage-demo.sqlite3
```

On a new demo database, the five samples create four documents and one duplicate
attempt. A repeat creates no new documents and records five duplicate attempts.
The missing-total record retains `needs_review`. This demo reads reference
answers explicitly, makes no model calls, and marks its database as
`reference_fixture`. It cannot be used by live `process`, which requires an
`llm` database; this keeps fixture answers out of the real result cache.
Database files and temporary test directories are ignored by Git.

## Stage 6: upload, human review, and CSV

The browser workspace includes drag-and-drop or file selection, a searchable
and paginated document history, rule issues, extracted page text, a PDF preview,
five editable fields, review notes, revision history, and CSV downloads.
The interface and stage walkthroughs are in English.

Select a PDF up to 20 MB, then click **Process PDF**. Uploaded PDFs are archived
under `data/uploads/<sha256>/<filename>` and processed by the existing pipeline.
Only one PDF job parses at a time because PDFium is not thread-safe. The local
in-memory queue accepts up to ten active/queued jobs. Jobs are not a durable
task queue; saved documents and reviews are persistent.

In demo mode, uploads accept only copies of the included PDFs and reuse their
reference records. Unknown demo uploads are rejected rather than simulated as
model results. The live and reference-fixture databases remain separate.

**Save draft** records a new manual revision without changing the original LLM
fields. **Confirm review** also requires checking the source acknowledgement.
If rule issues remain, a note is required; confirmation does not erase those
issues. Editing a confirmed document into a new draft removes its confirmation
from the current export until that latest revision is confirmed again.
An outdated browser tab receives a conflict instead of overwriting a newer edit.

The CSV menu defaults to **Confirmed documents**, using the latest confirmed
manual values. **All completed documents** also includes drafts and unreviewed
results. Both scopes contain one row per unique document and include review
status, validation status, and input provenance. Missing values are blank;
CSV is UTF-8 with BOM. Formula-like cell values are escaped for spreadsheet use.
Export goes through the browser download; no external service is involved.

Database schema v1 is upgraded transactionally to v2 by adding an append-only
`reviews` table. Existing documents, attempts, and original field JSON remain
intact. The CLI can still read v1 databases without modifying them; opening the
workspace or another write connection performs the migration.

The server binds to `127.0.0.1` and is intended for one local workspace. It uses
a process session cookie and CSRF token; API keys remain in server-side settings.
The API exposes document IDs, not arbitrary filesystem paths. Before serving a
PDF, it verifies that the source bytes still match the saved SHA-256. A moved
or changed source can still be reviewed through its saved text.

Stage 6 verification in this execution environment: 105 checks passed;
14 TestClient HTTP checks were skipped because loopback TCP/socketpair creation
is restricted. The review, queue, migration, CSV, route functions, and session
guards were also tested directly without sockets. JavaScript syntax and DOM
IDs were checked. Chrome/Edge could not launch successfully for screenshot QA,
so full HTTP/browser and visual verification remain to be run on an unrestricted
desktop. The HTTP tests stay enabled automatically where local TCP works.

## Errors and limitations

- Missing, unreadable, damaged, and password-locked inputs produce an error.
- A PDF with no extractable text fails with a message about OCR.
  OCR (recognizing text in images) is not implemented in this stage.
- If only some pages lack text, the CLI saves the available text and emits
  warnings in the terminal and JSON. Check those pages before later processing.
  An empty extracted page can be a blank page, a scan, or unsupported content.
- A PDF containing an existing OCR text layer can be read, but the quality of
  that layer is not verified.
- Text reading order in complex tables and layouts is not guaranteed.

Exit codes for `process` and `validate`: `0` means processing/checks completed
without review reasons; `3` means results were saved and at least one needs
review; `1` means an input, API, or output operation failed; `2` means invalid
CLI arguments. An operation error takes precedence over exit code `3`.
For `extract`, exit `0` still permits text-reader warnings. A successful
`--dry-run` returns `0` without claiming invoice validation has passed.
In `extract`, a failed file does not stop the rest of a batch. In `process`,
API, configuration, or storage errors stop the batch to avoid repeating failing requests.
No new invoice fields are saved for an input that fails extraction or the
basic model-response contract check.

## Verify

```powershell
.\.venv\Scripts\python.exe tools/verify_samples.py
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The tests read real sample PDFs and check multiple layouts, page order, textless
pages, invalid inputs, UTF-8 output, overwrite protection, and CLI batch behavior.
Stage 3 tests use controlled API replies to exercise request serialization,
configuration, JSON parsing, null handling, HTTP errors, and end-to-end saving.
Stage 4 tests cover calendar boundaries, exact decimal strings, supported
currencies, missing values, reader warnings, reference dates, and offline rechecks.
Stage 5 tests use real SQLite files to check persistence, unique hashes, separate
connections, expired reservations, rollback, export recovery, retry history,
fixture isolation, and four controlled API calls for five sample inputs.
Stage 6 checks cover schema migration, review revisions and conflicts, original
field preservation, confirmation, CSV scopes and escaping, uploads, queue limits,
and source PDF integrity. HTTP tests need the development-only `httpx` package;
they check loopback availability before initializing Windows asyncio.
No tests call an external LLM. They do not measure a real model's accuracy.

## Run stage 1

The five synthetic PDFs and their reference files are already included.
To recreate them intentionally:

```powershell
python tools/generate_samples.py
python tools/verify_samples.py
```

Stage 1 needs no third-party packages. The generator does not process your own
documents.

## Files

| Path | Purpose |
| --- | --- |
| `document_automation/pdf_text.py` | Reusable PDF text reader and result types |
| `document_automation/cli.py` | CLI arguments, batch processing, TXT/JSON output |
| `document_automation/config.py` | `.env` and environment settings |
| `document_automation/invoice.py` | Five-field schema, prompt, request building and basic response checks |
| `document_automation/llm_client.py` | HTTP API client and completion parsing |
| `document_automation/pipeline.py` | PDF-to-LLM processing and result persistence |
| `document_automation/validation.py` | Deterministic rules, statuses and review reasons |
| `document_automation/validation_files.py` | Offline rechecks of saved fields and companion warnings |
| `document_automation/storage.py` | SQLite schema, reservations, result persistence and history |
| `document_automation/workspace.py` | Upload queue, review operations, PDF integrity and CSV |
| `document_automation/web.py` | FastAPI routes, localhost session and static frontend |
| `document_automation/web_static/` | Browser interface: HTML, CSS, vanilla JavaScript |
| `document_automation/__main__.py` | Entry point for `python -m document_automation` |
| `requirements.txt` | Pinned PDF reader dependency |
| `requirements-dev.txt` | Test client dependency plus runtime requirements |
| `tests/test_text_extraction.py` | Automated stage 2 behavior checks |
| `tests/test_invoice_extraction.py` | Offline stage 3 integration checks |
| `tests/test_validation.py` | Stage 4 rule and offline CLI checks |
| `tests/test_storage.py` | Real SQLite persistence and concurrency checks |
| `tests/test_web_workspace.py` | Workspace operations, migration and HTTP checks |
| `.env.example` | Configuration template without credentials |
| `output/text/` | Generated extraction results; ignored by Git |
| `output/invoices/` | Stage 3 previews or invoice fields and metadata; ignored by Git |
| `output/validation/` | Recheck reports; ignored by Git |
| `data/documents.sqlite3` | Live result database; ignored by Git |
| `data/uploads/` | Managed uploaded PDFs; ignored by Git |
| `data/samples/` | Five text-based, synthetic invoice PDFs |
| `data/ground_truth.json` | Reference field values and expected special cases |
| `data/ground_truth.csv` | The same reference values in a spreadsheet-friendly table |
| `tools/generate_samples.py` | Invoice definitions, rendering and reference generation |
| `tools/fixture_pdf.py` | Dependency-free PDF drawing helper for stage 1 fixtures |
| `tools/verify_samples.py` | Dataset integrity checks |
| `tools/demo_validation.py` | Explicit reference-data validation demo without an LLM |
| `tools/demo_storage.py` | Separate reference-data SQLite and duplicate demonstration |
| `docs/01-sample-data.md` | English stage 1 walkthrough |
| `docs/02-text-extraction.md` | English stage 2 walkthrough and function explanations |
| `docs/03-llm-extraction.md` | English stage 3 setup and function explanations |
| `docs/04-validation.md` | English stage 4 rules, reports and function explanations |
| `docs/05-storage.md` | English SQLite, hash and history walkthrough |
| `docs/06-workspace.md` | English web workspace and manual-review walkthrough |

## Dataset

Three layouts, three date formats, and three currencies (PLN, EUR, USD).
There are four different documents; the fifth file is an exact copy of the first.

Every document is marked **SYNTHETIC SAMPLE - NOT VALID FOR PAYMENT**.
Suppliers and the client are fictional. These are not real client achievements.

The field extractor requests only five fields: supplier, invoice_number,
invoice_date, total_amount, and currency. Dates normalize to YYYY-MM-DD.
Amounts use decimal strings such as `"1234.50"`. Missing values use JSON `null`.

The incomplete invoice deliberately contains no line-item prices, so the missing
total cannot be reconstructed from subtotals.

Reference answers belong only in evaluation code. The stage 2 reader never reads
the ground truth files. Do not send the JSON or CSV reference file to an
extraction model.

## Roadmap

1. Synthetic PDFs and reference answers - complete.
2. CLI for extracting text from text-based PDFs - complete.
3. LLM extraction into the five-field JSON format - implemented; live provider verification pending configuration.
4. Deterministic validation with clear review reasons - complete.
5. SQLite persistence and file-content duplicate detection - complete.
6. Upload, review and history UI with CSV export - implemented; HTTP/browser QA limited by execution environment.
7. Automatic intake from an incoming folder.
8. Docker, reproducible demo and measured portfolio results.

Five synthetic samples are a development smoke-test set, not evidence of
real-world extraction accuracy. Larger, more varied data will be needed later.
