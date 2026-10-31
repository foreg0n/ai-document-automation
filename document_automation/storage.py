"""SQLite results and short reservations for exact-content deduplication."""
import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

DEFAULT_DATABASE = Path("data/documents.sqlite3")
APPLICATION_ID = 0x41494441
SCHEMA_VERSION = 3
LEASE_SECONDS = 900
SCHEMA = """
CREATE TABLE store_info (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    input_kind TEXT NOT NULL CHECK (input_kind IN ('llm', 'reference_fixture'))
);
CREATE TABLE documents (
    id INTEGER PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE CHECK (length(sha256) = 64),
    source_file TEXT NOT NULL,
    source_path TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('processing', 'completed', 'failed')),
    owner_token TEXT,
    lease_until TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    text_json TEXT,
    fields_json TEXT,
    metadata_json TEXT,
    validation_json TEXT,
    validation_status TEXT CHECK (validation_status IN ('passed', 'needs_review'))
);
CREATE TABLE attempts (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    source_file TEXT NOT NULL,
    source_path TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('processing', 'completed', 'duplicate', 'failed')),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    error_type TEXT,
    export_status TEXT NOT NULL DEFAULT 'not_started'
        CHECK (export_status IN ('not_started', 'pending', 'written', 'skipped', 'failed'))
);
CREATE INDEX attempts_document_id ON attempts(document_id);
"""
REVIEWS_SCHEMA = """
CREATE TABLE reviews (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    fields_json TEXT NOT NULL,
    validation_json TEXT NOT NULL,
    confirmed INTEGER NOT NULL CHECK (confirmed IN (0, 1)),
    note TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX reviews_document_id ON reviews(document_id, id);
"""
INTAKE_SCHEMA = """
CREATE TABLE intake_receipts (
    source_path TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    state TEXT NOT NULL CHECK (state IN ('completed', 'failed')),
    document_id INTEGER REFERENCES documents(id),
    error_type TEXT,
    finished_at TEXT NOT NULL,
    PRIMARY KEY (source_path, sha256),
    CHECK ((state = 'completed' AND document_id IS NOT NULL AND error_type IS NULL)
        OR (state = 'failed' AND error_type IS NOT NULL))
);
"""


class StorageError(Exception):
    """A local persistence operation failed."""


class DocumentBusyError(StorageError):
    """Another worker owns an unexpired reservation for this content."""


class ReviewConflictError(StorageError):
    """The review was changed in another tab before this edit was saved."""


@dataclass(frozen=True)
class DocumentClaim:
    document_id: int
    attempt_id: int
    owner_token: str | None
    duplicate: bool


