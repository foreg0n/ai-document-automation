"""Stable incoming files, durable receipts, and real pipeline integration."""
import io
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack, closing, redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from document_automation.cli import main
from document_automation.config import ConfigurationError, LLMSettings
from document_automation.intake import (
    MAX_INTAKE_BYTES, FolderIntake, SourceChanged, read_stable_snapshot, signature,
)
from document_automation.invoice import InvoiceFormatError
from document_automation.llm_client import APIError, LLMResult
from document_automation.storage import (
    APPLICATION_ID, REVIEWS_SCHEMA, SCHEMA, DocumentStore, StorageError, content_sha256,
)
from document_automation.validation import validate_invoice

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "data/samples"
TODAY = date(2026, 10, 31)
FIELDS = {"supplier": "Test Supplier", "invoice_number": "TEST-001", "invoice_date": "2026-10-20",
          "total_amount": "1234.50", "currency": "PLN"}
SETTINGS = LLMSettings(model="controlled-model", api_key="controlled-key")


class IntakeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / "tests")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.incoming = self.work / "incoming"
        self.incoming.mkdir()
        self.database = self.work / "documents.sqlite3"
        self.store = DocumentStore(self.database)
        self.addCleanup(self.store.close)
        self.now = 0.0
        self.loader = Mock(return_value=SETTINGS)
        api_patch = patch("document_automation.pipeline.request_invoice",
                          return_value=LLMResult(FIELDS, "controlled-model", {}))
        self.api = api_patch.start()
        self.addCleanup(api_patch.stop)

    def watcher(self, **kwargs):
        options = {"archive": self.work / "archive", "exports": self.work / "exports",
                   "clock": lambda: self.now, "today": lambda: TODAY,
                   "settings_loader": self.loader}
        options.update(kwargs)
        return FolderIntake(self.incoming, self.store, **options)

    def add_pdf(self, name="invoice.pdf", sample="01_invoice_pln.pdf"):
        path = self.incoming / name
        shutil.copyfile(SAMPLES / sample, path)
        return path

    def ready(self, watcher):
        self.assertEqual(list(watcher.poll()), [])
        self.now += watcher.stable_seconds
        return list(watcher.poll())

    def cli(self, *args, controlled_time=True):
        stdout, stderr = io.StringIO(), io.StringIO()
        options = ["watch", str(self.incoming), "--database", str(self.database),
                   "--archive-dir", str(self.work / "archive"),
                   "--output-dir", str(self.work / "exports"), "--stable-seconds", "0.001"]
        with ExitStack() as stack, redirect_stdout(stdout), redirect_stderr(stderr), patch(
            "document_automation.cli.load_settings", self.loader,
        ):
            if controlled_time:
                stack.enter_context(patch("document_automation.intake.FolderIntake", side_effect=lambda *a, **k: FolderIntake(*a, **k, clock=lambda: self.now)))
                stack.enter_context(patch("document_automation.cli.time.monotonic", side_effect=lambda: self.now))
                stack.enter_context(patch("document_automation.cli.time.sleep", side_effect=lambda seconds: setattr(self, "now", self.now + seconds)))
            code = main(options + list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_waits_before_processing_and_uses_managed_snapshot(self):
        source = self.add_pdf()
        original = source.read_bytes()
        watcher = self.watcher()
        self.assertEqual(list(watcher.poll()), [])
        self.assertEqual(watcher.pending, 1)
        self.api.assert_not_called()
        self.now = 2
        event, = watcher.poll()
        self.assertEqual(event.status, "processed")
        record = self.store.get(event.result.document_id)
        archived = Path(record["source_path"])
        self.assertNotEqual(archived, source)
        self.assertEqual(archived.read_bytes(), original)
        self.assertEqual(source.read_bytes(), original)
        source.unlink()
        self.assertEqual(archived.read_bytes(), original)
        self.assertEqual(len(event.result.outputs), 3)
        self.assertTrue(all(path.exists() for path in event.result.outputs))
        self.assertEqual(self.store.intake_receipt(source, record["sha256"])["state"], "completed")

    def test_changes_restart_quiet_period(self):
        source = self.add_pdf()
        watcher = self.watcher()
        list(watcher.poll())
        self.now = 1
        source.write_bytes(source.read_bytes() + b"\n% producer still copying\n")
        self.assertEqual(list(watcher.poll()), [])
        self.now = 2
        self.assertEqual(list(watcher.poll()), [])
        self.api.assert_not_called()
        self.now = 3
        self.assertEqual(next(watcher.poll()).status, "processed")

    def test_read_detects_change_during_snapshot_and_defers_without_model_call(self):
        source = self.add_pdf()
        watcher = self.watcher()
        list(watcher.poll())
        self.now = 2
        original_fstat = os.fstat
        calls = 0

        def change_on_second_stat(descriptor):
            nonlocal calls
            calls += 1
            if calls == 2:
                source.write_bytes(source.read_bytes() + b"\n% changed\n")
            return original_fstat(descriptor)

        with patch("document_automation.intake.os.fstat", side_effect=change_on_second_stat):
            event, = watcher.poll()
        self.assertEqual(event.status, "deferred")
        self.assertEqual(watcher.pending, 1)
        self.api.assert_not_called()
        self.assertFalse((self.work / "archive").exists())

    def test_changed_metadata_before_read_is_rejected(self):
        source = self.add_pdf()
        expected = signature(source.stat())
        source.write_bytes(source.read_bytes() + b"\n% new\n")
        with self.assertRaises(SourceChanged):
            read_stable_snapshot(source, expected)

    def test_five_samples_make_four_model_calls_and_one_duplicate(self):
        manifest = json.loads((ROOT / "data/ground_truth.json").read_text(encoding="utf-8"))
        fields = [case["expected_fields"] for case in manifest["cases"][:4]]
        self.api.side_effect = [LLMResult(item, "controlled-model", {}) for item in fields]
        for source in SAMPLES.glob("*.pdf"):
            self.add_pdf(source.name, source.name)
        events = self.ready(self.watcher())
        self.assertEqual([event.status for event in events], ["processed"] * 4 + ["duplicate"])
        self.assertEqual(self.api.call_count, 4)
        self.assertEqual(len(self.store.history()), 4)
        self.assertTrue(events[3].result.report.requires_review)
        self.assertEqual(events[0].result.document_id, events[4].result.document_id)

    def test_restart_skips_completed_receipts_without_credentials_or_new_attempts(self):
        self.add_pdf()
        self.ready(self.watcher())
        self.store.close()
        self.store = DocumentStore(self.database)
        self.addCleanup(self.store.close)
        self.loader.side_effect = AssertionError("Must not load settings for a receipt")
        events = self.ready(self.watcher())
        self.assertEqual(events[0].status, "skipped")
        self.assertEqual(self.api.call_count, 1)
        self.assertEqual(len(self.store.get(1)["attempts"]), 1)

    @unittest.skipUnless(os.name == "nt", "Windows path case handling")
    def test_receipt_path_is_case_insensitive_on_windows(self):
        source = self.add_pdf()
        self.ready(self.watcher())
        digest = content_sha256(source.read_bytes())
        alias = Path(str(source).swapcase())
        self.assertEqual(self.store.intake_receipt(alias, digest)["document_id"], 1)

    def test_unchanged_files_do_not_repeat_on_every_poll(self):
        self.add_pdf()
        watcher = self.watcher()
        self.ready(watcher)
        for _ in range(5):
            self.now += 3
            self.assertEqual(list(watcher.poll()), [])
        self.assertEqual(len(self.store.get(1)["attempts"]), 1)

    def test_new_bytes_under_same_name_use_separate_archive_and_exports(self):
        source = self.add_pdf()
        watcher = self.watcher()
        first, = self.ready(watcher)
        source.write_bytes(source.read_bytes() + b"\n% another version\n")
        second, = self.ready(watcher)
        self.assertNotEqual(first.result.document_id, second.result.document_id)
        self.assertNotEqual(first.result.outputs[0].parent, second.result.outputs[0].parent)
        self.assertEqual(len(self.store.history()), 2)
        self.assertEqual(self.api.call_count, 2)

    def test_renamed_content_reuses_pipeline_cache_without_settings(self):
        original = self.add_pdf()
        watcher = self.watcher()
        self.ready(watcher)
        self.loader.side_effect = AssertionError("Duplicate must not load credentials")
        shutil.copyfile(original, self.incoming / "renamed.PDF")
        event, = self.ready(watcher)
        self.assertEqual(event.status, "duplicate")
        self.assertEqual(self.api.call_count, 1)
        self.assertEqual(len(self.store.get(1)["attempts"]), 2)

    def test_input_failure_is_checkpointed_until_explicit_retry(self):
        source = self.add_pdf()
        self.api.side_effect = InvoiceFormatError("Invalid controlled response")
        event, = self.ready(self.watcher())
        self.assertEqual(event.status, "failed")
        digest = content_sha256(source.read_bytes())
        self.assertEqual(self.store.intake_receipt(source, digest)["error_type"], "InvoiceFormatError")
        skipped, = self.ready(self.watcher())
        self.assertEqual(skipped.status, "skipped_failed")
        self.assertEqual(self.api.call_count, 1)
        self.api.side_effect = None
        retried, = self.ready(self.watcher(retry_failed=True))
        self.assertEqual(retried.status, "processed")
        self.assertEqual(self.api.call_count, 2)
        self.assertEqual(self.store.intake_receipt(source, digest)["state"], "completed")

    def test_bad_pdf_does_not_prevent_later_valid_input(self):
        (self.incoming / "a-broken.pdf").write_bytes(b"not a PDF")
        self.add_pdf("b-valid.pdf")
        events = self.ready(self.watcher())
        self.assertEqual([item.status for item in events], ["failed", "processed"])
        self.assertEqual(self.api.call_count, 1)

    def test_service_failure_stops_before_next_input_without_receipt(self):
        source = self.add_pdf("a.pdf")
        self.add_pdf("b.pdf", "02_invoice_eur.pdf")
        self.api.side_effect = APIError("Controlled provider failure")
        code, _, stderr = self.cli("--once")
        self.assertEqual(code, 1)
        self.assertIn("no automatic retries", stderr)
        self.assertEqual(self.api.call_count, 1)
        self.assertIsNone(self.store.intake_receipt(source, content_sha256(source.read_bytes())))
        self.assertEqual(len(self.store.history()), 1)

    def test_configuration_failure_does_not_loop(self):
        self.add_pdf()
        self.loader.side_effect = ConfigurationError("Configure a model")
        code, _, stderr = self.cli("--once")
        self.assertEqual(code, 1)
        self.assertIn("Configure a model", stderr)
        self.api.assert_not_called()

    def test_active_claim_is_deferred_and_then_reuses_committed_result(self):
        source = self.add_pdf()
        digest = content_sha256(source.read_bytes())
        claim = self.store.claim(digest, source)
        watcher = self.watcher()
        event, = self.ready(watcher)
        self.assertEqual(event.status, "deferred")
        self.api.assert_not_called()
        self.store.complete(claim, text={"text": "controlled"}, fields=FIELDS,
                            metadata={"warnings": []},
                            validation=validate_invoice(FIELDS, reference_date=TODAY).to_dict())
        self.now += 2
        event, = watcher.poll()
        self.assertEqual(event.status, "duplicate")
        self.api.assert_not_called()

    def test_export_retry_uses_saved_result_without_another_model_call(self):
        source = self.add_pdf()
        with patch("document_automation.pipeline._write_outputs", side_effect=OSError("Disk failure")):
            event, = self.ready(self.watcher())
        self.assertEqual(event.status, "failed")
        self.assertEqual(self.store.get(1)["state"], "completed")
        self.loader.side_effect = AssertionError("No credentials on cached export retry")
        retried, = self.ready(self.watcher(retry_failed=True))
        self.assertEqual(retried.status, "duplicate")
        self.assertTrue(all(path.is_file() for path in retried.result.outputs))
        self.assertEqual(self.api.call_count, 1)
        self.assertEqual(self.store.intake_receipt(source, content_sha256(source.read_bytes()))["state"], "completed")

    def test_only_direct_regular_pdf_files_are_selected(self):
        (self.incoming / "notes.txt").write_text("ignored")
        (self.incoming / "directory.pdf").mkdir()
        shutil.copyfile(SAMPLES / "01_invoice_pln.pdf", self.incoming / "directory.pdf/nested.pdf")
        self.add_pdf("valid.PDF")
        events = self.ready(self.watcher())
        self.assertEqual([event.source.name for event in events], ["valid.PDF"])

    def test_oversized_input_is_rejected_before_model_call(self):
        source = self.incoming / "large.pdf"
        with source.open("wb") as stream:
            stream.truncate(MAX_INTAKE_BYTES + 1)
        event, = self.ready(self.watcher())
        self.assertEqual(event.status, "failed")
        self.assertIn("20 MB", event.message)
        self.api.assert_not_called()

    def test_archive_corruption_is_reported_without_overwrite_or_api_call(self):
        source = self.add_pdf()
        digest = content_sha256(source.read_bytes())
        archive = self.work / "archive" / digest / source.name
        archive.parent.mkdir(parents=True)
        archive.write_bytes(b"modified archive")
        event, = self.ready(self.watcher())
        self.assertEqual(event.status, "failed")
        self.assertEqual(archive.read_bytes(), b"modified archive")
        self.api.assert_not_called()

    def test_overlapping_input_and_managed_directories_are_rejected(self):
        for archive in (self.incoming, self.incoming / "archive", self.incoming.parent):
            with self.subTest(archive=archive), self.assertRaisesRegex(ValueError, "overlap"):
                self.watcher(archive=archive)

    def test_live_intake_rejects_reference_database(self):
        with DocumentStore(self.work / "fixture.sqlite3", input_kind="reference_fixture") as fixture:
            with self.assertRaisesRegex(StorageError, "separate LLM"):
                FolderIntake(self.incoming, fixture)

    def test_cli_once_exit_codes_for_success_review_empty_and_pending(self):
        self.assertEqual(self.cli("--once")[0], 0)
        self.add_pdf()
        self.api.return_value = LLMResult({**FIELDS, "total_amount": None}, "controlled-model", {})
        code, stdout, stderr = self.cli("--once")
        self.assertEqual(code, 3, stderr)
        self.assertIn("1 need review", stdout)
        self.assertEqual(self.cli("--once")[0], 0)  # Receipt: no new processing.
        self.add_pdf("new.pdf", "02_invoice_eur.pdf")
        def changing_new_file(source, expected):
            if source.name == "new.pdf":
                raise SourceChanged
            return read_stable_snapshot(source, expected)

        with patch("document_automation.intake.read_stable_snapshot", side_effect=changing_new_file):
            code, stdout, _ = self.cli("--once")
        self.assertEqual(code, 4)
        self.assertIn("1 pending", stdout)

    def test_continuous_mode_detects_new_file_then_stops_on_ctrl_c(self):
        real_factory = FolderIntake
        sleeps = 0

        def factory(*args, **kwargs):
            return real_factory(*args, **kwargs, clock=lambda: self.now)

        def tick(seconds):
            nonlocal sleeps
            sleeps += 1
            self.now += seconds
            if sleeps == 1:
                self.add_pdf()
            if sleeps == 3:
                raise KeyboardInterrupt

        with patch("document_automation.intake.FolderIntake", side_effect=factory), patch(
            "document_automation.cli.time.sleep", side_effect=tick,
        ):
            code, stdout, stderr = self.cli(controlled_time=False)
        self.assertEqual(code, 0, stderr)
        self.assertIn("PROCESSED invoice.pdf", stdout)
        self.assertIn("Intake stopped", stdout)
        self.assertEqual(self.api.call_count, 1)

    def test_invalid_timing_options_fail_without_model_call(self):
        for name, value in (("--interval", "0"), ("--interval", "nan"),
                            ("--stable-seconds", "-1"), ("--stable-seconds", "inf")):
            with self.subTest(name=name, value=value):
                code, _, _ = self.cli("--once", name, value)
                self.assertEqual(code, 1)
        self.api.assert_not_called()

    def test_success_receipt_cannot_reference_wrong_content_or_failed_document(self):
        source = self.add_pdf()
        digest = content_sha256(source.read_bytes())
        claim = self.store.claim(digest, source)
        with self.assertRaises(StorageError):
            self.store.record_intake(source, digest, document_id=claim.document_id)
        self.store.complete(claim, text={}, fields=FIELDS, metadata={"warnings": []},
                            validation=validate_invoice(FIELDS, reference_date=TODAY).to_dict())
        with self.assertRaises(StorageError):
            self.store.record_intake(source, "f" * 64, document_id=claim.document_id)
        self.store.record_intake(source, digest, document_id=claim.document_id)
        self.store.record_intake(source, digest, error_type="ControlledLateFailure")
        self.assertEqual(self.store.intake_receipt(source, digest)["state"], "completed")


class IntakeMigrationTests(unittest.TestCase):
    def test_v2_reviews_and_original_data_survive_v3_upgrade(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            path = Path(temporary) / "v2.sqlite3"
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.executescript(SCHEMA + REVIEWS_SCHEMA + f"PRAGMA application_id={APPLICATION_ID}; PRAGMA user_version=2;")
                connection.execute("INSERT INTO store_info VALUES(1,'llm')")
                connection.execute("INSERT INTO documents(sha256,source_file,source_path,state,created_at,updated_at) VALUES(?,'original.pdf','original','completed','original-time','original-time')", ("a" * 64,))
                connection.execute("INSERT INTO reviews(document_id,fields_json,validation_json,confirmed,note,created_at) VALUES(1,?,?,1,'original review','original-time')",
                                   (json.dumps(FIELDS), json.dumps(validate_invoice(FIELDS, reference_date=TODAY).to_dict())))
            with DocumentStore(path, readonly=True) as store:
                self.assertEqual(store.schema_version, 2)
                self.assertIsNone(store.intake_receipt(Path("original.pdf"), "a" * 64))
            with DocumentStore(path) as store:
                self.assertEqual(store.schema_version, 3)
                record = store.get(1)
                self.assertEqual(record["created_at"], "original-time")
                self.assertEqual(record["latest_review"]["note"], "original review")
                self.assertEqual(record["latest_review"]["fields"], FIELDS)
                self.assertTrue(record["latest_review"]["confirmed"])

    def test_rejected_fixture_database_is_not_migrated(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            path = Path(temporary) / "v2-fixture.sqlite3"
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.executescript(SCHEMA + REVIEWS_SCHEMA + f"PRAGMA application_id={APPLICATION_ID}; PRAGMA user_version=2;")
                connection.execute("INSERT INTO store_info VALUES(1,'reference_fixture')")
            with self.assertRaises(StorageError):
                DocumentStore(path)
            with DocumentStore(path, readonly=True) as store:
                self.assertEqual(store.schema_version, 2)


if __name__ == "__main__":
    unittest.main()
