"""Real SQLite persistence, ownership, provenance, and read-only history checks."""
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

from document_automation.cli import main
from document_automation.storage import (
    LEASE_SECONDS, DocumentBusyError, DocumentStore, StorageError, content_sha256,
)
from tools.demo_storage import main as demo_storage

ROOT = Path(__file__).resolve().parents[1]
FIELDS = {"supplier": "Example Supplier", "invoice_number": "TEST-001", "invoice_date": "2026-10-20",
          "total_amount": "1234.50", "currency": "PLN"}
VALIDATION = {"rules_version": "invoice_demo_v1", "reference_date": "2026-10-31",
              "status": "passed", "requires_review": False, "issues": []}


class StorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / "tests")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.database = self.work / "documents.sqlite3"
        self.source = self.work / "invoice.pdf"
        self.source.write_bytes(b"test document bytes")
        self.digest = content_sha256(self.source.read_bytes())

    def complete(self, store, claim, fields=None, validation=None):
        store.complete(claim, text={"text": "Invoice text", "pages": []},
                       fields=FIELDS if fields is None else fields,
                       metadata={"model": "test-model", "warnings": []},
                       validation=VALIDATION if validation is None else validation)

    def run_cli(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_committed_fields_text_and_validation_survive_reopening(self):
        with DocumentStore(self.database) as store:
            claim = store.claim(self.digest, self.source)
            self.complete(store, claim, {**FIELDS, "supplier": "Łódź / Київ"})
            store.set_export_status(claim, "written")
        with DocumentStore(self.database, readonly=True) as store:
            record = store.get(claim.document_id)
            self.assertEqual(record["fields"]["supplier"], "Łódź / Київ")
            self.assertEqual(record["fields"]["total_amount"], "1234.50")
            self.assertEqual(record["validation"], VALIDATION)
            self.assertEqual(record["text"]["text"], "Invoice text")
            self.assertEqual(record["state"], "completed")
            self.assertEqual(record["attempts"][0]["export_status"], "written")
            self.assertNotIn("owner_token", record)

    def test_renaming_content_creates_duplicate_attempt_not_another_document(self):
        with DocumentStore(self.database) as store:
            original = store.claim(self.digest, self.source)
            self.complete(store, original)
            duplicate = store.claim(self.digest, self.work / "renamed.pdf")
            self.assertTrue(duplicate.duplicate)
            self.assertEqual(original.document_id, duplicate.document_id)
            self.assertEqual(len(store.history()), 1)
            record = store.get(original.document_id)
            self.assertEqual([item["state"] for item in record["attempts"]], ["completed", "duplicate"])
            self.assertEqual(record["source_file"], "invoice.pdf")
            self.assertEqual(record["attempts"][1]["source_file"], "renamed.pdf")

    def test_different_bytes_with_same_filename_are_distinct_documents(self):
        with DocumentStore(self.database) as store:
            first = store.claim(self.digest, self.source)
            self.complete(store, first)
            second = store.claim(content_sha256(b"different bytes"), self.source)
            self.assertFalse(second.duplicate)
            self.assertNotEqual(first.document_id, second.document_id)

    def test_failed_attempt_can_be_retried_under_the_same_document_id(self):
        with DocumentStore(self.database) as store:
            first = store.claim(self.digest, self.source)
            store.fail(first, "APIError")
            second = store.claim(self.digest, self.source)
            self.assertEqual(first.document_id, second.document_id)
            self.assertFalse(second.duplicate)
            self.complete(store, second)
            record = store.get(second.document_id)
            self.assertEqual([item["state"] for item in record["attempts"]], ["failed", "completed"])
            self.assertEqual(record["attempts"][0]["error_type"], "APIError")

    def test_second_connection_cannot_claim_active_content_but_can_claim_other_content(self):
        with DocumentStore(self.database) as first, DocumentStore(self.database) as second:
            active = first.claim(self.digest, self.source)
            with self.assertRaises(DocumentBusyError):
                second.claim(self.digest, self.work / "copy.pdf")
            other = second.claim(content_sha256(b"other"), self.work / "other.pdf")
            self.complete(second, other)
            self.complete(first, active)
            self.assertEqual(len(first.history()), 2)

    def test_expired_claim_recovers_and_old_owner_cannot_overwrite_new_worker(self):
        now = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
        with DocumentStore(self.database, clock=lambda: now) as first:
            old = first.claim(self.digest, self.source)
            later = now + timedelta(seconds=LEASE_SECONDS + 1)
            with DocumentStore(self.database, clock=lambda: later) as second:
                current = second.claim(self.digest, self.source)
                with self.assertRaisesRegex(StorageError, "reservation was lost"):
                    self.complete(first, old)
                first.fail(old, "Interrupted")
                self.assertEqual(second.get(current.document_id)["state"], "processing")
                self.complete(second, current)
                record = first.get(current.document_id)
                self.assertEqual(record["state"], "completed")
                self.assertEqual(record["attempts"][0]["error_type"], "LeaseExpired")

    def test_failed_transaction_does_not_partially_save_fields(self):
        with DocumentStore(self.database) as store:
            claim = store.claim(self.digest, self.source)
            with self.assertRaises(StorageError):
                self.complete(store, claim, validation={**VALIDATION, "status": "unsupported-status"})
            record = store.get(claim.document_id)
            self.assertEqual(record["state"], "processing")
            self.assertIsNone(record["fields"])
            self.assertEqual(record["attempts"][0]["state"], "processing")
            store.fail(claim, "StorageError")

    def test_sql_metacharacters_are_stored_as_data(self):
        supplier = "Supplier'); DROP TABLE documents; --"
        with DocumentStore(self.database) as store:
            claim = store.claim(self.digest, self.source)
            self.complete(store, claim, {**FIELDS, "supplier": supplier})
            self.assertEqual(store.get(claim.document_id)["fields"]["supplier"], supplier)
            self.assertEqual(len(store.history()), 1)

    def test_reference_fixture_database_cannot_be_used_for_live_results(self):
        with DocumentStore(self.database, input_kind="reference_fixture") as store:
            claim = store.claim(self.digest, self.source)
            self.complete(store, claim)
        with self.assertRaisesRegex(StorageError, "separate databases"):
            DocumentStore(self.database)
        with DocumentStore(self.database, readonly=True) as store:
            self.assertEqual(store.get(1)["input_kind"], "reference_fixture")

    def test_readonly_history_does_not_create_a_missing_database(self):
        code, _, stderr = self.run_cli("history", "--database", str(self.database))
        self.assertEqual(code, 1)
        self.assertIn("not found", stderr)
        self.assertFalse(self.database.exists())

    def test_history_and_show_are_readable_without_llm_configuration(self):
        with DocumentStore(self.database) as store:
            claim = store.claim(self.digest, self.source)
            self.complete(store, claim)
        code, stdout, stderr = self.run_cli("history", "--database", str(self.database), "--json")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout)[0]["sha256"], self.digest)
        code, stdout, stderr = self.run_cli("show", "1", "--database", str(self.database))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout)["fields"], FIELDS)
        self.assertNotIn("owner_token", stdout)

    def test_unknown_record_and_bad_history_limit_have_actionable_errors(self):
        with DocumentStore(self.database):
            pass
        self.assertEqual(self.run_cli("show", "999", "--database", str(self.database))[0], 1)
        code, _, stderr = self.run_cli("history", "--database", str(self.database), "--limit", "0")
        self.assertEqual(code, 1)
        self.assertIn("between 1 and 1000", stderr)

    def test_foreign_database_and_future_schema_are_not_modified(self):
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("CREATE TABLE important(value TEXT)")
            connection.execute("INSERT INTO important VALUES ('keep')")
        with self.assertRaises(StorageError):
            DocumentStore(self.database)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            self.assertEqual(connection.execute("SELECT value FROM important").fetchone()[0], "keep")
        other_database = self.work / "future.sqlite3"
        with DocumentStore(other_database):
            pass
        with closing(sqlite3.connect(other_database)) as connection, connection:
            connection.execute("PRAGMA user_version=999")
        with self.assertRaisesRegex(StorageError, "schema version"):
            DocumentStore(other_database)
        with closing(sqlite3.connect(other_database)) as connection, connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 999)

    def test_reference_demo_is_repeatable_without_resetting_its_database(self):
        args = ["--database", str(self.database), "--as-of", "2026-10-31"]
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(demo_storage(args), 0)
            self.assertEqual(demo_storage(args), 0)
        self.assertIn("4 new documents, 1 duplicates reused", stdout.getvalue())
        self.assertIn("0 new documents, 5 duplicates reused", stdout.getvalue())
        with DocumentStore(self.database, readonly=True) as store:
            rows = store.history()
            self.assertEqual(len(rows), 4)
            self.assertEqual(sum(row["attempts"] for row in rows), 10)
            self.assertTrue(all(row["input_kind"] == "reference_fixture" for row in rows))


if __name__ == "__main__":
    unittest.main()
