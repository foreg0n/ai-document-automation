# Stage 2. Extracting text from PDFs

## What is ready

The application accepts a PDF or folder through the command line, reads each
page's text layer, and saves the result as TXT and JSON.

Processing runs locally. This stage needs no API key or model.
The reader never opens `ground_truth.json` or `ground_truth.csv`.

## Run the stage

From a terminal in the project root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m document_automation extract data/samples
```

If the environment and dependency are already installed, only the last command
is needed. The `output/text` folder receives ten files: TXT and JSON for each PDF.

To repeat the run and replace previous results:

```powershell
.\.venv\Scripts\python.exe -m document_automation extract data/samples --overwrite
```

To process one document into a separate output folder:

```powershell
.\.venv\Scripts\python.exe -m document_automation extract data/samples/01_invoice_pln.pdf --output-dir output/one-invoice
```

The application protects existing results by default. `--overwrite` explicitly
allows replacement. Use different `--output-dir` folders when different input
folders contain PDFs with the same filenames.

## Processing flow

1. `__main__.py` calls `main()`.
2. `main()` parses terminal arguments with the standard `argparse` library.
3. `collect_pdf_files()` builds the input PDF list.
4. `extract_pdf_text()` reads the text of each page.
5. `save_extraction()` writes TXT and JSON.
6. `main()` reports how many files succeeded or failed.

Splitting the work into functions allows the same PDF reader to be used later
in the web application without invoking the command line.

## collect_pdf_files(source)

Located in `document_automation/cli.py`.

If given a file, the function checks its extension and returns a one-item list.
If given a folder, it collects its PDFs and sorts them by name.
It does not search subfolders.

`Path` represents a file or folder path. It can check whether a path exists,
read its extension, and build a new path with the `/` operator.

The expression `path.suffix.lower() == ".pdf"` accepts both `.pdf` and `.PDF`.

## extract_pdf_text(path)

Located in `document_automation/pdf_text.py`.

This stage's main function opens a document with `pypdfium2.PdfDocument`,
visits every page, and calls `get_text_bounded()` on its text representation.

```python
with pdfium.PdfDocument(path) as reader:
    for index in range(len(reader)):
        ...
```

`len(reader)` returns the page count. Python indexes start at zero, so the
page number shown in the result is `index + 1`.

PDFium uses native memory and file resources outside ordinary Python objects.
`with` and `closing()` ensure that `close()` is called, including when an error occurs.

After reading a page, the function normalizes line endings to `\n` and removes
whitespace around the entire page with `strip()`. It preserves dates, amounts,
separators, and labels.

For example, the EUR invoice retains `21.10.2026` and `980,00 EUR`.
Later stages convert them to the normalized invoice-field format.

## PageText and ExtractedDocument

These are data classes declared with `@dataclass`. Python generates their
constructors, avoiding repetitive setup code.

`PageText` stores a page number and its text.

`ExtractedDocument` stores the source filename, pages, and warnings.
Its `text` property joins page texts with a blank line between pages:

```python
return "\n\n".join(page.text for page in self.pages)
```

The property uses `@property`, so callers read `document.text` without parentheses.

`frozen=True` prevents field reassignment after the object is created.
Pages and warnings are tuples: sequences whose contents cannot be changed.
This helps prevent accidental changes while passing results between stages.

`to_dict()` converts the result into a JSON-compatible dictionary.
Page numbers remain separate so later stages can locate information in the PDF.

## save_extraction(document, output_dir)

Located in `document_automation/cli.py`.

Creates the output folder and writes two UTF-8 files:

- `01_invoice_pln.pdf.txt`: all extracted text;
- `01_invoice_pln.pdf.json`: text, numbered pages, and metadata.

`json.dumps()` converts a dictionary into a JSON string.
`ensure_ascii=False` keeps Cyrillic and other Unicode characters readable.
`indent=2` adds indentation.

Both destination names are checked before writing. File mode `"x"` creates
only new files and refuses to overwrite existing ones. If writing new files
fails, the function removes files created by that attempt. Earlier results
are preserved.

With `--overwrite`, mode `"w"` replaces file contents. The two output files
are not a single transaction: if an overwrite is interrupted, repeat the command
after resolving the problem.

## main(argv)

Coordinates the functions and handles expected errors with `try / except`.

`argv` is an argument list. During normal execution, `argparse` reads arguments
from the terminal. Tests can pass a list directly to `main()`.

An individual PDF error is printed to `stderr`, the error-output stream.
The remaining documents continue processing.

Exit codes:

- `0`: all PDFs were processed; any warnings still need checking;
- `1`: at least one input or output operation failed;
- `2`: command-line arguments were invalid.

`raise SystemExit(main())` in `__main__.py` passes that code to the operating
system. In PowerShell, `$LASTEXITCODE` shows the last process exit code.

## Empty pages and scans

A PDF can contain text, images, or both.

If no page yields text, the application reports an error and explains that a
scan needs OCR. OCR recognizes text in images; it is not part of this stage.

If only some pages yield text, the available text is saved. Every textless page
receives a warning and remains in `pages`. Missing extracted text alone does
not prove that a page is a scan: it may be blank or contain unsupported objects.

An existing OCR text layer can be read, but this code does not detect errors
made by the OCR system. Complex tables may produce an inconvenient reading order.

A PDF that requires an opening password returns a clear error.
Password input is not supported by the CLI at this stage.

## Verification

```powershell
.\.venv\Scripts\python.exe tools/verify_samples.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests use real PDFs. They check visible values across layouts, page order,
textless pages, damaged files, Unicode paths, TXT/JSON output, and CLI behavior.

They also verify that the first and fifth samples yield identical text.
Automatic duplicate detection is introduced in stage 5.

These checks verify text reading. They do not measure the five-field extraction
accuracy of an LLM; that belongs to later stages.

## Next stage

In stage 3, the model receives extracted text and an instruction to return five fields:
`supplier`, `invoice_number`, `invoice_date`, `total_amount`, and `currency`.

This stage's JSON stores source text and page information. It is a separate
format from the model's invoice-field response.

Library documentation:
[pypdfium2 Python API](https://pypdfium2.readthedocs.io/en/stable/python_api.html).
