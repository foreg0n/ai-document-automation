"""Connect PDF snapshots, deduplication, LLM extraction, storage, and exports."""
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

from .config import LLMSettings
from .invoice import build_request
from .llm_client import request_invoice
from .pdf_text import extract_pdf_text
from .storage import DocumentStore, StorageError, content_sha256
from .validation import ValidationIssue, ValidationReport, validate_invoice


@dataclass(frozen=True)
class ProcessResult:
    outputs: tuple[Path, ...]
    warnings: tuple[str, ...]
    report: ValidationReport | None
    document_id: int | None = None
    duplicate: bool = False


def _check_outputs(destinations: tuple[Path, ...], overwrite: bool) -> None:
    for path in destinations:
        if path.exists():
            if not path.is_file():
                raise IsADirectoryError(f"Output path is not a file: {path}")
            if not overwrite:
                raise FileExistsError(f"Output already exists: {path}. Use --overwrite or another --output-dir.")
    destinations[0].parent.mkdir(parents=True, exist_ok=True)


def _write_outputs(destinations: tuple[Path, ...], outputs: tuple[dict, ...], overwrite: bool) -> None:
    created_paths = []
    try:
        for destination, output in zip(destinations, outputs, strict=True):
            with destination.open("w" if overwrite else "x", encoding="utf-8", newline="\n") as stream:
                if not overwrite:
                    created_paths.append(destination)
                stream.write(json.dumps(output, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    except (OSError, UnicodeError):
        for path in created_paths:
            path.unlink(missing_ok=True)
        raise


def _restore_report(raw: dict) -> ValidationReport:
    try:
        return ValidationReport(
            date.fromisoformat(raw["reference_date"]),
            tuple(ValidationIssue(**item) for item in raw["issues"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise StorageError("The stored validation report is malformed.") from exc


def process_pdf(
    source: Path, settings: LLMSettings | None, output_dir: Path, *,
    dry_run: bool = False, overwrite: bool = False, reference_date: date | None = None,
    store: DocumentStore | None = None,
    settings_loader: Callable[[], LLMSettings] | None = None,
) -> ProcessResult:
    """Reuse completed hashes before loading credentials or calling the model."""
    if dry_run:
        if settings is None:
            raise ValueError("Request previews require model settings.")
        destinations = (output_dir / f"{source.name}.request.json",)
        _check_outputs(destinations, overwrite)
        document = extract_pdf_text(source)
        preview = {
            "schema_version": 1, "mode": "dry_run", "source_file": source.name,
            "warnings": list(document.warnings), "request_body": build_request(document, settings),
        }
        _write_outputs(destinations, (preview,), overwrite)
        return ProcessResult(destinations, document.warnings, None)

    if store is None or store.input_kind != "llm":
        raise StorageError("Live processing requires a separate LLM result database.")
    # Hash and extract the same snapshot, even if the original file later changes.
    content = source.read_bytes()
    digest = content_sha256(content)
    claim = store.claim(digest, source)
    destinations = tuple(output_dir / f"{source.name}.{suffix}" for suffix in
                         ("fields.json", "meta.json", "validation.json"))
    try:
        if claim.duplicate:
            stored = store.get(claim.document_id)
            fields, metadata = stored["fields"], stored["metadata"]
            validation_data = stored["validation"]
            report = _restore_report(validation_data)
            warnings = tuple(metadata["warnings"])
            if not overwrite and any(path.exists() for path in destinations):
                store.set_export_status(claim, "skipped")
                return ProcessResult((), warnings, report, claim.document_id, True)
            _check_outputs(destinations, overwrite)
        else:
            active_settings = settings_loader() if settings_loader is not None else settings
            if active_settings is None or not active_settings.api_key:
                raise ValueError("LLM settings and an API key are required for new documents.")
            _check_outputs(destinations, overwrite)
            document = extract_pdf_text(source, content=content)
            result = request_invoice(build_request(document, active_settings), active_settings)
            fields, warnings = result.fields, document.warnings
            report = validate_invoice(fields, reference_date=reference_date or date.today(),
                                      extraction_warnings=warnings)
            validation_data = report.to_dict()
            metadata = {
                "schema_version": 1, "source_file": source.name, "page_count": len(document.pages),
                "requested_model": active_settings.model, "model": result.model, "usage": result.usage,
                "warnings": list(warnings), "validation_status": report.status,
                "validation_report": destinations[2].name,
                "document_id": claim.document_id, "sha256": digest,
            }
            # Persist first: an export failure must not cause another paid call.
            store.complete(claim, text=document.to_dict(), fields=fields, metadata=metadata,
                           validation=validation_data)
        metadata = {
            **metadata, "source_file": source.name, "validation_report": destinations[2].name,
            "document_id": claim.document_id, "sha256": digest,
        }
        if claim.duplicate:
            metadata["duplicate_of"] = claim.document_id
            metadata["original_source_file"] = stored["source_file"]
        outputs = (fields, metadata, {
            "schema_version": 1, "source_file": source.name,
            "fields_file": destinations[0].name, "text_warnings_checked": True,
            "validation": validation_data,
        })
        try:
            _write_outputs(destinations, outputs, overwrite)
        except (OSError, UnicodeError):
            store.set_export_status(claim, "failed")
            raise
        store.set_export_status(claim, "written")
        return ProcessResult(destinations, warnings, report, claim.document_id, claim.duplicate)
    except Exception as exc:
        store.fail(claim, type(exc).__name__)
        raise
