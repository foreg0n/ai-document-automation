"""Command-line entry point for the text extraction stage."""
import argparse
import json
import sys
from pathlib import Path

from .pdf_text import ExtractedDocument, TextExtractionError, extract_pdf_text


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="document_automation",
        description="Extract the text layer from PDF files locally.",
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
    args = parser.parse_args(argv)
    try:
        files = collect_pdf_files(args.source)
    except (ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

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
