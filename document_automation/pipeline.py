"""Connect PDF reading to LLM extraction and local output files."""
import json
from pathlib import Path

from .config import LLMSettings
from .invoice import build_request
from .llm_client import request_invoice
from .pdf_text import extract_pdf_text


def process_pdf(
    source: Path, settings: LLMSettings, output_dir: Path, *,
    dry_run: bool = False, overwrite: bool = False,
) -> tuple[tuple[Path, ...], tuple[str, ...]]:
    """Preview without network, or make one call and save schema-checked fields."""
    suffixes = ("request.json",) if dry_run else ("fields.json", "meta.json")
    destinations = tuple(output_dir / f"{source.name}.{suffix}" for suffix in suffixes)
    output_dir.mkdir(parents=True, exist_ok=True)
    # Check conflicts before making a potentially billable request.
    for path in destinations:
        if path.exists():
            if not path.is_file():
                raise IsADirectoryError(f"Output path is not a file: {path}")
            if not overwrite:
                raise FileExistsError(f"Output already exists: {path}. Use --overwrite or another --output-dir.")
    document = extract_pdf_text(source)
    payload = build_request(document, settings)
    if dry_run:
        outputs = ({
            "schema_version": 1, "mode": "dry_run", "source_file": source.name,
            "warnings": list(document.warnings), "request_body": payload,
        },)
    else:
        result = request_invoice(payload, settings)
        outputs = (result.fields, {
            "schema_version": 1, "source_file": source.name,
            "page_count": len(document.pages), "requested_model": settings.model,
            "model": result.model, "usage": result.usage,
            "warnings": list(document.warnings), "validation_status": "not_run",
        })
    created_paths = []
    try:
        for destination, output in zip(destinations, outputs, strict=True):
            with destination.open("w" if overwrite else "x", encoding="utf-8", newline="\n") as stream:
                if not overwrite:
                    created_paths.append(destination)
                stream.write(json.dumps(output, indent=2, ensure_ascii=False) + "\n")
    except OSError:
        for path in created_paths:
            path.unlink(missing_ok=True)
        raise
    return destinations, document.warnings
