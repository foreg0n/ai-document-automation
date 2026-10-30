"""Recheck saved stage 3 fields locally, including companion text warnings."""
import json
from datetime import date
from pathlib import Path

from .invoice import parse_invoice_fields
from .validation import ValidationReport, validate_invoice


def collect_field_files(source: Path) -> list[Path]:
    if not source.exists():
        raise ValueError(f"Input not found: {source}")
    if source.is_file():
        if source.suffix.lower() != ".json":
            raise ValueError("Validation input must be a JSON fields file.")
        return [source]
    if not source.is_dir():
        raise ValueError(f"Expected a fields JSON file or folder: {source}")
    files = sorted(
        (path for path in source.iterdir()
         if path.is_file() and path.name.lower().endswith(".fields.json")),
        key=lambda path: path.name.casefold(),
    )
    if not files:
        raise ValueError(f"No *.fields.json files found directly inside: {source}")
    return files


def validate_saved_fields(
    source: Path, output_dir: Path, *, reference_date: date, overwrite: bool = False,
) -> tuple[Path, ValidationReport]:
    """Malformed JSON/metadata is an error, not a passing validation result."""
    is_fields_file = source.name.lower().endswith(".fields.json")
    base_name = source.name[:-len(".fields.json")] if is_fields_file else source.stem
    destination = output_dir / f"{base_name}.validation.json"
    if destination.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {destination}. Use --overwrite or another --output-dir.")
    fields = parse_invoice_fields(source.read_text(encoding="utf-8-sig"))
    warnings = ()
    metadata_loaded = False
    if is_fields_file:
        metadata_file = source.with_name(f"{base_name}.meta.json")
        if metadata_file.exists():
            metadata = json.loads(metadata_file.read_text(encoding="utf-8-sig"))
            if not isinstance(metadata, dict) or metadata.get("source_file") != base_name:
                raise ValueError("Companion metadata does not match the source filename.")
            warning_list = metadata.get("warnings")
            if not isinstance(warning_list, list) or not all(isinstance(item, str) for item in warning_list):
                raise ValueError("Companion metadata must contain a list of text warnings.")
            warnings = tuple(warning_list)
            metadata_loaded = True
    report = validate_invoice(fields, reference_date=reference_date, extraction_warnings=warnings)
    record = {
        "schema_version": 1, "fields_file": source.name,
        "text_warnings_checked": metadata_loaded, "validation": report.to_dict(),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with destination.open("w" if overwrite else "x", encoding="utf-8", newline="\n") as stream:
            created = not overwrite
            stream.write(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    except (OSError, UnicodeError):
        if created:
            destination.unlink(missing_ok=True)
        raise
    return destination, report
