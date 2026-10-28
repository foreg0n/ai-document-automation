"""Command-line entry point for the text extraction stage."""
import argparse
import json
import sys
from pathlib import Path

from .config import ConfigurationError, load_settings
from .invoice import InvoiceFormatError
from .llm_client import APIError
from .pdf_text import ExtractedDocument, TextExtractionError, extract_pdf_text
from .pipeline import process_pdf


def collect_pdf_files(source: Path) -> list[Path]:
    """Select one PDF or the PDFs directly inside a folder, in stable order."""
    if not source.exists():
        raise ValueError(f"Input not found: {source}")
    if source.is_file():
        if source.suffix.lower() != ".pdf":
            raise ValueError(f"Only .pdf files are supported: {source.name}")
        return [source]
    if not source.is_dir():
        raise ValueError(f"Expected a PDF file or folder: {source}")
    files = sorted(
        (path for path in source.iterdir()
         if path.is_file() and path.suffix.lower() == ".pdf"),
        key=lambda path: path.name.casefold(),
    )
    if not files:
        raise ValueError(f"No PDF files found directly inside: {source}")
    return files


def save_extraction(
    document: ExtractedDocument, output_dir: Path, *, overwrite: bool = False,
) -> tuple[Path, Path]:
    """Save UTF-8 text and JSON, protecting existing files by default."""
    output_dir.mkdir(parents=True, exist_ok=True)
    # Retain the original extension to keep the source name unambiguous.
    text_path = output_dir / f"{document.source_file}.txt"
    json_path = output_dir / f"{document.source_file}.json"
    for destination in (text_path, json_path):
        if destination.exists() and not overwrite:
            raise FileExistsError(
                f"Output already exists: {destination}. "
                "Choose another --output-dir or use --overwrite."
            )
    contents = (
        document.text + "\n",
        json.dumps(document.to_dict(), indent=2, ensure_ascii=False) + "\n",
    )
    created_paths = []
    try:
        for destination, content in zip(
            (text_path, json_path), contents, strict=True,
        ):
            # 'x' refuses replacement even if another CLI run creates a file
            # after the existence check. 'w' is enabled only by --overwrite.
            with destination.open(
                "w" if overwrite else "x", encoding="utf-8", newline="\n",
            ) as handle:
                if not overwrite:
                    created_paths.append(destination)
                handle.write(content)
    except OSError:
        # Remove only files created by this attempt, never earlier results.
        for created in created_paths:
            created.unlink(missing_ok=True)
        raise
    return text_path, json_path


def run_invoice_extraction(files: list[Path], args: argparse.Namespace) -> int:
    try:
        settings = load_settings(
            args.env_file, model=args.model, response_format=args.response_format,
            require_api_key=not args.dry_run,
        )
    except (ConfigurationError, OSError, UnicodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.dry_run:
        print("DRY RUN: request previews only; no API calls or invoice-field results.")
    succeeded = failed = 0
    for path in files:
        try:
            outputs, warnings = process_pdf(
                path, settings, args.output_dir,
                dry_run=args.dry_run, overwrite=args.overwrite,
            )
        except APIError as exc:
            failed += 1
            print(f"ERROR {path.name}: {exc} Batch stopped; no automatic retries.", file=sys.stderr)
            break
        except (TextExtractionError, InvoiceFormatError, OSError) as exc:
            failed += 1
            print(f"ERROR {path.name}: {exc}", file=sys.stderr)
            continue
        succeeded += 1
        print(f"{'PREVIEW' if args.dry_run else 'OK'} {path.name}")
        for output in outputs:
            print(f"  {output}")
        for warning in warnings:
            print(f"WARNING {path.name}: {warning}", file=sys.stderr)
    skipped = len(files) - succeeded - failed
    print(f"Processed {len(files)} PDF(s): {succeeded} succeeded, {failed} failed, {skipped} skipped.")
    return 1 if failed or skipped else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="document_automation",
        description="Read PDF text or extract invoice fields with a configured LLM.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    extract = subparsers.add_parser("extract", help="Read a PDF or folder of PDFs")
    extract.add_argument("source", type=Path, help="PDF file or folder (not recursive)")
    extract.add_argument(
        "--output-dir", type=Path, default=Path("output/text"),
        help="Folder for UTF-8 TXT and JSON files (default: output/text)",
    )
    extract.add_argument(
        "--overwrite", action="store_true", help="Replace existing extraction output",
    )
    process = subparsers.add_parser("process", help="Extract five invoice fields with an LLM")
    process.add_argument("source", type=Path, help="PDF file or folder (not recursive)")
    process.add_argument("--output-dir", type=Path, default=Path("output/invoices"))
    process.add_argument("--env-file", type=Path, default=Path(".env"))
    process.add_argument("--model", help="Override LLM_MODEL")
    process.add_argument("--response-format", choices=("json_schema", "json_object"))
    process.add_argument("--dry-run", action="store_true", help="Save request previews without contacting the API")
    process.add_argument("--overwrite", action="store_true", help="Replace previous outputs")
    args = parser.parse_args(argv)
    try:
        files = collect_pdf_files(args.source)
    except (ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.command == "process":
        return run_invoice_extraction(files, args)

    succeeded = 0
    for path in files:
        try:
            document = extract_pdf_text(path)
            text_path, json_path = save_extraction(
                document, args.output_dir, overwrite=args.overwrite,
            )
        except (TextExtractionError, OSError) as exc:
            print(f"ERROR {path.name}: {exc}", file=sys.stderr)
            continue
        succeeded += 1
        print(f"OK {path.name}: {len(document.pages)} page(s), {len(document.text)} characters")
        print(f"  TXT: {text_path}\n  JSON: {json_path}")
        for warning in document.warnings:
            print(f"WARNING {path.name}: {warning}", file=sys.stderr)

    failed = len(files) - succeeded
    print(f"Processed {len(files)} PDF(s): {succeeded} succeeded, {failed} failed.")
    return 1 if failed else 0
