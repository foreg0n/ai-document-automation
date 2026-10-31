"""Score original saved LLM fields against the synthetic reference dataset, offline."""
import argparse
import json
import sys
from pathlib import Path

from document_automation.storage import DEFAULT_DATABASE, DocumentStore, StorageError

ROOT = Path(__file__).resolve().parents[1]


def evaluate(database: Path, manifest_path: Path = ROOT / "data/ground_truth.json") -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    unique = {}
    for case in manifest["cases"]:
        digest = case["sha256"]
        if digest in unique and unique[digest]["expected_fields"] != case["expected_fields"]:
            raise ValueError("Duplicate reference contents have inconsistent expected fields.")
        unique.setdefault(digest, case)
    if not unique:
        raise ValueError("The reference dataset contains no cases.")
    cases, correct, available, exact, total_fields = [], 0, 0, 0, 0
    with DocumentStore(database, readonly=True) as store:
        if store.input_kind != "llm":
            raise ValueError("Reference-fixture databases cannot measure model accuracy. Use saved LLM results.")
        for digest, case in unique.items():
            row = store.connection.execute("SELECT id, state FROM documents WHERE sha256=?", (digest,)).fetchone()
            record = store.get(row["id"]) if row is not None and row["state"] == "completed" else None
            if record is not None and not isinstance(record["fields"], dict):
                raise ValueError(f"Completed document #{record['id']} has malformed saved fields.")
            expected = case["expected_fields"]
            total_fields += len(expected)
            checks = {field: record is not None and record["fields"].get(field) == value
                      and field in record["fields"] for field, value in expected.items()}
            matched = sum(checks.values())
            correct += matched
            available += record is not None
            exact += all(checks.values())
            cases.append({
                "file": case["file"], "sha256": digest,
                "state": row["state"] if row else "missing",
                "document_id": row["id"] if row else None,
                "model": (record["metadata"] or {}).get("model") if record else None,
                "correct_fields": matched, "total_fields": len(expected),
                "field_matches": checks, "exact_match": all(checks.values()),
            })
    return {
        "schema_version": 1, "mode": "saved_llm_results", "synthetic_dataset": manifest["synthetic"],
        "uses_original_fields": True, "duplicates_counted_once": True,
        "expected_documents": len(unique), "available_documents": available,
        "exact_match_documents": exact, "correct_fields": correct, "expected_fields": total_fields,
        "field_accuracy": correct / total_fields, "document_accuracy": exact / len(unique),
        "missing_results_count_as_incorrect": True, "cases": cases,
        "limitations": "Recorded database provenance is trusted. Four synthetic invoices cannot establish real-world accuracy.",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path, help="Write a new JSON report; existing files are preserved")
    args = parser.parse_args(argv)
    try:
        if args.output is not None and args.output.exists():
            raise FileExistsError(f"Evaluation output already exists: {args.output}")
        result = evaluate(args.database)
        encoded = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(encoded)
        print(encoded, end="")
    except (OSError, StorageError, ValueError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0 if result["exact_match_documents"] == result["expected_documents"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
