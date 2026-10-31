# Stage 6. Upload workspace, human review, and CSV

## Run the workspace

From the project folder:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m document_automation serve --demo --port 8765
```

Open [http://127.0.0.1:8765/](http://127.0.0.1:8765/) in your browser.
Keep the terminal running; Ctrl+C stops the server.

Demo mode creates a separate `output/ui-demo.sqlite3` database containing
synthetic documents. No API key is needed. You can inspect PDFs and extracted
text, edit fields, save drafts, confirm reviews, and download CSV.

Demo uploads accept only the included sample PDFs, recognizing them as
duplicates of existing reference records. Arbitrary PDFs are rejected in this
mode rather than being presented as simulated model results.

For your own documents, configure `.env` as described in stage 3, then run:

```powershell
.\.venv\Scripts\python.exe -m document_automation serve --port 8765
```

Without `--demo`, the workspace uses the main `data/documents.sqlite3` database
and configured model. `--database` selects another database.
A new unique PDF sends its text to the model after you click **Process PDF**.
A completed duplicate reuses the existing record.

## What the interface includes

- Document history with search and 25-record pages.
- A human-review queue and a list of confirmed documents.
- The original PDF and saved text for each page.
- Five editable fields and stage 4 rule issues.
- Review notes, draft saving, and review confirmation.
- Manual revision history and processing attempts.
- CSV downloads for confirmed documents or all completed results.

The interface and stage walkthroughs are in English for use in the portfolio.

## Application components

| File | Responsibility |
| --- | --- |
| `web.py` | FastAPI HTTP routes and the browser workspace |
| `workspace.py` | Uploads, queue, manual edits, PDF integrity, and CSV |
| `web_static/index.html` | Page structure |
| `web_static/styles.css` | Styling and narrow-screen layouts |
| `web_static/app.js` | Browser requests, forms, lists, and notifications |
| `storage.py` | SQLite documents and the manual-revision table |

FastAPI accepts HTTP requests such as getting a document or saving a review.
Uvicorn runs the server that receives those requests from the browser.
JavaScript calls the API through `fetch()` and updates the page without a full reload.

The `serve` command imports web dependencies only when starting the interface.
The PDF-reading and database CLI commands continue to operate separately.

## create_app()

Located in `document_automation/web.py`.
Creates the FastAPI application and a `Workspace` object.
`@app.get(...)` and `@app.post(...)` declare API routes.

For example, GET `/api/documents/1` returns document data, while POST
`/api/documents/1/review` saves a manual revision. GET retrieves data;
POST changes data or starts processing.

The local application serves its own HTML, CSS, and JavaScript.
No external fonts, frontend build, or CDN are required.

The browser session uses a cookie. Changes also require a CSRF token, which
helps reject requests originating from another page. The LLM key stays out of
HTML and JavaScript and is read by the server from its settings.
The server binds to `127.0.0.1`; this is a local application without public hosting.

## Uploads and processing queue

`Workspace.submit()` checks the extension, PDF header, filename, and 20 MB
size limit. Uploaded bytes are saved under `data/uploads/<sha256>/<filename>`.
Filename components that could refer to another directory are removed.

Processing receives a job ID. The browser periodically requests
`/api/jobs/<id>` to check completion. A slow model request therefore does not
prevent switching between documents that are already stored.

`ThreadPoolExecutor(max_workers=1)` runs one PDF worker.
PDFium does not support concurrent calls from multiple threads, so new documents
are processed sequentially. Saving reviews and reading history do not use
PDFium and can run separately.

The queue accepts at most ten active or queued jobs and lives in process memory.
Job state is not recovered after a server restart. Documents, archived PDFs,
and saved manual revisions remain persistent. After an interrupted job, the
PDF can be uploaded again, subject to stage 5 reservation handling.

## Workspace.save_review()

Accepts five fields, a note, a confirmation flag, and the ID of the last manual
revision known to the browser. Empty form fields are sent as JSON `null`.

It checks the field structure and runs `validate_invoice()` again.
Reader warnings remain part of validation: correcting a total does not hide
a page with missing extracted text.

**Save draft** saves changes without confirmation. **Confirm review** requires
acknowledging that the values were checked against the document.
If `needs_review` reasons remain, an explanatory note is also required.
Human confirmation and rule results are stored separately: a confirmed document
can retain `unsupported_currency` or a missing total explicitly acknowledged
by the reviewer.

The model's original fields are preserved. They can be compared with the
current form, and history retains every saved manual revision.

## The reviews table and stale-tab protection

The database schema changes from version 1 to version 2.
A transactional migration adds `reviews` while preserving earlier documents
and attempts. The CLI can read a v1 database without migrating it.

The new table stores document ID, manual field JSON, rule results,
confirmation state, note, and timestamp. Each save appends a row.

If two tabs open the same revision, the first can save its changes.
For the second, the server detects that `expected_revision` no longer matches
the latest revision and returns a conflict. An outdated form cannot overwrite
a newer edit.

Saving a new draft after confirmation changes the current status to `draft`.
The latest revision must be confirmed before inclusion in the confirmed export.
The earlier confirmation remains in history.

## CSV export

`Workspace.export_csv()` reads one record per document and selects the latest
saved manual fields. If no manual revision exists, the original fields are available.

By default, downloads include only documents whose latest manual revision is
confirmed. **All completed documents** also includes drafts and unreviewed
results. Incomplete processing is excluded.

CSV contains the five invoice fields, document ID and filename, validation and
review statuses, provenance, and review note. Duplicate attempts do not produce
extra rows. Reference data is marked `reference_fixture`.

`csv.DictWriter` handles commas, quotes, and newlines.
UTF-8 with BOM helps Excel recognize the encoding. Missing values produce empty
cells. Formula-like values receive an apostrophe prefix so document content
is not executed as a spreadsheet formula.

CSV uses the latest manual revision. JSON exports from `process` continue
to represent the original model response and original processing report.

## PDFs and saved text

`Workspace.pdf_bytes()` reads the source file only through the stored document
path and verifies its SHA-256. If the file was moved or changed, the server
does not present different bytes as the original. Saved text remains available.
PDF previews have the same 20 MB limit.

In the browser, document values are assigned through `textContent` and form
input values rather than inserted into the page as HTML.

## Verification and environment limitations

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tools/verify_samples.py
```

In the current execution environment, 105 checks passed. Another 14 HTTP tests
were skipped: Windows asyncio creates an internal socket pair over loopback,
and the environment blocks that connection. A short capability check runs
before TestClient initialization to avoid hanging. These tests automatically
run on a machine with working local TCP.

Without sockets, checks exercised real SQLite operations, migration, manual
revisions, confirmations and conflicts, the queue with a controlled LLM reply,
CSV, route functions, and session guards. JavaScript syntax and the DOM element
IDs it references were also checked.

Launching Chrome/Edge for screenshot QA also failed in this environment.
Full HTTP-server startup, browser workflows, and visual verification have
therefore not been confirmed here. The `serve` command and HTTP tests are
available for checking from a normal desktop terminal.
Tests do not call an external LLM.
