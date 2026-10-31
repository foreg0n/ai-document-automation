# Stage 3. Extracting invoice fields with an LLM

## What was added

The `process` command connects the PDF reader from stage 2 to a model request.
The response must contain exactly five fields:

| Field | Meaning |
| --- | --- |
| `supplier` | Supplier issuing the invoice |
| `invoice_number` | Invoice identifier |
| `invoice_date` | Issue date in YYYY-MM-DD format |
| `total_amount` | Final total as a string, such as `"1234.50"` |
| `currency` | Currency code, such as `"PLN"` |

Any value can be `null` when missing or ambiguous.
A missing total is not replaced with zero or calculated from other amounts.

The current `process` command also runs stage 4 checks and saves a separate
report. See [stage 4](04-validation.md).

The integration is implemented. Tests use real PDFs and controlled API replies.
No live model call was made during development: that requires your API key and
model ID. Real-model extraction accuracy has not yet been measured.

## Preview without a key

From the project root:

```powershell
.\.venv\Scripts\python.exe -m document_automation process data/samples --dry-run --model preview-only --overwrite
```

`--dry-run` means prepare a request without sending it.
`preview-only` is a placeholder for previewing, not an actual model ID.
`--overwrite` allows earlier request previews to be replaced.

Five `*.request.json` files appear in `output/invoices`. They contain
`mode: "dry_run"`, page text, and the proposed request.
This mode does not create fields presented as if a model had generated them.

## Configure a live request

The project root contains `.env.example`. Copy it to `.env` if that file does
not exist. An empty configuration file was already created in the prepared workspace.

Fill in the values locally:

```dotenv
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL=YOUR_MODEL_ID
LLM_API_KEY=YOUR_API_KEY
LLM_RESPONSE_FORMAT=json_schema
LLM_TIMEOUT_SECONDS=60
```

Replace the model and key placeholders. Use the exact model ID from your
provider account. Both the model and its endpoint must support the selected
response format.

OpenRouter is an example configuration. `LLM_BASE_URL` can point to another
compatible Chat Completions API root. Do not append `/chat/completions`;
the client adds that path.

Start with one PDF:

```powershell
.\.venv\Scripts\python.exe -m document_automation process data/samples/01_invoice_pln.pdf
```

This sends text to the provider and can incur API charges.
The request includes page text, without filenames or reference JSON/CSV.
The key is sent in an HTTP header rather than in the model's text input.

For an endpoint that supports JSON mode but not JSON Schema, explicitly select:

```powershell
.\.venv\Scripts\python.exe -m document_automation process data/samples/01_invoice_pln.pdf --response-format json_object
```

There is no automatic response-format fallback or request retry.

## load_settings() and read_env_file()

Located in `document_automation/config.py`.

`read_env_file()` reads `NAME=value` assignments. It supports full-line comments
and matching quotes around values. It does not execute commands, expand variables,
support `export`, or parse inline comments after values.

`load_settings()` applies this precedence:

1. CLI options `--model` and `--response-format`.
2. Environment variables.
3. Values from `.env`.

It checks that a model is selected and that a live request has an API key.
A `--dry-run` needs no key. Settings are passed as an `LLMSettings` object.
Its key field uses `repr=False`, so the object's normal text representation
does not display the key.

## invoice_schema() and build_request()

Located in `document_automation/invoice.py`.

`invoice_schema()` describes the response structure using JSON Schema:
which keys and value types are allowed. All five keys are required, but each
value may be a string or `null`. Extra keys are forbidden.

`build_request()` assembles the HTTP request body:

- `model`: selected model;
- `messages`: system instructions and page text;
- `response_format`: JSON Schema or JSON mode;
- `max_tokens`: a 2,048-token completion limit;
- `stream: false`: receive the complete response.

A token is a unit into which a model splits text. It is not necessarily one
character or word. Long responses can reach the limit; incomplete responses
are rejected.

The system instructions distinguish the issue date from the payment deadline
and the supplier from the customer, and prohibit guessing missing values.
PDF content is described as data rather than instructions. This guides the
model but does not guarantee that its answer is correct.

Documents exceeding 50,000 extracted characters are rejected. Automatic
truncation could lose a total on the last page, so it is not performed.
The character limit does not guarantee a fit within every model's context window.

## request_invoice() and parse_completion()

Located in `document_automation/llm_client.py`.

`request_invoice()` encodes the request body as UTF-8 and sends an HTTP POST
with the standard `urllib` module. No separate SDK or extra dependency is required
for this client.

It handles HTTP errors, unreachable servers, timeouts, and invalid responses.
Raw error bodies are not printed. An API error stops the batch and marks
remaining PDFs as skipped.

`parse_completion()` reads the response text from `choices[0].message.content`.
It rejects incomplete responses, model refusals, and tool requests.
A successful result contains the fields, model name, and available token usage.

## parse_invoice_fields()

Located in `document_automation/invoice.py`.

Converts the model's JSON string into a Python dictionary and checks the basic contract:

- exactly the five required keys;
- non-empty strings or `None`, corresponding to JSON `null`;
- no duplicate keys, Markdown, or surrounding commentary;
- amounts represented as strings rather than numbers.

For example, `"total_amount": "1234.50"` is accepted, while
`"total_amount": 1234.5` is rejected. This preserves the exact decimal spelling
instead of converting it to a binary floating-point number.

This is not a factual correctness check. A string describing an impossible date
can pass a type check. Stage 4 adds date, amount, currency, and review-reason checks.

## process_pdf()

Located in `document_automation/pipeline.py`.

Connects the components: reads a PDF, builds a request, obtains fields, and saves
the result. Before contacting the API, it checks for output filename conflicts.
This helps avoid unnecessary model requests on repeated runs.

A successful live run creates:

- `invoice.pdf.fields.json`: only the five invoice fields;
- `invoice.pdf.meta.json`: filename, page count, model, token usage, and reader warnings;
- `invoice.pdf.validation.json`: the stage 4 field-validation report.

Metadata records `validation_status` as `passed` or `needs_review`.
Textless-page warnings are preserved, so available text is not treated as
proof that the entire invoice was read.

Existing results are preserved without `--overwrite`. Output files are not
written in one transaction; an interrupted overwrite may require a repeat run.
Do not run concurrent processes writing to the same output directory.

## Verification and limits

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tools/verify_samples.py
```

Tests cover reading a real PDF, request contents, controlled API response saving,
`null` handling, settings, network errors, and output protection.
An external API is replaced by a test transport that can reproducibly return
JSON or errors without a key or API charges.

These tests verify the integration code, not a real model's accuracy.
Reference answers remain in separate evaluation code and are not sent to the LLM.

API documentation:
[Structured Outputs](https://openrouter.ai/docs/guides/features/structured-outputs).
