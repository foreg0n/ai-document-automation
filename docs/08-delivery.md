# Stage 8. Docker, reproducible demonstration, and measured results

## Start the container demo

Use Docker Desktop with the Linux container engine running, and the
`docker compose` plugin. From the repository root:

```powershell
docker compose up --build demo
```

Open [http://127.0.0.1:8765/](http://127.0.0.1:8765/).
The demo uses the included synthetic PDFs and known reference answers.
It does not need `.env`, an API key, or any model calls. Building the image
does need access to the Python base image and package registry.

Ctrl+C stops an attached session. To stop detached services and remove their
containers while keeping saved data:

```powershell
docker compose --profile live --profile intake --profile tools down
```

The named volumes retain documents, archives, and reviews. Do not add `-v`
when you want to keep those volumes. The demo and live workspace use separate
volumes and database provenance. Run only one of `demo` and `workspace` on
host port 8765 at a time.

## What Docker adds

An **image** contains Python, installed dependencies, and application source.
A **container** runs that image. A **volume** keeps writable data outside the
container's disposable filesystem.

| File | Responsibility |
| --- | --- |
| `Dockerfile` | Python 3.12 image, dependency install, non-root user, runtime and test stages |
| `.dockerignore` | Allows public source/fixtures into the build context; excludes keys, local PDFs, databases, output, and the virtual environment |
| `compose.yaml` | Demo, live workspace, watcher, report, and test services |
| `constraints.txt` | Verified versions of runtime and test dependencies |
| `.github/workflows/checks.yaml` | Container build, tests, report, HTTP checks, and restart persistence checks |

The application runs as UID/GID 10001. Writable state belongs under
`/app/state`; application code and public fixture data remain under `/app`.
The `test` stage includes development dependencies and writable test folders;
the final `runtime` stage does not include the test suite or `httpx`.

`requirements.txt` applies version constraints to the dependency graph.
These are version pins, not artifact hashes. The Python base tag
`python:3.12-slim-bookworm` can receive new patch releases; the image is not
claimed to be byte-for-byte reproducible. The demonstration's expected
functional results are reproducible, and reports capture runtime versions.

## Container networking and health

The new `serve --host 0.0.0.0` option lets the web server listen on the
container's network interface. Normal local runs keep `127.0.0.1` as default.
Compose publishes container port 8000 only on the host's `127.0.0.1:8765`.
The existing localhost/session/CSRF checks remain in place.

`serve --output-dir` selects persistent JSON export storage. Compose points
it to `/app/state/exports` instead of the application source tree.

`GET /healthz` opens the workspace database read-only and returns
`{"status":"ok"}` when it is available. It needs no browser session and
exposes no invoice data. Compose calls it with Python's HTTP client every
15 seconds. Check the resulting service status with:

```powershell
docker compose ps
```

## Live workspace and incoming-folder worker

Create `.env` from `.env.example` if it does not exist, then enter your model
ID and key as described in stage 3. Stop the demo before using the same port:

```powershell
docker compose stop demo
docker compose --profile live up --build workspace
```

Compose mounts the existing `.env` file read-only at `/app/config/.env`.
The application reads it with the same parser used by local commands.
Credentials are not copied into the image or written into `compose.yaml`.

For automatic intake, first create the host input folder:

```powershell
New-Item -ItemType Directory -Force data/incoming
docker compose --profile live --profile intake up --build workspace watcher
```

Copy PDFs into the host's `data/incoming/`. The worker reads that bind mount
without moving or deleting inputs. Its managed snapshots and the web
workspace share the `live-state` volume, so imported documents are available
for preview and review. Refresh the browser history to see new imports.
The same database handles content reservations and duplicate reuse.

Both `.env` and `data/incoming/` must exist before starting these services.
On Linux the host PDFs must be readable by the container's user.
The worker uses the existing failure/retry behavior described in stage 7;
there is no automatic restart loop for failed API requests.

The container's live database is separate from a native Windows database at
`data/documents.sqlite3`. Run any inspection or evaluation against the
container's `/app/state/documents.sqlite3` when using this deployment.

## Reproduce the complete workflow without Docker

```powershell
.\.venv\Scripts\python.exe -m tools.demo_project --as-of 2026-10-31
```

Each invocation creates a fresh timestamp/UUID directory under
`output/project-demo/`, preserving earlier results. It checks:

1. Five incoming PDFs become four unique documents and one duplicate.
2. The missing-total invoice keeps its review reason and original null value.
3. A new observer and database connection skip all five after restart.
4. All four archived PDFs can be opened through the workspace integrity check.
5. Automated actions create one confirmed review and one incomplete draft.
6. All-results CSV has four rows; confirmed-only CSV has one.

The tool writes `report.md`, `report.json`, both CSV files, an isolated SQLite
database, input copies, and archived PDFs. **The processor uses reference
answers, and review actions are simulated by code. No human review or model
accuracy is claimed.** No key is read and no external model call is made.

`scan_phase()` records each intake outcome and measures local wall/processing
time using `perf_counter()`. Processing time excludes the quiet-period wait;
wall time includes it. `run_demo()` verifies stored counts, restart behavior,
PDF integrity, review persistence, and CSV scopes before reporting success.
`render_report()` presents those observations with their provenance and limits.

The same demonstration in the container:

```powershell
docker compose --profile tools up --build --abort-on-container-exit --exit-code-from report report
New-Item -ItemType Directory -Force output/docker-report
docker compose cp report:/app/state/report/. output/docker-report
```

The stopped report container is retained so its generated files can be copied.
The `report-state` volume also keeps earlier runs. The report service uses no
live key or live database.

## Evaluate actual saved model results

After separately running `process` on the five sample PDFs with your configured
provider, score the saved original fields locally:

```powershell
.\.venv\Scripts\python.exe -m tools.evaluate_results --database data/documents.sqlite3 --output output/evaluation.json
```

For a container database:

```powershell
docker compose --profile live run --rm workspace python -m tools.evaluate_results --database /app/state/documents.sqlite3
```

`evaluate()` matches documents by SHA-256, counts each unique PDF content once,
and compares the five original model fields exactly with the expected values.
Four unique invoices produce 20 comparisons. Explicit null values count as
correct when expected; an absent key does not count as null. Missing and failed
results count as incorrect. Manual review corrections do not increase model
accuracy. The output includes field matches, coverage, and model IDs.

Reference-fixture databases are rejected. The evaluator trusts the recorded
database provenance; this is not proof of an external provider call. It makes
no API calls itself. Exit codes: `0` for all exact matches, `3` for mismatches
or missing results, `1` for an operational error. A requested output file must
not already exist.

These four synthetic invoices are a smoke-test set. Even a perfect score on
them would not establish accuracy on real invoices, scanned PDFs, or unseen
layouts. See [observed results](portfolio-results.md) and the
[captured JSON](results/reference-run.json) for the current reference run.

## Verification and current limits

```powershell
docker compose --profile live --profile intake --profile tools config --quiet
docker compose --profile tools run --build --rm tests
```

The GitHub Actions workflow runs container tests, executes the reference report,
starts the demo on its published port, checks a real HTTP session/review/CSV,
then restarts the container and checks that the review survived. Its measured
report is added to the job summary. It needs no API secret. This workflow runs
after your manual push; it has not been executed from this local session.

Local verification: 160 tests total, 146 passed, 14 HTTP tests skipped because
this execution environment blocks loopback TCP/socketpair connections. All
14 stage 8 tests passed. Dependency consistency and the full Compose model
were checked. The complete reference demonstration passed locally.

The Docker CLI is installed here, but the engine was unavailable even after a
start request. The attempted build also failed on access to Docker's buildx
lock file. Consequently, image build, Linux native-library loading, actual
container startup, health checks, and volume permissions remain unverified in
this environment. Run the commands above with a working Docker Desktop engine
or inspect the future Actions run. Browser visual checks and live provider
accuracy remain unverified as recorded in earlier stages.

## Official references

- [Dockerfile reference](https://docs.docker.com/reference/dockerfile/)
- [Compose services: ports, volumes, health checks, and profiles](https://docs.docker.com/reference/compose-file/services/)
- [Docker build practices and version pinning](https://docs.docker.com/build/building/best-practices/)
- [GitHub checkout action](https://github.com/actions/checkout)
