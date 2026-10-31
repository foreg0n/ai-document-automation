"""Exports, page density, real processing stages, and the component reference."""
import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import patch

from document_automation.config import LLMSettings
from document_automation.llm_client import LLMResult
from document_automation.pdf_text import extract_pdf_text
from document_automation.storage import StorageError, content_sha256
from document_automation.validation import validate_invoice
from document_automation.web import create_app
from document_automation.workspace import Workspace
from tools.demo_storage import main as seed_demo

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 10, 31)


class DesignWorkspaceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / "tests")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.database = self.work / "demo.sqlite3"
        with redirect_stdout(io.StringIO()):
            seed_demo(["--database", str(self.database), "--as-of", TODAY.isoformat()])
        self.workspace = Workspace(self.database, demo=True, uploads=self.work / "uploads", today=lambda: TODAY)
        self.addCleanup(self.workspace.close)

    def confirm(self, document_id=1, **changes):
        record = self.workspace.document(document_id)
        return self.workspace.save_review(document_id, {
            "fields": {**record["fields"], **changes}, "expected_revision": record["review_revision"],
            "confirmed": True, "acknowledged": True, "note": "Controlled source check",
        })

    def test_json_and_csv_export_only_the_selected_document(self):
        self.confirm(1, supplier="Corrected supplier")
        self.confirm(2)
        data = json.loads(self.workspace.export_json("confirmed", 1))
        self.assertEqual(len(data["documents"]), 1)
        row = data["documents"][0]
        self.assertEqual(row["document_id"], 1)
        self.assertEqual(row["fields"]["supplier"], "Corrected supplier")
        self.assertEqual(row["input_kind"], "reference_fixture")
        csv_rows = list(csv.DictReader(io.StringIO(self.workspace.export_csv("confirmed", 1).decode("utf-8-sig"))))
        self.assertEqual(len(csv_rows), 1)
        self.assertEqual(csv_rows[0]["document_id"], "1")

    def test_json_preserves_missing_values_and_does_not_escape_plain_strings(self):
        self.confirm(1, supplier="=Example")
        records = json.loads(self.workspace.export_json("all"))["documents"]
        self.assertEqual(len(records), 4)
        self.assertEqual(records[0]["fields"]["supplier"], "=Example")
        self.assertIsNone(records[3]["fields"]["total_amount"])
        self.assertEqual(records[3]["review_status"], "unreviewed")

    def test_export_preview_counts_unconfirmed_records_and_latest_revisions(self):
        self.assertEqual(self.workspace.export_preview()["count"], 0)
        self.confirm(1)
        preview = self.workspace.export_preview("all")
        self.assertEqual((preview["count"], preview["unreviewed"]), (4, 3))
        record = self.workspace.document(1)
        self.workspace.save_review(1, {"fields": record["fields"], "expected_revision": record["review_revision"],
                                      "confirmed": False, "note": "Draft again"})
        self.assertEqual(self.workspace.export_preview("confirmed")["count"], 0)

    def test_export_scope_and_missing_selection_are_validated(self):
        with self.assertRaises(ValueError):
            self.workspace.export_json("unknown")
        with self.assertRaises(StorageError):
            self.workspace.export_preview("all", 999)

    def test_dashboard_has_eight_rows_per_page_and_preview_is_bounded(self):
        fields = self.workspace.document(1)["fields"]
        with self.workspace.store() as store:
            for index in range(5):
                claim = store.claim(content_sha256(f"test-{index}".encode()), Path(f"new-{index}.pdf"))
                store.complete(claim, text={"pages": []}, fields=fields, metadata={"warnings": []},
                               validation=validate_invoice(fields, reference_date=TODAY).to_dict())
        page = self.workspace.list_documents()
        self.assertEqual((page["total"], page["page_size"], len(page["items"])), (9, 8, 8))
        self.assertEqual(len(self.workspace.list_documents(page=2)["items"]), 1)
        preview = self.workspace.export_preview("all")
        self.assertEqual(preview["count"], 9)
        self.assertEqual(len(preview["samples"]), 5)

    def test_routes_return_json_preview_and_self_hosted_reference(self):
        app = create_app(self.database, demo=True, uploads=self.work / "route-uploads")
        self.addCleanup(app.state.workspace.close)
        routes = {getattr(route, "path", ""): getattr(route, "endpoint", None) for route in app.routes}
        self.assertEqual(routes["/api/export/preview"](scope="all")["count"], 4)
        self.assertEqual(len(json.loads(routes["/api/export.json"](scope="all").body)["documents"]), 4)
        guide = routes["/design-system"]()
        self.assertIn(b"Component states", guide.body)
        self.assertIn(b"illustrative design examples", guide.body)

    def test_job_stages_follow_real_reader_extractor_and_validator(self):
        workspace = Workspace(self.work / "live.sqlite3", uploads=self.work / "live-uploads", exports=self.work / "exports", today=lambda: TODAY)
        self.addCleanup(workspace.close)
        fields = self.workspace.document(1)["fields"]
        source = ROOT / "data/samples/01_invoice_pln.pdf"
        workspace.jobs["stage-test"] = {"state": "queued", "source_file": source.name}
        observed = []

        def read(*args, **kwargs):
            observed.append(workspace.job("stage-test")["stage"])
            return extract_pdf_text(*args, **kwargs)

        def model(*args):
            observed.append(workspace.job("stage-test")["stage"])
            return LLMResult(fields, "controlled-model", {})

        def validate(*args, **kwargs):
            observed.append(workspace.job("stage-test")["stage"])
            return validate_invoice(*args, **kwargs)

        with patch("document_automation.workspace.load_settings", return_value=LLMSettings("test", "test-key")), patch(
            "document_automation.pipeline.extract_pdf_text", side_effect=read,
        ), patch("document_automation.pipeline.request_invoice", side_effect=model), patch(
            "document_automation.pipeline.validate_invoice", side_effect=validate,
        ):
            workspace._process_job("stage-test", source, content_sha256(source.read_bytes()))
        self.assertEqual(observed, ["reading", "extracting", "validating"])
        self.assertEqual(workspace.job("stage-test")["state"], "completed")
        self.assertEqual(workspace.job("stage-test")["stage"], "ready")


if __name__ == "__main__":
    unittest.main()
