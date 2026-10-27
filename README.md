# AI Document Automation

A portfolio project built in eight small stages: invoice upload, text extraction,
structured AI extraction, validation, human review, storage, and CSV export.

**Current stage: 1 / 8 — synthetic sample documents and reference answers.**

## Run stage 1

Python 3.12 or newer. No third-party packages or API keys are needed at this stage.

From the project root:

```powershell
python tools/generate_samples.py
python tools/verify_samples.py
```

The five PDFs are already included. Running the generator recreates these sample
PDFs and the two reference files. It does not process your own documents.

## Files

| Path | Purpose |
| --- | --- |
| `data/samples/` | Five text-based, synthetic invoice PDFs |
| `data/ground_truth.json` | Reference field values and expected special cases |
| `data/ground_truth.csv` | The same reference values in a spreadsheet-friendly table |
| `tools/generate_samples.py` | Invoice definitions, rendering and reference generation |
| `tools/fixture_pdf.py` | Small dependency-free PDF drawing helper for these fixtures |
| `tools/verify_samples.py` | Dataset integrity checks |
| `docs/01-sample-data.md` | Russian walkthrough of the stage and its functions |

## Dataset

Three layouts, three date formats, and three currencies (PLN, EUR, USD).
There are four different documents; the fifth file is an exact copy of the first.

Every document is marked **SYNTHETIC SAMPLE — NOT VALID FOR PAYMENT**.
Suppliers and the client are fictional. These are not real client achievements.

Extract only five fields: supplier, invoice_number, invoice_date, total_amount,
and currency. Dates normalize to YYYY-MM-DD. Amounts use decimal strings such
as `"1234.50"`. Missing values use JSON `null`.

The incomplete invoice deliberately contains no line-item prices, so the missing
total cannot be reconstructed from subtotals.

Reference answers belong only in evaluation code. Do not send the JSON or CSV
reference file to an extraction model.

## Roadmap

1. Synthetic PDFs and reference answers — complete.
2. CLI for extracting text from text-based PDFs.
3. LLM extraction into the five-field JSON format.
4. Deterministic validation with clear review reasons.
5. SQLite persistence and file-content duplicate detection.
6. Upload, review and history UI with CSV export.
7. Automatic intake from an incoming folder.
8. Docker, reproducible demo and measured portfolio results.

Five synthetic samples are a development smoke-test set, not evidence of
real-world extraction accuracy. Larger, more varied data will be needed later.
