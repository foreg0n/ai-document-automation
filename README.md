# AI Document Automation

A portfolio project built in eight small stages: invoice upload, text extraction,
structured AI extraction, validation, human review, storage, and CSV export.

**Current stage: 2 / 8 - CLI text extraction from PDF files.**

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

This is the text extraction format, not the five invoice fields planned for stage 3.
Dates and amounts retain the source document's spelling.

Existing output is preserved unless `--overwrite` is supplied.
When processing different input folders with matching filenames, use different
output directories to keep their results separate.

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
A failed file does not stop the rest of a batch. No new result is saved for a
PDF that fails text extraction.

## Verify

```powershell
.\.venv\Scripts\python.exe tools/verify_samples.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The tests read real sample PDFs and check multiple layouts, page order, textless
pages, invalid inputs, UTF-8 output, overwrite protection, and CLI batch behavior.
They do not measure invoice-field extraction accuracy.

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
| `document_automation/__main__.py` | Entry point for `python -m document_automation` |
| `requirements.txt` | Pinned PDF reader dependency |
| `tests/test_text_extraction.py` | Automated stage 2 behavior checks |
| `output/text/` | Generated extraction results; ignored by Git |
| `data/samples/` | Five text-based, synthetic invoice PDFs |
| `data/ground_truth.json` | Reference field values and expected special cases |
| `data/ground_truth.csv` | The same reference values in a spreadsheet-friendly table |
| `tools/generate_samples.py` | Invoice definitions, rendering and reference generation |
| `tools/fixture_pdf.py` | Dependency-free PDF drawing helper for stage 1 fixtures |
| `tools/verify_samples.py` | Dataset integrity checks |
| `docs/01-sample-data.md` | Russian stage 1 walkthrough |
| `docs/02-text-extraction.md` | Russian stage 2 walkthrough and function explanations |

## Dataset

Three layouts, three date formats, and three currencies (PLN, EUR, USD).
There are four different documents; the fifth file is an exact copy of the first.

Every document is marked **SYNTHETIC SAMPLE - NOT VALID FOR PAYMENT**.
Suppliers and the client are fictional. These are not real client achievements.

The future field extractor will return only five fields: supplier, invoice_number,
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
3. LLM extraction into the five-field JSON format.
4. Deterministic validation with clear review reasons.
5. SQLite persistence and file-content duplicate detection.
6. Upload, review and history UI with CSV export.
7. Automatic intake from an incoming folder.
8. Docker, reproducible demo and measured portfolio results.

Five synthetic samples are a development smoke-test set, not evidence of
real-world extraction accuracy. Larger, more varied data will be needed later.
