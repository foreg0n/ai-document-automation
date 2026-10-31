"""SQLite/hash demonstration using reference fixtures, never a model or live DB.

Run from the project root: python -m tools.demo_storage
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

from document_automation.cli import parse_reference_date
from document_automation.pdf_text import extract_pdf_text
from document_automation.storage import DocumentStore, StorageError, content_sha256
from document_automation.validation import validate_invoice

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=ROOT / "output" / "storage-demo.sqlite3")
    parser.add_argument("--as-of", type=parse_reference_date)
    args = parser.parse_args(argv)
    reference_date = args.as_of or date.today()
    print("REFERENCE-FIXTURE DEMO: known answers, separate database, no LLM calls.")
    created = duplicates = 0
    try:
        manifest = json.loads((ROOT / "data" / "ground_truth.json").read_text(encoding="utf-8"))
        with DocumentStore(args.database, input_kind="reference_fixture") as store:
            for case in manifest["cases"]:
                source = ROOT / "data" / "samples" / case["file"]
                content = source.read_bytes()
                claim = store.claim(content_sha256(content), source)
                if claim.duplicate:
                    duplicates += 1
                    print(f"DUPLICATE {source.name} -> document #{claim.document_id}")
                else:
                    try:
                        text = extract_pdf_text(source, content=content)
                        report = validate_invoice(case["expected_fields"], reference_date=reference_date,
                                                  extraction_warnings=text.warnings)
                        store.complete(
                            claim, text=text.to_dict(), fields=case["expected_fields"],
                            metadata={"input_kind": "reference_fixture", "model": None,
                                      "warnings": list(text.warnings), "validation_status": report.status},
                            validation=report.to_dict(),
                        )
                    except Exception as exc:
                        store.fail(claim, type(exc).__name__)
                        raise
                    created += 1
                    print(f"STORED #{claim.document_id} {source.name}: {report.status}")
                store.set_export_status(claim, "skipped")
    except (OSError, ValueError, StorageError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Demo completed: {created} new documents, {duplicates} duplicates reused. Database: {args.database}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
