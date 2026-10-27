"""Check the reference dataset; this does not run an extraction model."""
import csv
import hashlib
import json
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIELDS = {"supplier", "invoice_number", "invoice_date", "total_amount", "currency"}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def main() -> None:
    data_dir = ROOT / "data"
    manifest = json.loads((data_dir / "ground_truth.json").read_text(encoding="utf-8"))
    cases = manifest["cases"]
    require(manifest["synthetic"] is True, "Dataset must be marked synthetic.")
    require(len(cases) == 5, "Expected exactly five documents.")
    require(
        Counter(case["scenario"] for case in cases)
        == {"valid": 3, "missing_total": 1, "duplicate": 1},
        "Expected three valid cases, one missing total and one duplicate.",
    )
    names = [case["file"] for case in cases]
    require(len(set(names)) == len(names), "Filenames must be unique.")
    require(
        set(names) == {path.name for path in (data_dir / "samples").glob("*.pdf")},
        "PDF files and reference records must match.",
    )
    seen: dict[str, dict] = {}
    hashes = []
    for case in cases:
        name = case["file"]
        require(Path(name).name == name, f"Expected a plain filename: {name}")
        fields = case["expected_fields"]
        require(set(fields) == FIELDS, f"Wrong extraction fields: {name}")
        date.fromisoformat(fields["invoice_date"])
        require(fields["currency"] in {"PLN", "EUR", "USD"}, f"Wrong currency: {name}")
        amount = fields["total_amount"]
        if amount is not None:
            require(isinstance(amount, str), f"Amount must be a decimal string: {name}")
            decimal_amount = Decimal(amount)
            require(decimal_amount.is_finite() and decimal_amount > 0, f"Invalid amount: {name}")
            require(format(decimal_amount, ".2f") == amount, f"Amount needs two decimals: {name}")
        missing = [field for field, value in fields.items() if value is None]
        require(missing == case["expected_missing_fields"], f"Wrong missing fields: {name}")
        if case["scenario"] == "missing_total":
            require(missing == ["total_amount"], "The incomplete invoice must lack its total.")
        else:
            require(not missing, f"Unexpected missing values: {name}")

        content = (data_dir / "samples" / name).read_bytes()
        require(content.startswith(b"%PDF-") and content.rstrip().endswith(b"%%EOF"),
                f"Invalid PDF envelope: {name}")
        digest = hashlib.sha256(content).hexdigest()
        require(digest == case["sha256"], f"Fingerprint mismatch: {name}")
        hashes.append(digest)
        original_name = case["duplicate_of"]
        if original_name:
            require(case["scenario"] == "duplicate", f"Unexpected duplicate link: {name}")
            require(original_name in seen, f"Original must appear before its duplicate: {name}")
            original = seen[original_name]
            require(fields == original["expected_fields"], "Duplicate reference values differ.")
            require(
                content == (data_dir / "samples" / original_name).read_bytes(),
                "Duplicate must match the original byte for byte.",
            )
        else:
            require(digest not in hashes[:-1], f"Undeclared duplicate: {name}")
        seen[name] = case
    require(len(set(hashes)) == 4, "Expected four unique file contents.")

    with (data_dir / "ground_truth.csv").open(encoding="utf-8-sig", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    require(len(csv_rows) == len(cases), "CSV row count differs from JSON.")
    for row, case in zip(csv_rows, cases, strict=True):
        expected_row = {
            "file": case["file"], **case["expected_fields"],
            "scenario": case["scenario"], "duplicate_of": case["duplicate_of"],
        }
        require(
            row == {key: "" if value is None else value for key, value in expected_row.items()},
            f"CSV differs from JSON: {case['file']}",
        )
    print("PASS: 5 synthetic PDFs; 3 valid, 1 missing total, 1 exact duplicate.")
    print("PASS: reference fields, dates, amounts, SHA-256 and CSV/JSON consistency.")
    print("This verifies fixture integrity, not LLM extraction accuracy.")


if __name__ == "__main__":
    main()
