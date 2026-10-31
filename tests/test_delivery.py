"""Project demonstration, saved-result scoring, and container server settings."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from document_automation.cli import main
from document_automation.storage import DocumentStore, StorageError
from document_automation.validation import validate_invoice
from document_automation.web import create_app
from tools.demo_project import run_demo
from tools.evaluate_results import evaluate, main as evaluate_cli

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 10, 31)


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / "tests")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        no_api = patch("document_automation.pipeline.request_invoice", side_effect=AssertionError("No external calls allowed"))
        self.api = no_api.start()
        self.addCleanup(no_api.stop)

    def test_complete_reference_demo_archives_replays_reviews_and_exports(self):
        run, report = run_demo(self.work, stable_seconds=0.001)
        self.api.assert_not_called()
        self.assertEqual(report["storage"], {"documents": 4, "attempts": 5, "intake_receipts": 5, "reviews": 2})
        self.assertEqual(report["restart"]["outcomes"], {"skipped": 5})
        self.assertEqual(report["pdf_previews_verified"], 4)
        self.assertEqual(report["csv"], {"all_rows": 4, "confirmed_rows": 1})
        self.assertEqual(report["model_accuracy"], "not_measured")
        self.assertEqual(report["review_mode"], "automated_simulation")
        self.assertEqual(json.loads((run / "report.json").read_text(encoding="utf-8")), report)
        self.assertIn("not a person's review", (run / "report.md").read_text(encoding="utf-8"))
        self.assertTrue((run / "all-results.csv").read_bytes().startswith(b"\xef\xbb\xbf"))
        with DocumentStore(run / "documents.sqlite3", readonly=True) as store:
            self.assertEqual(store.input_kind, "reference_fixture")
            incomplete = store.get(4)
            self.assertIsNone(incomplete["fields"]["total_amount"])
            self.assertIsNone(incomplete["latest_review"]["fields"]["total_amount"])
            self.assertFalse(incomplete["latest_review"]["confirmed"])

    def test_repeated_demo_uses_fresh_directory_preserving_previous_reports(self):
        first, _ = run_demo(self.work, stable_seconds=0.001)
        original = (first / "report.json").read_bytes()
        second, _ = run_demo(self.work, stable_seconds=0.001)
        self.assertNotEqual(first, second)
        self.assertEqual((first / "report.json").read_bytes(), original)
        self.assertTrue((second / "report.json").is_file())

    def test_health_checks_persistent_database_without_opening_a_session(self):
        database = self.work / "workspace.sqlite3"
        app = create_app(database, uploads=self.work / "uploads")
        self.addCleanup(app.state.workspace.close)
        health = next(route.endpoint for route in app.routes if getattr(route, "path", "") == "/healthz")
        self.assertEqual(health(), {"status": "ok"})
        database.unlink()
        with self.assertRaises(StorageError):
            health()
        self.assertFalse(database.exists())

    def test_serve_keeps_local_default_and_accepts_container_host_and_export_path(self):
        for extra, host, exports in (([], "127.0.0.1", Path("output/invoices")),
                                     (["--host", "0.0.0.0", "--output-dir", str(self.work)], "0.0.0.0", self.work)):
            with self.subTest(host=host), patch("uvicorn.run") as server, patch(
                "document_automation.web.create_app", return_value=Mock(),
            ) as factory, redirect_stdout(io.StringIO()):
                self.assertEqual(main(["serve", *extra]), 0)
                self.assertEqual(server.call_args.kwargs["host"], host)
                self.assertFalse(server.call_args.kwargs["proxy_headers"])
                self.assertEqual(factory.call_args.kwargs["exports"], exports)

    def test_serve_rejects_unexpected_listen_address(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stopped:
            main(["serve", "--host", "example.com"])
        self.assertEqual(stopped.exception.code, 2)


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / "tests")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.database = self.work / "results.sqlite3"
        manifest = json.loads((ROOT / "data/ground_truth.json").read_text(encoding="utf-8"))
        self.cases = manifest["cases"][:4]

    def save_cases(self, cases=None, overrides=None):
        with DocumentStore(self.database) as store:
            for case in self.cases if cases is None else cases:
                fields = {**case["expected_fields"], **(overrides or {}).get(case["file"], {})}
                claim = store.claim(case["sha256"], ROOT / "data/samples" / case["file"])
                store.complete(claim, text={}, fields=fields,
                               metadata={"model": "controlled-test-model", "warnings": []},
                               validation=validate_invoice(fields, reference_date=TODAY).to_dict())

    def test_exact_saved_fields_include_null_and_count_unique_hashes_once(self):
        self.save_cases()
        report = evaluate(self.database)
        self.assertEqual(report["expected_documents"], 4)
        self.assertEqual(report["expected_fields"], 20)
        self.assertEqual(report["available_documents"], 4)
        self.assertEqual(report["field_accuracy"], 1)
        self.assertEqual(report["document_accuracy"], 1)
        self.assertTrue(report["cases"][3]["field_matches"]["total_amount"])

    def test_wrong_format_counts_as_mismatch_without_normalizing(self):
        self.save_cases(overrides={self.cases[0]["file"]: {"total_amount": "1,234.50"}})
        report = evaluate(self.database)
        self.assertEqual(report["correct_fields"], 19)
        self.assertEqual(report["field_accuracy"], 0.95)
        self.assertEqual(report["exact_match_documents"], 3)

    def test_manual_correction_is_not_used_to_inflate_original_accuracy(self):
        self.save_cases(overrides={self.cases[0]["file"]: {"supplier": "Wrong supplier"}})
        with DocumentStore(self.database) as store:
            fields = self.cases[0]["expected_fields"]
            store.save_review(1, fields=fields, validation=validate_invoice(fields, reference_date=TODAY).to_dict(),
                              confirmed=True, note="Controlled correction", expected_revision=0)
        report = evaluate(self.database)
        self.assertFalse(report["cases"][0]["field_matches"]["supplier"])
        self.assertEqual(report["correct_fields"], 19)

    def test_missing_and_failed_results_count_as_incorrect(self):
        self.save_cases(cases=self.cases[:1])
        with DocumentStore(self.database) as store:
            case = self.cases[1]
            claim = store.claim(case["sha256"], ROOT / "data/samples" / case["file"])
            store.fail(claim, "ControlledFailure")
        report = evaluate(self.database)
        self.assertEqual(report["available_documents"], 1)
        self.assertEqual(report["correct_fields"], 5)
        self.assertEqual(report["field_accuracy"], 0.25)
        self.assertEqual([item["state"] for item in report["cases"]], ["completed", "failed", "missing", "missing"])

    def test_fixture_database_cannot_report_model_accuracy(self):
        with DocumentStore(self.database, input_kind="reference_fixture"):
            pass
        with self.assertRaisesRegex(ValueError, "cannot measure model accuracy"):
            evaluate(self.database)

    def test_evaluation_does_not_create_a_missing_database(self):
        with self.assertRaises(StorageError):
            evaluate(self.database)
        self.assertFalse(self.database.exists())

    def test_missing_field_is_not_equivalent_to_explicit_null(self):
        self.save_cases()
        with DocumentStore(self.database) as store:
            original = self.cases[3]["expected_fields"].copy()
            original.pop("total_amount")
            with store.transaction():
                store.connection.execute("UPDATE documents SET fields_json=? WHERE id=4", (json.dumps(original),))
        self.assertFalse(evaluate(self.database)["cases"][3]["field_matches"]["total_amount"])

    def test_malformed_completed_fields_do_not_produce_an_accuracy_score(self):
        self.save_cases(cases=self.cases[:1])
        with DocumentStore(self.database) as store:
            with store.transaction():
                store.connection.execute("UPDATE documents SET fields_json='null' WHERE id=1")
        with self.assertRaisesRegex(ValueError, "malformed saved fields"):
            evaluate(self.database)

    def test_evaluation_cli_reports_mismatches_and_preserves_existing_output(self):
        self.save_cases(cases=self.cases[:1])
        output = self.work / "evaluation.json"
        with redirect_stdout(io.StringIO()):
            code = evaluate_cli(["--database", str(self.database), "--output", str(output)])
        self.assertEqual(code, 3)
        original = output.read_bytes()
        with redirect_stderr(io.StringIO()):
            self.assertEqual(evaluate_cli(["--database", str(self.database), "--output", str(output)]), 1)
        self.assertEqual(output.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
