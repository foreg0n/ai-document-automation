"""Reproduce intake, restart, simulated review, and CSV using reference fixtures."""
import argparse
import csv
import io
import json
import platform
import shutil
import sys
import uuid
from collections import Counter
from datetime import date, datetime, timezone
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

from document_automation.cli import parse_reference_date
from document_automation.intake import FolderIntake, wait_for_quiet_period
from document_automation.storage import DocumentStore, StorageError
from document_automation.workspace import Workspace
from tools.demo_intake import ROOT, fixture_processor

DEMO_DATE = date(2026, 10, 31)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(f"Demo verification failed: {message}")


def scan_phase(watcher: FolderIntake) -> dict:
    started = perf_counter()
    require(not list(watcher.poll()), "new observer processed before the quiet period")
    wait_for_quiet_period(watcher.stable_seconds)
    processing = perf_counter()
    events = list(watcher.poll())
    finished = perf_counter()
    require(watcher.pending == 0, "some demo inputs are still pending")
    return {
        "outcomes": dict(Counter(event.status for event in events)),
        "wall_seconds": round(finished - started, 6),
        "processing_seconds": round(finished - processing, 6),
        "quiet_period_seconds": watcher.stable_seconds,
        "files": [{"file": event.source.name, "outcome": event.status,
                   "document_id": event.result.document_id if event.result else None,
                   "validation": event.result.report.status if event.result else None}
                  for event in events],
    }


def render_report(report: dict) -> str:
    first, replay = report["first_intake"], report["restart"]
    store, exports = report["storage"], report["csv"]
    return f"""# Reproducible project demonstration

Measured at {report['measured_at_utc']} on {report['environment']['platform']}
with Python {report['environment']['python']}.

**Provenance: reference_fixture. External model calls: 0. Model accuracy: not measured.**
The processor uses supplied answers. Review actions below are automated workflow
demonstrations, not a person's review. These counts demonstrate application behavior.

| Measurement | Observed value |
| --- | --- |
| Incoming PDFs | {report['dataset']['files']} |
| Unique stored documents | {store['documents']} |
| Initial duplicate outcomes | {first['outcomes'].get('duplicate', 0)} |
| Initial documents needing review | {report['validation']['needs_review']} |
| Files skipped after restart | {replay['outcomes'].get('skipped', 0)} |
| Attempts before / after restart | {report['attempts_before_restart']} / {store['attempts']} |
| Persistent intake receipts | {store['intake_receipts']} |
| Verified archived PDF previews | {report['pdf_previews_verified']} |
| Simulated review revisions | {store['reviews']} |
| All-results CSV rows | {exports['all_rows']} |
| Confirmed-only CSV rows | {exports['confirmed_rows']} |
| First intake processing seconds | {first['processing_seconds']:.6f} |
| Restart processing seconds | {replay['processing_seconds']:.6f} |
| First intake wall seconds | {first['wall_seconds']:.6f} |
| Restart wall seconds | {replay['wall_seconds']:.6f} |

Processing time excludes the configured quiet wait. Wall time includes it.
This is one local fixture run, not an LLM latency, cost, throughput, or accuracy
benchmark. Results depend on the machine and cache state. The dataset has only
five synthetic files and four distinct contents.

Artifacts in this run directory:

- `report.json`: structured observations and runtime package versions.
- `documents.sqlite3`: isolated reference database and review history.
- `all-results.csv`, `confirmed-results.csv`: UTF-8 exports with BOM.
- `incoming/`, `archive/`: original sample copies and managed PDF snapshots.

Reproduce from the repository root:

```powershell
.\\.venv\\Scripts\\python.exe -m tools.demo_project --as-of 2026-10-31
```

Each invocation creates a fresh run directory; prior results are retained.
The fixture/live database separation remains enforced. Use
`python -m tools.evaluate_results --database data/documents.sqlite3` to evaluate
saved original LLM results after a separate live run.
"""


