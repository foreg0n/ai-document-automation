# AI Document Automation

Upload an invoice PDF, review AI-extracted data against the source, and export
the checked result as CSV or JSON.

**Python · FastAPI · SQLite · PDFium · Vanilla JavaScript · Docker**

## Features

- PDF upload and automatic intake from an incoming folder.
- Five extracted fields: supplier, invoice number, date, total, and currency.
- Validation checks, source preview, and editable review forms.
- Review history, Undo, and document-scoped or bulk exports.
- Content-based deduplication that reuses saved results.
- Responsive dark interface with a documented design system.

## Quick start

Python 3.12+. From the project root in Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m document_automation serve --demo --port 8765
```

Open [the workspace](http://127.0.0.1:8765/) or
[the component catalogue](http://127.0.0.1:8765/design-system).
The demo uses included synthetic invoices and reference answers; no API key
or model calls are needed. Reviews survive a restart.

Or, with Docker Desktop's Linux engine running:

```powershell
docker compose up --build demo
```

## Use your own invoices

Create `.env` from [`.env.example`](.env.example) and set your model ID and API
key. Follow the [model configuration guide](docs/03-llm-extraction.md), then run:

```powershell
.\.venv\Scripts\python.exe -m document_automation serve --port 8765
```

For automatic processing, run
`.\.venv\Scripts\python.exe -m document_automation watch`
and add PDFs to `data/incoming/`.

New unique documents send their text to the configured model; completed
duplicates reuse saved results. Current support is text-based invoice PDFs
up to 20 MB in the web workspace. OCR and contract extraction are not implemented.

## Documentation

- [Upload, review, and export](docs/06-workspace.md)
- [Automatic folder intake](docs/07-folder-intake.md)
- [Docker, reproducible demo, and evaluation](docs/08-delivery.md)
- [Design system and accessibility checks](docs/design-system.md)
- [Measured reference-demo results and verification limits](docs/portfolio-results.md)

The reference demo demonstrates application behavior, not real-model accuracy.

## Development

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests
npm install --ignore-scripts
npm run test:ui
```

Node 22+ is used for UI tests. The interface itself has no build step.
