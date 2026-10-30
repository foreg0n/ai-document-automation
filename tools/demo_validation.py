"""Offline reference-data demo, not generated model output or an accuracy test.

Run from the project root: python -m tools.demo_validation --overwrite
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

from document_automation.cli import parse_reference_date
from document_automation.validation import validate_invoice

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output" / "validation-demo")
    parser.add_argument("--as-of", type=parse_reference_date)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    reference_date = args.as_of or date.today()
    print("REFERENCE-DATA DEMO: fixture answers only; no LLM calls or accuracy measurement.")
    passed = review = 0
    try:
        manifest = json.loads((ROOT / "data" / "ground_truth.json").read_text(encoding="utf-8"))
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for case in manifest["cases"]:
            report = validate_invoice(case["expected_fields"], reference_date=reference_date)
            destination = args.output_dir / f"{case['file']}.validation.json"
            record = {
                "schema_version": 1, "input_kind": "reference_fixture",
                "source_file": case["file"], "validation": report.to_dict(),
            }
            with destination.open("w" if args.overwrite else "x", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
            print(f"{report.status.upper()} {case['file']}")
            for issue in report.issues:
                print(f"  [{issue.code}] {issue.field}: {issue.message}")
            review += report.requires_review
            passed += not report.requires_review
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}. Use --overwrite to replace existing demo reports.", file=sys.stderr)
        return 1
    print(f"Demo completed: {passed} passed, {review} need review. Reports: {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
