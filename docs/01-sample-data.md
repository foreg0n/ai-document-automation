# Stage 1. Sample documents and reference answers

## What is ready

The `data/samples` folder contains five PDFs. Their content is stored as PDF
text objects rather than images. All company names and documents are synthetic.

| File | Scenario | Amount | Currency |
| --- | --- | --- | --- |
| 01_invoice_pln.pdf | Standard invoice, YYYY-MM-DD date | 1234.50 | PLN |
| 02_invoice_eur.pdf | Different layout, DD.MM.YYYY date, decimal comma | 980.00 | EUR |
| 03_invoice_usd.pdf | Third layout, month-name date, separate payment deadline | 2750.00 | USD |
| 04_invoice_missing_total.pdf | Final total is missing | null | PLN |
| 05_invoice_duplicate.pdf | Exact copy of the first file | 1234.50 | PLN |

We need known answers before testing extraction. Otherwise, it would be hard to
tell whether a model read a document correctly. This set of reference answers is
called **ground truth**.

## Run the stage

Open the project folder in VS Code, then select Terminal > New Terminal.

```powershell
python tools/generate_samples.py
python tools/verify_samples.py
```

The first command recreates the synthetic PDFs, JSON, and CSV. The second checks
dataset integrity. A successful check prints two PASS lines.

This stage needs no API keys or paid model calls.

## Where the source values live

The `INVOICES` list is near the beginning of `tools/generate_samples.py`.
Each item is a Python dictionary describing one synthetic invoice.

For example, `invoice["supplier"]` returns the supplier name, and
`invoice["total_amount"]` returns the amount. `None` means a value is missing.
When saved as JSON, it becomes `null`.

The `layout` and `services` entries control the sample's appearance. They are
not part of the five fields that the extraction system will return.

The source values generate both the PDFs and the reference answers. This makes
the examples reproducible, but does not demonstrate extraction quality:
this stage does not implement extraction or call a model.

## Main generator functions

### format_amount()

Accepts an amount, currency, and layout name. Returns a string to print in a PDF.

Displayed amounts may look like `1,234.50 PLN` or `980,00 EUR`. The reference
answers use one normalized representation: `"1234.50"` and `"980.00"`.

We use `Decimal` because it represents decimal amounts exactly. JSON amounts
are stored as strings. Later calculations should convert them to `Decimal`
rather than `float`.

If the amount is missing, the function prints `Not provided`. It does not
replace the missing value with zero.

### render_invoice()

Takes one invoice's data and draws its PDF: heading, supplier, date, services,
currency, final total, and synthetic-document notice.

The function supports three layouts. They let us check later that extraction
does not depend on a single arrangement of text.

The third document contains both an issue date and a payment deadline.
The correct `invoice_date` is the issue date.

The incomplete document also omits individual service prices. The model should
return `null`: the missing total cannot be calculated or guessed from its content.

### sha256_file()

Reads the file bytes and computes SHA-256, a string of 64 hexadecimal characters.

The fingerprint depends on content, not the filename. Renaming a file leaves
its fingerprint unchanged. The first and fifth PDFs in this dataset contain
identical bytes and have the same SHA-256.

This fingerprint will support exact-duplicate detection. Two PDFs that look
identical can still have different hashes if their metadata differs.
Detecting visually similar documents is a separate task.

### build_samples()

Creates `data/samples`, draws four distinct documents, and copies the first one
with `shutil.copyfile()`. Copying guarantees a byte-for-byte duplicate.

For each file, the function builds a record containing its name, scenario,
SHA-256, expected fields, missing-field list, and any duplicate's original filename.

`FIELD_NAMES` lists the five target fields. The expression
`{field: invoice[field] for field in FIELD_NAMES}` builds a new dictionary
containing only those five fields from the source dictionary.

### write_ground_truth()

Writes the records to two files.

- `ground_truth.json` is useful in code: it preserves nested structure and `null`.
- `ground_truth.csv` is convenient for spreadsheet viewing. A missing amount is
  an empty cell rather than the text `null`.

The CSV uses UTF-8 with BOM to help Excel on Windows recognize its encoding.

The `duplicate_of` field and `expected_missing_fields` list describe expected
system behavior. They are evaluation metadata, not extra extraction fields.

### main()

Calls `build_samples()`, passes its result to `write_ground_truth()`, and
prints what was created.

The `if __name__ == "__main__":` condition runs `main()` when the file is
started with `python tools/generate_samples.py`. Importing the module does
not automatically generate documents.

## What fixture_pdf.py does

This small helper only creates synthetic PDFs. It is not used to read uploaded
documents.

The `PdfPage` class collects drawing commands:

- `text()` adds text, font size, color, and coordinates;
- `rect()` adds a colored rectangle;
- `line()` adds a divider;
- `save()` packages the commands and fonts into a one-page PDF.

`escape_pdf_text()` handles backslashes and parentheses, which have special
meaning in PDF strings. The generator supports ASCII text only. That is enough
for these five English samples; it is not a limitation of the document reader.

`Path(__file__).resolve().parents[1]` locates the project root relative to the
script. The scripts therefore do not depend on a particular absolute user path.

## What verify_samples.py checks

The checker verifies the scenario counts, filenames, five fields, date and
amount formats, supported currencies, deliberately missing total, file hashes,
exact duplicate, and JSON/CSV consistency.

It does not measure LLM accuracy and is not a general-purpose PDF parser.

## How later stages use the dataset

Stage 2 adds a component that reads a PDF and returns its text.
In stage 3, a model receives that text and instructions for extracting five fields.

Do not pass the reference JSON or CSV to the model. Evaluation code keeps those
answers separate and compares the model's output with them.

The five samples support initial development checks. A larger dataset will be
needed to assess extraction quality on real invoices.
