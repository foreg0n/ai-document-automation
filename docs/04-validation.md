# Stage 4. Field validation and manual-review reasons

## What was added

After receiving an LLM response, the application checks its fields with ordinary
Python code. These checks are deterministic: the same fields, reader warnings,
and reference date produce the same result.

A report has one of two statuses:

- `passed`: the implemented rules found no issues;
- `needs_review`: there are reasons to inspect the document manually.

`passed` does not prove that the fields match the PDF. For example, a plausible
but invented amount can pass a format check. This stage does not compare facts
with source text, verify suppliers, or approve payments.

## Run without an API key

Demonstrate the rules using stage 1 reference data:

```powershell
.\.venv\Scripts\python.exe -m tools.demo_validation --as-of 2026-10-30 --overwrite
```

This creates five reports in `output/validation-demo`. Four records pass the rules.
The fourth record has no total and receives `needs_review`.
Each report is marked `input_kind: "reference_fixture"`. This demonstrates
checks on known values rather than model output or LLM accuracy.

The duplicate passes these field checks. Duplicate detection is introduced in stage 5.
A successfully completed demonstration returns exit code 0, including the expected
`needs_review` example.

## Check saved model results

If you already have `*.fields.json` files from a live stage 3 run:

```powershell
.\.venv\Scripts\python.exe -m document_automation validate output/invoices --as-of 2026-10-30
```

Or check one file:

```powershell
.\.venv\Scripts\python.exe -m document_automation validate output/invoices/01_invoice_pln.pdf.fields.json --overwrite
```

Reports go to `output/validation` by default. The input folder may contain other
JSON files: the command selects only `*.fields.json` and does not search subfolders.
A single JSON file can have any filename.

`validate` runs locally without `.env`, a key, or a model call.
If a matching `*.meta.json` accompanies a `*.fields.json` file, the command
reads its textless-page warnings. Invalid metadata causes an error so that
discarded warnings cannot accidentally produce a passing report.

Without metadata, only the supplied fields are checked. The report records
`text_warnings_checked: false`: this run did not check PDF-reading completeness.
Original fields and metadata are preserved; the new report contains the recheck.

## Rules in invoice_demo_v1

| Check | Reason for manual review |
| --- | --- |
| Values of all five fields | `null`, empty text, or a placeholder such as `N/A` |
| Surrounding whitespace | The value needs deliberate normalization |
| Date format | Exactly `YYYY-MM-DD`, without a time or abbreviated components |
| Calendar date | The date must exist; `2026-02-30` is impossible |
| Future date | The invoice date is later than the reference date |
| Amount format | Decimal string with a dot, two decimal places, and no grouping |
| Positive amount | Zero or negative totals need review |
| Currency | Three uppercase Latin letters and membership in the supported list |
| Text completeness | Any warning about a page without extracted text |

The current supported currencies are PLN, EUR, and USD. For example, CHF receives
`unsupported_currency`; the application does not claim that CHF is not a real currency.

These are demo-project rules. A credit note with a negative total, a zero-value
document, or a future-dated invoice may be legitimate. The application flags
these cases because they are not yet supported by its policy.
Dates, amounts, and currencies are not repaired automatically.

## validate_invoice()

Located in `document_automation/validation.py`.

Accepts a field dictionary, a `reference_date`, and reader warnings.
It does not read files, call a model, or modify the input dictionary.
The same rules can therefore be reused in a UI or API.

The nested `add()` function appends a review reason. Checks collect all detected
issues so the user can inspect them together.

Date validation has two steps:

1. A regular expression checks the exact `YYYY-MM-DD` spelling.
2. `date.fromisoformat()` checks that the date exists in the calendar.

A regular expression describes allowed characters and their order. It does not
know how many days February contains, so the second step is necessary.

The parsed date is then compared with `reference_date`.
`--as-of 2026-10-30` sets this date explicitly. Without it, the CLI reads the
local computer date once per batch. Using the same reference date makes an
earlier report reproducible.

## Why Decimal is used

The amount pattern first checks its spelling. `"1234.50"` is accepted, while
`"1,234.50"`, `"1234,50"`, and `"1e3"` need format review.

An exact comparison follows:

```python
elif Decimal(value) <= 0:
    add("non_positive_amount", "total_amount", "...")
```

`Decimal` represents decimal values exactly. A normal `float` stores values
in binary and cannot represent every decimal fraction exactly.
The application does not round the model's value or replace a missing total with zero.

## ValidationIssue and ValidationReport

`ValidationIssue` stores one reason:

- `code`: a short, stable identifier for application logic;
- `field`: a field name, or `None` for a document-wide issue;
- `message`: a readable explanation.

`ValidationReport` stores the reference date and an issue tuple.
`requires_review` indicates whether any issues exist, while `status` returns
the corresponding status. `to_dict()` prepares the report for JSON output.

Example report fragment:

```json
{
  "rules_version": "invoice_demo_v1",
  "reference_date": "2026-10-30",
  "status": "needs_review",
  "requires_review": true,
  "issues": [
    {
      "code": "missing_value",
      "field": "total_amount",
      "message": "Required invoice value is missing; check the source document."
    }
  ]
}
```

An issue code allows the UI to highlight a field and display its explanation.
The rules version identifies the policy used to produce a report.

## validate_saved_fields() and process_pdf()

`validate_saved_fields()` is in `validation_files.py`. It reads saved JSON,
checks the stage 3 structural contract, loads any companion metadata, and calls
`validate_invoice()`. It then writes a separate report.

Invalid JSON, duplicate keys, or incorrect types violate the stage 3 contract
and count as processing errors. A missing total represented by valid JSON
`null` is processed successfully but requires manual review.

`process_pdf()` automatically runs the same validator after obtaining model
fields. A live run creates three files:

- `*.fields.json`: original response fields;
- `*.meta.json`: processing metadata and validation status;
- `*.validation.json`: review reasons and rules version.

A `--dry-run` creates only a request preview; it has no invoice fields to validate.
All three result filenames are checked before contacting the API.

Since stage 5, completed results are committed to SQLite first.
Exact duplicates reuse stored fields and the original report. Use `validate`
to check again with a different reference date.
See [stage 5](05-storage.md).

## Exit codes

| Code | Meaning for process and validate |
| --- | --- |
| 0 | All results were processed and need no review |
| 1 | Reading, API, or writing failed; this takes precedence over code 3 |
| 2 | Invalid command-line arguments |
| 3 | Results were saved, but at least one needs manual review |

Code 3 lets later automation distinguish a document needing review from a
program error. `extract` and `--dry-run` retain their earlier exit-code behavior.

## Verification

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tools/verify_samples.py
```

Tests cover calendar boundaries and leap years, decimal amounts, currencies,
missing values, reader warnings, preservation of original fields, report
overwrite protection, and saving results through the processing pipeline.
Tests do not call an external LLM.