def content_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DocumentStore:
    def __init__(
        self, path: Path, *, readonly: bool = False, input_kind: str = "llm",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(path)
        self.clock = clock or utc_now
        self.connection = None
        if input_kind not in {"llm", "reference_fixture"}:
            raise StorageError("Unknown database input kind.")
        if readonly and not self.path.is_file():
            raise StorageError(f"Database not found: {self.path}")
        try:
            if readonly:
                self.connection = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
            else:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.connection = sqlite3.connect(self.path, timeout=5)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys = ON")
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            application = self.connection.execute("PRAGMA application_id").fetchone()[0]
            if version == 0 and not readonly:
                tables = self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                if tables or application not in (0, APPLICATION_ID):
                    raise StorageError("This is not an empty AI Document Automation database.")
                # One script makes schema initialization atomic as well.
                try:
                    self.connection.executescript(
                        "BEGIN IMMEDIATE;\n" + SCHEMA + REVIEWS_SCHEMA + INTAKE_SCHEMA
                        + f"\nPRAGMA application_id = {APPLICATION_ID};"
                        + f"\nPRAGMA user_version = {SCHEMA_VERSION};"
                    )
                    self.connection.execute("INSERT INTO store_info VALUES (1, ?)", (input_kind,))
                    self.connection.commit()
                except Exception:
                    self.connection.rollback()
                    raise
                version, application = SCHEMA_VERSION, APPLICATION_ID
            if version not in (1, 2, SCHEMA_VERSION) or application != APPLICATION_ID:
                raise StorageError("Unsupported database format or schema version.")
            row = self.connection.execute("SELECT input_kind FROM store_info WHERE id=1").fetchone()
            if row is None:
                raise StorageError("Database input-kind metadata is missing.")
            self.input_kind = row[0]
            if not readonly and self.input_kind != input_kind:
                raise StorageError("Reference-fixture and LLM results must use separate databases.")
            if version < SCHEMA_VERSION and not readonly:
                try:
                    # Recheck under the write lock: another process may have
                    # migrated the database since our initial version read.
                    self.connection.execute("BEGIN IMMEDIATE")
                    current = self.connection.execute("PRAGMA user_version").fetchone()[0]
                    if current not in (1, 2, SCHEMA_VERSION):
                        raise StorageError("Unsupported database format or schema version.")
                    if current == 1:
                        for statement in REVIEWS_SCHEMA.split(";"):
                            if statement.strip():
                                self.connection.execute(statement)
                    if current < 3:
                        self.connection.execute(INTAKE_SCHEMA)
                    self.connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                    self.connection.commit()
                except Exception:
                    self.connection.rollback()
                    raise
                version = SCHEMA_VERSION
            self.schema_version = version
        except (sqlite3.Error, OSError) as exc:
            self.close()
            raise StorageError(f"Cannot open the SQLite database: {exc}") from exc
        except Exception:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    @contextmanager
    def transaction(self):
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            yield
            self.connection.commit()
        except sqlite3.Error as exc:
            self.connection.rollback()
            raise StorageError(f"SQLite transaction failed: {exc}") from exc
        except Exception:
            self.connection.rollback()
            raise

    def claim(self, digest: str, source: Path) -> DocumentClaim:
        """Reserve new/failed content, or record reuse of a completed document."""
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise StorageError("Expected a SHA-256 hex digest.")
        now = self.clock().astimezone(timezone.utc)
        timestamp = now.isoformat()
        lease_until = (now + timedelta(seconds=LEASE_SECONDS)).isoformat()
        token = uuid.uuid4().hex
        path = str(source.resolve())
        with self.transaction():
            row = self.connection.execute("SELECT * FROM documents WHERE sha256=?", (digest,)).fetchone()
            duplicate = row is not None and row["state"] == "completed"
            if row is not None and row["state"] == "processing":
                if row["lease_until"] > timestamp:
                    raise DocumentBusyError(f"Document #{row['id']} is already being processed. Retry after it finishes or its 15-minute reservation expires.")
                self.connection.execute(
                    "UPDATE attempts SET state='failed', finished_at=?, error_type='LeaseExpired' "
                    "WHERE document_id=? AND state='processing'", (timestamp, row["id"]),
                )
            if row is None:
                cursor = self.connection.execute(
                    "INSERT INTO documents (sha256, source_file, source_path, state, owner_token, lease_until, created_at, updated_at) "
                    "VALUES (?, ?, ?, 'processing', ?, ?, ?, ?)",
                    (digest, source.name, path, token, lease_until, timestamp, timestamp),
                )
                document_id = cursor.lastrowid
            else:
                document_id = row["id"]
                if not duplicate:
                    self.connection.execute(
                        "UPDATE documents SET source_file=?, source_path=?, state='processing', owner_token=?, lease_until=?, updated_at=? WHERE id=?",
                        (source.name, path, token, lease_until, timestamp, document_id),
                    )
            cursor = self.connection.execute(
                "INSERT INTO attempts (document_id, source_file, source_path, state, started_at, finished_at, export_status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (document_id, source.name, path, "duplicate" if duplicate else "processing", timestamp,
                 timestamp if duplicate else None, "pending" if duplicate else "not_started"),
            )
            return DocumentClaim(document_id, cursor.lastrowid, None if duplicate else token, duplicate)

    def complete(self, claim: DocumentClaim, *, text: dict, fields: dict, metadata: dict, validation: dict) -> None:
        values = [json.dumps(value, ensure_ascii=False, allow_nan=False) for value in (text, fields, metadata, validation)]
        timestamp = self.clock().astimezone(timezone.utc).isoformat()
        with self.transaction():
            cursor = self.connection.execute(
                "UPDATE documents SET state='completed', owner_token=NULL, lease_until=NULL, updated_at=?, "
                "text_json=?, fields_json=?, metadata_json=?, validation_json=?, validation_status=? "
                "WHERE id=? AND state='processing' AND owner_token=?",
                (timestamp, *values, validation["status"], claim.document_id, claim.owner_token),
            )
            if cursor.rowcount != 1:
                raise StorageError("Document reservation was lost; this worker cannot overwrite the result.")
            self.connection.execute(
                "UPDATE attempts SET state='completed', finished_at=?, export_status='pending' WHERE id=?",
                (timestamp, claim.attempt_id),
            )

    def fail(self, claim: DocumentClaim, error_type: str) -> None:
        """Keep failure history, but do not store exception bodies or credentials."""
        timestamp = self.clock().astimezone(timezone.utc).isoformat()
        with self.transaction():
            cursor = self.connection.execute(
                "UPDATE documents SET state='failed', owner_token=NULL, lease_until=NULL, updated_at=? "
                "WHERE id=? AND state='processing' AND owner_token=?",
                (timestamp, claim.document_id, claim.owner_token),
            )
            if cursor.rowcount:
                self.connection.execute(
                    "UPDATE attempts SET state='failed', finished_at=?, error_type=? WHERE id=?",
                    (timestamp, error_type, claim.attempt_id),
                )

    def set_export_status(self, claim: DocumentClaim, status: str) -> None:
        with self.transaction():
            self.connection.execute("UPDATE attempts SET export_status=? WHERE id=?", (status, claim.attempt_id))

    def get(self, document_id: int) -> dict:
        try:
            row = self.connection.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
            if row is None:
                raise StorageError(f"Document #{document_id} was not found.")
            record = dict(row)
            record.pop("owner_token")
            for name in ("text", "fields", "metadata", "validation"):
                raw = record.pop(name + "_json")
                record[name] = json.loads(raw) if raw is not None else None
            record["input_kind"] = self.input_kind
            record["attempts"] = [dict(item) for item in self.connection.execute(
                "SELECT * FROM attempts WHERE document_id=? ORDER BY id", (document_id,),
            )]
            record["reviews"] = []
            if self.schema_version >= 2:
                for item in self.connection.execute("SELECT * FROM reviews WHERE document_id=? ORDER BY id", (document_id,)):
                    review = dict(item)
                    review["fields"] = json.loads(review.pop("fields_json"))
                    review["validation"] = json.loads(review.pop("validation_json"))
                    review["confirmed"] = bool(review["confirmed"])
                    record["reviews"].append(review)
            record["latest_review"] = record["reviews"][-1] if record["reviews"] else None
            return record
        except (sqlite3.Error, ValueError) as exc:
            raise StorageError(f"Cannot read stored document: {exc}") from exc

    def history(self, limit: int = 20) -> list[dict]:
        if not 1 <= limit <= 1000:
            raise StorageError("History limit must be between 1 and 1000.")
        try:
            rows = self.connection.execute(
                "SELECT d.id, d.sha256, d.source_file, d.state, d.validation_status, d.created_at, "
                "COUNT(a.id) AS attempts, SUM(a.state='duplicate') AS duplicates "
                "FROM documents d LEFT JOIN attempts a ON a.document_id=d.id "
                "GROUP BY d.id ORDER BY d.id DESC LIMIT ?", (limit,),
            ).fetchall()
            return [{**dict(row), "input_kind": self.input_kind} for row in rows]
        except sqlite3.Error as exc:
            raise StorageError(f"Cannot read document history: {exc}") from exc

    def intake_receipt(self, source: Path, digest: str) -> dict | None:
        """Find the durable outcome for this incoming path and byte content."""
        if self.schema_version < 3:
            return None
        try:
            row = self.connection.execute(
                "SELECT * FROM intake_receipts WHERE source_path=? AND sha256=?",
                (os.path.normcase(str(source.resolve())), digest),
            ).fetchone()
            return dict(row) if row is not None else None
        except sqlite3.Error as exc:
            raise StorageError(f"Cannot read intake receipt: {exc}") from exc

    def record_intake(
        self, source: Path, digest: str, *, document_id: int | None = None,
        error_type: str | None = None,
    ) -> None:
        """Checkpoint success or an input failure; store no exception body."""
        with self.transaction():
            if error_type is None:
                row = self.connection.execute(
                    "SELECT state, sha256 FROM documents WHERE id=?", (document_id,),
                ).fetchone()
                if row is None or row["state"] != "completed" or row["sha256"] != digest:
                    raise StorageError("An intake receipt must reference matching completed content.")
            self.connection.execute(
                "INSERT INTO intake_receipts(source_path,sha256,state,document_id,error_type,finished_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(source_path,sha256) DO UPDATE SET "
                "state=excluded.state,document_id=excluded.document_id,"
                "error_type=excluded.error_type,finished_at=excluded.finished_at "
                "WHERE intake_receipts.state != 'completed'",
                (os.path.normcase(str(source.resolve())), digest, "failed" if error_type else "completed",
                 document_id, error_type, self.clock().astimezone(timezone.utc).isoformat()),
            )

    def save_review(
        self, document_id: int, *, fields: dict, validation: dict,
        confirmed: bool, note: str, expected_revision: int,
    ) -> int:
        fields_json = json.dumps(fields, ensure_ascii=False, allow_nan=False)
        validation_json = json.dumps(validation, ensure_ascii=False, allow_nan=False)
        with self.transaction():
            row = self.connection.execute("SELECT state FROM documents WHERE id=?", (document_id,)).fetchone()
            if row is None or row["state"] != "completed":
                raise StorageError("Only completed documents can be reviewed.")
            latest = self.connection.execute("SELECT COALESCE(MAX(id),0) FROM reviews WHERE document_id=?", (document_id,)).fetchone()[0]
            if latest != expected_revision:
                raise ReviewConflictError("This document was edited in another tab. Refresh before saving.")
            cursor = self.connection.execute(
                "INSERT INTO reviews(document_id, fields_json, validation_json, confirmed, note, created_at) VALUES(?, ?, ?, ?, ?, ?)",
                (document_id, fields_json, validation_json, int(confirmed), note,
                 self.clock().astimezone(timezone.utc).isoformat()),
            )
            return cursor.lastrowid

    def review_rows(self) -> list[dict]:
        """Small summaries, excluding large page text; one row per document."""
        try:
            rows = self.connection.execute(
                "SELECT d.id, d.source_file, d.state, d.created_at, d.fields_json, d.validation_json, "
                "r.id AS review_id, r.fields_json AS review_fields, r.validation_json AS review_validation, "
                "r.confirmed, r.note FROM documents d LEFT JOIN reviews r ON r.id="
                "(SELECT MAX(id) FROM reviews WHERE document_id=d.id) ORDER BY d.id DESC"
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                original = item.pop("fields_json")
                reviewed = item.pop("review_fields")
                validation = item.pop("review_validation") or item.pop("validation_json")
                item.pop("validation_json", None)
                item["fields"] = json.loads(reviewed or original) if reviewed or original else None
                item["validation"] = json.loads(validation) if validation else None
                item["review_status"] = "confirmed" if item["confirmed"] else "draft" if item["review_id"] else "unreviewed"
                item["input_kind"] = self.input_kind
                result.append(item)
            return result
        except (sqlite3.Error, ValueError) as exc:
            raise StorageError(f"Cannot read review history: {exc}") from exc
