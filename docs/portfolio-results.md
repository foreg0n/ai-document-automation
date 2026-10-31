# Observed reference-run results

This is a captured local run of `tools.demo_project`. Its portable observations
are also saved in [JSON](results/reference-run.json). The counts below are from
an executed reference workflow, not an external model or a container run.

Container configuration was validated locally, but image build/run is pending
a working Docker engine. The provided GitHub Actions workflow has not been run
from this session. See [verification limits](08-delivery.md#verification-and-current-limits).

Measured at 2026-10-31T22:07:05.261203+00:00 on Windows-11-10.0.26200-SP0
with Python 3.12.10.

**Provenance: reference_fixture. External model calls: 0. Model accuracy: not measured.**
The processor uses supplied answers. Review actions below are automated workflow
demonstrations, not a person's review. These counts demonstrate application behavior.

| Measurement | Observed value |
| --- | --- |
| Incoming PDFs | 5 |
| Unique stored documents | 4 |
| Initial duplicate outcomes | 1 |
| Initial documents needing review | 1 |
| Files skipped after restart | 5 |
| Attempts before / after restart | 5 / 5 |
| Persistent intake receipts | 5 |
| Verified archived PDF previews | 4 |
| Simulated review revisions | 2 |
| All-results CSV rows | 4 |
| Confirmed-only CSV rows | 1 |
| First intake processing seconds | 0.183832 |
| Restart processing seconds | 0.026540 |
| First intake wall seconds | 2.185189 |
| Restart wall seconds | 2.027367 |

Processing time excludes the configured quiet wait. Wall time includes it.
This is one local fixture run, not an LLM latency, cost, throughput, or accuracy
benchmark. Results depend on the machine and cache state. The dataset has only
five synthetic files and four distinct contents.

Artifacts created in `output/project-demo/20261031T220705Z-eea94b9f/`
(generated files are ignored by Git):

- `report.json`: structured observations and runtime package versions.
- `documents.sqlite3`: isolated reference database and review history.
- `all-results.csv`, `confirmed-results.csv`: UTF-8 exports with BOM.
- `incoming/`, `archive/`: original sample copies and managed PDF snapshots.

Reproduce from the repository root:

```powershell
.\.venv\Scripts\python.exe -m tools.demo_project --as-of 2026-10-31
```

Each invocation creates a fresh run directory; prior results are retained.
The fixture/live database separation remains enforced. Use
`python -m tools.evaluate_results --database data/documents.sqlite3` to evaluate
saved original LLM results after a separate live run.