def run_demo(output_dir: Path, reference_date: date = DEMO_DATE, *, stable_seconds=2) -> tuple[Path, dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc)
    run = output_dir / (stamp.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    run.mkdir()
    incoming = run / "incoming"
    incoming.mkdir()
    manifest = json.loads((ROOT / "data/ground_truth.json").read_text(encoding="utf-8"))
    for case in manifest["cases"]:
        shutil.copyfile(ROOT / "data/samples" / case["file"], incoming / case["file"])
    database = run / "documents.sqlite3"
    with DocumentStore(database, input_kind="reference_fixture") as store:
        options = {"archive": run / "archive", "exports": run / "exports",
                   "stable_seconds": stable_seconds,
                   "processor": fixture_processor(store, reference_date)}
        first = scan_phase(FolderIntake(incoming, store, **options))
        require(first["outcomes"] == {"processed": 4, "duplicate": 1}, "unexpected initial intake outcomes")
        attempts_before = store.connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
    # Open a new connection and observer to exercise durable restart behavior.
    with DocumentStore(database, input_kind="reference_fixture") as store:
        options["processor"] = fixture_processor(store, reference_date)
        replay = scan_phase(FolderIntake(incoming, store, **options))
        require(replay["outcomes"] == {"skipped": 5}, "restart did not reuse all receipts")
        records = store.history()
        needs_review = sum(row["validation_status"] == "needs_review" for row in records)
        require(needs_review == 1, "missing-total review result was not preserved")

    workspace = Workspace(database, demo=True, uploads=run / "uploads",
                          exports=run / "exports", today=lambda: reference_date)
    try:
        previews = sum(bool(workspace.pdf_bytes(row["id"])) for row in records)
        complete = workspace.document(1)
        workspace.save_review(1, {
            "fields": complete["fields"], "expected_revision": 0,
            "confirmed": True, "acknowledged": True,
            "note": "Automated reference demo acknowledgement; no human review performed.",
        })
        incomplete = next(workspace.document(row["id"]) for row in records
                          if row["validation_status"] == "needs_review")
        workspace.save_review(incomplete["id"], {
            "fields": incomplete["fields"], "expected_revision": 0,
            "confirmed": False, "acknowledged": False,
            "note": "Automated draft: missing total still needs a source review.",
        })
        csv_counts = {}
        for scope, filename in (("all", "all-results.csv"), ("confirmed", "confirmed-results.csv")):
            content = workspace.export_csv(scope)
            (run / filename).write_bytes(content)
            csv_counts[scope + "_rows"] = len(list(csv.DictReader(io.StringIO(content.decode("utf-8-sig")))))
    finally:
        workspace.close()
    with DocumentStore(database, readonly=True) as store:
        counts = {table: store.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for table in ("documents", "attempts", "intake_receipts", "reviews")}
        require(counts == {"documents": 4, "attempts": 5, "intake_receipts": 5, "reviews": 2}, "unexpected persisted counts")
        require(store.get(incomplete["id"])["fields"]["total_amount"] is None, "demo changed the missing original total")
    require(csv_counts == {"all_rows": 4, "confirmed_rows": 1}, "unexpected CSV scope")
    require(previews == 4 and attempts_before == 5, "preview or initial attempt count mismatch")
    report = {
        "schema_version": 1, "mode": "reference_fixture", "measured_at_utc": stamp.isoformat(),
        "reference_date": reference_date.isoformat(), "external_model_calls": 0,
        "model_accuracy": "not_measured", "review_mode": "automated_simulation",
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        "packages": {name: version(name) for name in ("pypdfium2", "fastapi", "uvicorn")}},
        "dataset": {"files": 5, "unique_contents": 4, "synthetic": True},
        "first_intake": first, "restart": replay, "attempts_before_restart": attempts_before,
        "storage": counts, "validation": {"passed": 3, "needs_review": needs_review},
        "pdf_previews_verified": previews, "csv": csv_counts, "checks_passed": True,
    }
    (run / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (run / "report.md").write_text(render_report(report), encoding="utf-8")
    return run, report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("output/project-demo"))
    parser.add_argument("--as-of", type=parse_reference_date, default=DEMO_DATE)
    args = parser.parse_args(argv)
    print("REFERENCE DEMO: no model calls; automated review simulation; accuracy not measured.")
    try:
        run, report = run_demo(args.output_dir, args.as_of)
    except (OSError, ValueError, StorageError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"PASS: {report['storage']['documents']} unique documents; 1 duplicate; 1 needs review.")
    print("Restart: 5 skipped, no new attempts. CSV: 4 all-results rows, 1 confirmed row.")
    print(f"Report: {run / 'report.md'}\nJSON: {run / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
