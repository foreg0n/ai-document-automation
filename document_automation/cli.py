"""Command-line entry points for reading, processing, and validating invoices."""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .config import ConfigurationError, load_settings
from .invoice import InvoiceFormatError
from .llm_client import APIError
from .pdf_text import ExtractedDocument, TextExtractionError, extract_pdf_text
from .pipeline import process_pdf
from .validation import ValidationReport
from .validation_files import collect_field_files, validate_saved_fields


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


def parse_reference_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use a real calendar date in YYYY-MM-DD format.") from exc


def print_review_reasons(report: ValidationReport) -> None:
    for issue in report.issues:
        location = f"{issue.field}: " if issue.field else ""
        print(f"  [{issue.code}] {location}{issue.message}")


def run_saved_validation(args: argparse.Namespace) -> int:
    try:
        files = collect_field_files(args.source)
    except (ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    reference_date = args.as_of or date.today()
    passed = review = failed = 0
    for path in files:
        try:
            output, report = validate_saved_fields(
                path, args.output_dir, reference_date=reference_date, overwrite=args.overwrite,
            )
        except (ValueError, OSError, UnicodeError, RecursionError) as exc:
            failed += 1
            print(f"ERROR {path.name}: {exc}", file=sys.stderr)
            continue
        if report.requires_review:
            review += 1
        else:
            passed += 1
        print(f"{report.status.upper()} {path.name}")
        print_review_reasons(report)
        print(f"  {output}")
    print(f"Validated {len(files)} file(s): {passed} passed, {review} need review, {failed} failed.")
    return 1 if failed else 3 if review else 0


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
    succeeded = failed = review = 0
    reference_date = args.as_of or date.today()
    for path in files:
        try:
            outputs, warnings, report = process_pdf(
                path, settings, args.output_dir,
                dry_run=args.dry_run, overwrite=args.overwrite, reference_date=reference_date,
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
        label = "PREVIEW" if args.dry_run else "OK"
        if report is not None and report.requires_review:
            label = "NEEDS_REVIEW"
            review += 1
        print(f"{label} {path.name}")
        if report is not None:
            print_review_reasons(report)
        for output in outputs:
            print(f"  {output}")
        for warning in warnings:
            print(f"WARNING {path.name}: {warning}", file=sys.stderr)
    skipped = len(files) - succeeded - failed
    print(f"Processed {len(files)} PDF(s): {succeeded} succeeded, {failed} failed, {skipped} skipped.")
    if not args.dry_run:
        print(f"Validation: {succeeded - review} passed, {review} need review.")
    return 1 if failed or skipped else 3 if review else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="document_automation",
        description="Read PDF text, extract invoice fields, or validate saved results.",
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
    process.add_argument("--as-of", type=parse_reference_date, help="Validation reference date YYYY-MM-DD (default: local computer date)")
    validate = subparsers.add_parser("validate", help="Validate saved fields locally; no API calls")
    validate.add_argument("source", type=Path, help="JSON fields file or folder of *.fields.json files")
    validate.add_argument("--output-dir", type=Path, default=Path("output/validation"))
    validate.add_argument("--overwrite", action="store_true", help="Replace previous validation reports")
    validate.add_argument("--as-of", type=parse_reference_date, help="Reference date YYYY-MM-DD (default: local computer date)")
    args = parser.parse_args(argv)
    if args.command == "validate":
        return run_saved_validation(args)
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
