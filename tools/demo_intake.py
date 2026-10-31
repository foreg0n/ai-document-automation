"""Demonstrate real folder intake with explicit reference fixtures, never an LLM."""
import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from document_automation.cli import parse_reference_date
from document_automation.intake import FolderIntake, wait_for_quiet_period
from document_automation.invoice import InvoiceFormatError
from document_automation.pdf_text import extract_pdf_text
from document_automation.pipeline import ProcessResult
from document_automation.storage import DocumentStore, StorageError, content_sha256
from document_automation.validation import ValidationIssue, ValidationReport, validate_invoice

ROOT = Path(__file__).resolve().parents[1]


def fixture_processor(store: DocumentStore, reference_date: date):
    """Only the offline tool reads reference answers; live watch never imports it."""
    if store.input_kind != "reference_fixture":
        raise StorageError("The reference processor requires a separate fixture database.")
    manifest = json.loads((ROOT / "data/ground_truth.json").read_text(encoding="utf-8"))
    known = {
        content_sha256((ROOT / "data/samples" / case["file"]).read_bytes()): case["expected_fields"]
        for case in manifest["cases"]
    }

    def process(source: Path, output: Path) -> ProcessResult:
        content = source.read_bytes()
        digest = content_sha256(content)
        if digest not in known:
            raise InvoiceFormatError("The reference demo accepts only the included sample PDFs.")
        claim = store.claim(digest, source)
        if claim.duplicate:
            stored = store.get(claim.document_id)
            raw = stored["validation"]
            report = ValidationReport(date.fromisoformat(raw["reference_date"]),
                                      tuple(ValidationIssue(**item) for item in raw["issues"]))
            warnings = tuple(stored["metadata"]["warnings"])
        else:
            try:
                text = extract_pdf_text(source, content=content)
                warnings = text.warnings
                report = validate_invoice(known[digest], reference_date=reference_date,
                                          extraction_warnings=warnings)
                store.complete(
                    claim, text=text.to_dict(), fields=known[digest],
                    metadata={"input_kind": "reference_fixture", "model": None,
                              "warnings": list(warnings), "validation_status": report.status},
                    validation=report.to_dict(),
                )
            except Exception as exc:
                store.fail(claim, type(exc).__name__)
                raise
        store.set_export_status(claim, "skipped")
        return ProcessResult((), warnings, report, claim.document_id, claim.duplicate)

    return process


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=ROOT / "output/intake-demo")
    parser.add_argument("--as-of", type=parse_reference_date)
    args = parser.parse_args(argv)
    work = args.work_dir
    print("REFERENCE-FIXTURE INTAKE DEMO: separate database, known answers, no API calls.")
    counts = Counter()
    try:
        incoming = work / "incoming"
        incoming.mkdir(parents=True, exist_ok=True)
        for sample in sorted((ROOT / "data/samples").glob("*.pdf")):
            destination = incoming / sample.name
            if not destination.exists():
                with destination.open("xb") as stream:
                    stream.write(sample.read_bytes())
        with DocumentStore(work / "documents.sqlite3", input_kind="reference_fixture") as store:
            watcher = FolderIntake(
                incoming, store, archive=work / "archive", exports=work / "exports",
                processor=fixture_processor(store, args.as_of or date.today()),
            )
            list(watcher.poll())  # First observation; never processes immediately.
            wait_for_quiet_period(watcher.stable_seconds)
            for event in watcher.poll():
                counts[event.status] += 1
                print(f"{event.status.upper()} {event.source.name} {event.message}")
                if event.result is not None:
                    print(f"  Document #{event.result.document_id}: {event.result.report.status}")
            print(f"Stored documents: {len(store.history(1000))}. Intake outcomes: {dict(counts)}")
    except (OSError, StorageError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Demo database: {work / 'documents.sqlite3'}")
    return 1 if counts["failed"] or counts["skipped_failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
