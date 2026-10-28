# AI Document Automation

A portfolio project built in eight small stages: invoice upload, text extraction,
structured AI extraction, validation, human review, storage, and CSV export.

**Current stage: 3 / 8 - LLM invoice-field extraction and request previews.**

The stage 3 integration is implemented and tested with controlled API responses.
A live provider run still needs your API key and model ID; no real-model
accuracy results are claimed.

## Quick start (Windows / PowerShell)

Python 3.12 or newer. Run these commands from the project root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m document_automation extract data/samples
```

No activation or execution-policy changes are needed. The only third-party
dependency is the pinned PDFium binding in `requirements.txt`.
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
charges. The default is one request per PDF, with no automatic retries.
Use a single PDF for your first live check before processing the whole folder.

Successful runs save two files under `output/invoices/`:

- `invoice.pdf.fields.json`: exactly five nullable string fields.
- `invoice.pdf.meta.json`: source filename, page count, model, available token
  usage, text-reader warnings, and `validation_status: "not_run"`.

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
numbers. The prompt asks for normalized dates and amounts; deterministic checks
of their formats and plausibility will be added in stage 4.

Use `--response-format json_object` explicitly if the selected endpoint supports
JSON mode but not JSON Schema. No automatic format fallback is performed.
Both modes are checked locally for exactly five keys and string-or-null values.
Malformed, duplicate-key, truncated, refused, and tool-call responses are rejected.

Output conflicts are checked before contacting the API. `--overwrite` explicitly
allows replacing previous results. As in stage 2, the two output files are not a
single transaction: a failure during overwrite can require rerunning the command.
Do not run concurrent commands writing to the same output directory.

Inputs above 50,000 extracted characters are rejected rather than silently
truncated. This is a character guard, not a guarantee that every model's context
window will fit the request. The completion limit is 2,048 tokens.
An API/network error stops the batch; the summary reports remaining files as
skipped. File errors and invalid model output allow later documents to continue.

The `.env` file, generated outputs, and the virtual environment are ignored by Git.
The simple `.env` reader accepts `KEY=value`, matching single/double quotes, and
full-line comments. It does not support `export`, interpolation, or inline comments.

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

Exit codes: `0` means all inputs were processed (possibly with warnings);
`1` means at least one input or output failed; `2` means invalid CLI arguments.
In `extract`, a failed file does not stop the rest of a batch. In `process`,
API/network errors stop the batch to avoid repeating failing requests.
No new invoice fields are saved for an input that fails extraction or the
basic model-response contract check.

## Verify

```powershell
.\.venv\Scripts\python.exe tools/verify_samples.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The tests read real sample PDFs and check multiple layouts, page order, textless
pages, invalid inputs, UTF-8 output, overwrite protection, and CLI batch behavior.
Stage 3 tests use controlled API replies to exercise request serialization,
configuration, JSON parsing, null handling, HTTP errors, and end-to-end saving.
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
| `document_automation/__main__.py` | Entry point for `python -m document_automation` |
| `requirements.txt` | Pinned PDF reader dependency |
| `tests/test_text_extraction.py` | Automated stage 2 behavior checks |
| `tests/test_invoice_extraction.py` | Offline stage 3 integration checks |
| `.env.example` | Configuration template without credentials |
| `output/text/` | Generated extraction results; ignored by Git |
| `output/invoices/` | Stage 3 previews or invoice fields and metadata; ignored by Git |
| `data/samples/` | Five text-based, synthetic invoice PDFs |
| `data/ground_truth.json` | Reference field values and expected special cases |
| `data/ground_truth.csv` | The same reference values in a spreadsheet-friendly table |
| `tools/generate_samples.py` | Invoice definitions, rendering and reference generation |
| `tools/fixture_pdf.py` | Dependency-free PDF drawing helper for stage 1 fixtures |
| `tools/verify_samples.py` | Dataset integrity checks |
| `docs/01-sample-data.md` | Russian stage 1 walkthrough |
| `docs/02-text-extraction.md` | Russian stage 2 walkthrough and function explanations |
| `docs/03-llm-extraction.md` | Russian stage 3 setup and function explanations |

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
4. Deterministic validation with clear review reasons.
5. SQLite persistence and file-content duplicate detection.
6. Upload, review and history UI with CSV export.
7. Automatic intake from an incoming folder.
8. Docker, reproducible demo and measured portfolio results.

Five synthetic samples are a development smoke-test set, not evidence of
real-world extraction accuracy. Larger, more varied data will be needed later.
