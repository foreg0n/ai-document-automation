"""Local workspace operations shared by HTTP routes and tests."""
import csv
import io
import json
import logging
import re
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile

from .config import ConfigurationError, load_settings
from .invoice import FIELD_DESCRIPTIONS, InvoiceFormatError, parse_invoice_fields
from .llm_client import APIError
from .pipeline import process_pdf
from .storage import DocumentStore, StorageError, content_sha256
from .validation import validate_invoice

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
logger = logging.getLogger(__name__)


def safe_filename(value: str) -> str:
    value = value.replace("\\", "/").rsplit("/", 1)[-1]
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    if not value or not value.lower().endswith(".pdf"):
        raise ValueError("Choose a PDF file.")
    stem = value[:-4][:120].rstrip(" .") or "document"
    if stem.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        stem = "_" + stem
    return stem + ".pdf"


def csv_cell(value) -> str:
    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


class Workspace:
    def __init__(self, database: Path, *, demo=False, uploads=Path("data/uploads"),
                 exports=Path("output/invoices"), env_file=Path(".env"), today=None):
        self.database = Path(database)
        self.demo = demo
        self.input_kind = "reference_fixture" if demo else "llm"
        self.uploads = Path(uploads).resolve()
        self.exports = Path(exports)
        self.env_file = Path(env_file)
        self.today = today or date.today
        with self.store():
            pass  # Create/migrate once before serving requests.
        # PDFium is not thread-safe; all PDF parsing uses this single worker.
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pdf-worker")
        self.lock = threading.Lock()
        self.jobs = {}

    def store(self, readonly=False):
        return DocumentStore(self.database, input_kind=self.input_kind, readonly=readonly)

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=True)

    def list_documents(self, query="", status="all", page=1):
        with self.store(readonly=True) as store:
            rows = store.review_rows()
        counts = {"total": len(rows), "pending": sum(r["state"] == "completed" and r["review_status"] != "confirmed" for r in rows),
                  "confirmed": sum(r["review_status"] == "confirmed" for r in rows),
                  "needs_review": sum(bool(r["validation"] and r["validation"]["requires_review"]) for r in rows)}
        needle = query.strip().casefold()
        filtered = []
        for row in rows:
            if status == "pending" and not (row["state"] == "completed" and row["review_status"] != "confirmed"):
                continue
            if status == "confirmed" and row["review_status"] != "confirmed":
                continue
            if status == "needs_review" and not (row["validation"] and row["validation"]["requires_review"]):
                continue
            if needle and needle not in (row["source_file"] + " " + " ".join(str(v or "") for v in (row["fields"] or {}).values())).casefold():
                continue
            filtered.append(row)
        start = (page - 1) * 25
        return {"items": filtered[start:start + 25], "total": len(filtered), "page": page,
                "page_size": 25, "counts": counts, "demo": self.demo}

    def document(self, document_id):
        with self.store(readonly=True) as store:
            record = store.get(document_id)
        latest = record["latest_review"]
        return {
            "id": record["id"], "source_file": record["source_file"], "state": record["state"],
            "fields": latest["fields"] if latest else record["fields"], "original_fields": record["fields"],
            "validation": latest["validation"] if latest else record["validation"],
            "review_revision": latest["id"] if latest else 0,
            "review_status": "confirmed" if latest and latest["confirmed"] else "draft" if latest else "unreviewed",
            "note": latest["note"] if latest else "", "reviews": record["reviews"],
            "pages": (record["text"] or {}).get("pages", []),
            "model": (record["metadata"] or {}).get("model"), "input_kind": record["input_kind"],
            "attempts": [{key: attempt[key] for key in ("id", "state", "started_at", "error_type", "export_status")}
                         for attempt in record["attempts"]],
        }

    def save_review(self, document_id, payload):
        if not isinstance(payload, dict):
            raise ValueError("Review must be a JSON object.")
        fields = parse_invoice_fields(json.dumps(payload.get("fields"), ensure_ascii=True))
        revision = payload.get("expected_revision")
        confirmed, acknowledged = payload.get("confirmed", False), payload.get("acknowledged", False)
        note = payload.get("note", "")
        if type(revision) is not int or revision < 0 or type(confirmed) is not bool or type(acknowledged) is not bool:
            raise ValueError("Invalid review revision or confirmation flags.")
        if not isinstance(note, str) or len(note) > 2000:
            raise ValueError("Review note must contain at most 2,000 characters.")
        note.encode("utf-8")
        with self.store() as store:
            record = store.get(document_id)
            warnings = tuple((record["metadata"] or {}).get("warnings", []))
            report = validate_invoice(fields, reference_date=self.today(), extraction_warnings=warnings)
            if confirmed and not acknowledged:
                raise ValueError("Confirm that you checked the values against the document.")
            if confirmed and report.requires_review and not note.strip():
                raise ValueError("Add a note explaining the outstanding checks before confirming.")
            store.save_review(document_id, fields=fields, validation=report.to_dict(), confirmed=confirmed,
                              note=note.strip(), expected_revision=revision)
        return self.document(document_id)

    def export_csv(self, scope="confirmed"):
        with self.store(readonly=True) as store:
            rows = store.review_rows()
        columns = ["document_id", "source_file", *FIELD_DESCRIPTIONS,
                   "validation_status", "review_status", "input_kind", "review_note"]
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        for row in reversed(rows):
            if row["state"] != "completed" or (scope == "confirmed" and row["review_status"] != "confirmed"):
                continue
            values = {"document_id": row["id"], "source_file": row["source_file"], **row["fields"],
                      "validation_status": row["validation"]["status"], "review_status": row["review_status"],
                      "input_kind": row["input_kind"], "review_note": row["note"] or ""}
            writer.writerow({key: csv_cell(value) for key, value in values.items()})
        return ("\ufeff" + output.getvalue()).encode("utf-8")

    def pdf_bytes(self, document_id):
        with self.store(readonly=True) as store:
            record = store.get(document_id)
        path = Path(record["source_path"])
        if not path.is_file():
            raise ValueError("The source PDF was moved or removed. Its extracted text is still available.")
        if path.stat().st_size > MAX_UPLOAD_BYTES:
            raise ValueError("The source PDF exceeds the 20 MB preview limit.")
        content = path.read_bytes()
        if content_sha256(content) != record["sha256"]:
            raise ValueError("The source PDF changed after processing. Its saved text is still available.")
        return content

    def submit(self, filename, content):
        filename = safe_filename(filename)
        if not content or len(content) > MAX_UPLOAD_BYTES:
            raise ValueError("PDF must be between 1 byte and 20 MB.")
        if b"%PDF-" not in content[:1024]:
            raise ValueError("This file does not have a PDF header.")
        digest = content_sha256(content)
        if self.demo:
            with self.store(readonly=True) as store:
                row = store.connection.execute("SELECT id FROM documents WHERE sha256=? AND state='completed'", (digest,)).fetchone()
                if row is None:
                    raise ValueError("Demo uploads accept the included sample PDFs. Use the live workspace for your own files.")
        with self.lock:
            if sum(job["state"] in {"queued", "processing"} for job in self.jobs.values()) >= 10:
                raise ValueError("The processing queue is full. Wait for a document to finish.")
            destination = (self.uploads / digest / filename).resolve()
            if self.uploads not in destination.parents:
                raise ValueError("Invalid upload path.")
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(content)
                temporary.replace(destination)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            job_id = secrets.token_hex(12)
            self.jobs[job_id] = {"id": job_id, "source_file": filename, "state": "queued"}
            self.executor.submit(self._process_job, job_id, destination, digest)
            return dict(self.jobs[job_id])

    def job(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise ValueError("Processing job was not found.")
            return dict(self.jobs[job_id])

    def _process_job(self, job_id, source, digest):
        with self.lock:
            self.jobs[job_id]["state"] = "processing"
        try:
            with self.store() as store:
                if self.demo:
                    claim = store.claim(digest, source)
                    store.set_export_status(claim, "skipped")
                    document_id, duplicate = claim.document_id, True
                else:
                    result = process_pdf(source, None, self.exports / digest, store=store,
                                         settings_loader=lambda: load_settings(self.env_file),
                                         reference_date=self.today())
                    document_id, duplicate = result.document_id, result.duplicate
            update = {"state": "completed", "document_id": document_id, "duplicate": duplicate}
        except (ConfigurationError, APIError, StorageError, InvoiceFormatError, ValueError, OSError) as exc:
            update = {"state": "failed", "error": str(exc)}
        except Exception as exc:
            logger.error("Upload processing failed: %s", type(exc).__name__)
            update = {"state": "failed", "error": "Could not process this PDF. Check the file and server configuration."}
        with self.lock:
            self.jobs[job_id].update(update)
            finished = [key for key, value in self.jobs.items() if value["state"] in {"completed", "failed"}]
            for key in finished[:-50]:
                del self.jobs[key]
