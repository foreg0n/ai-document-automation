"""Local HTTP/review/CSV behavior with real SQLite and controlled model calls."""
import csv
import io
import json
import re
import sqlite3
import socket
import tempfile
import time
import unittest
from contextlib import closing, redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import JSONResponse

from document_automation.llm_client import LLMResult
from document_automation.storage import APPLICATION_ID, SCHEMA, DocumentStore
from document_automation.web import create_app
from document_automation.workspace import MAX_UPLOAD_BYTES, safe_filename
from document_automation.workspace import Workspace
from tools.demo_storage import main as seed_demo

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "data" / "samples"
TODAY = date(2026, 10, 31)


def local_tcp_available():
    # Windows asyncio creates its internal wake-up pair over loopback TCP.
    # Check with a bounded handshake, rather than hanging TestClient startup.
    try:
        with socket.socket() as listener, socket.socket() as client:
            listener.settimeout(.2)
            client.settimeout(.2)
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            client.connect(listener.getsockname())
            accepted, _ = listener.accept()
            accepted.close()
        return True
    except OSError:
        return False


LOCAL_TCP_AVAILABLE = local_tcp_available()


@unittest.skipUnless(LOCAL_TCP_AVAILABLE, "Local TCP/socketpair is blocked by this execution environment")
class WebWorkspaceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / "tests")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.database = self.work / "demo.sqlite3"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(seed_demo(["--database", str(self.database), "--as-of", TODAY.isoformat()]), 0)
        self.app = create_app(self.database, demo=True, uploads=self.work / "uploads", today=lambda: TODAY)
        self.client = TestClient(self.app, base_url="http://127.0.0.1")
        self.client.__enter__()
        self.addCleanup(lambda: self.client.__exit__(None, None, None))
        root = self.client.get("/")
        token = re.search(r'name="csrf-token" content="([^"]+)"', root.text).group(1)
        self.client.headers["X-CSRF-Token"] = token

    def doc(self, document_id=1):
        response = self.client.get(f"/api/documents/{document_id}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def save(self, fields=None, *, document_id=1, confirmed=False, note="", revision=None, acknowledged=True):
        document = self.doc(document_id)
        return self.client.post(f"/api/documents/{document_id}/review", json={
            "fields": fields if fields is not None else document["fields"],
            "expected_revision": document["review_revision"] if revision is None else revision,
            "confirmed": confirmed, "acknowledged": acknowledged, "note": note,
        })

    def csv_rows(self, scope="confirmed"):
        response = self.client.get("/api/export.csv", params={"scope": scope})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.content.startswith(b"\xef\xbb\xbf"))
        return list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))

    def wait_job(self, job_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = self.client.get("/api/jobs/" + job_id).json()
            if job["state"] in {"completed", "failed"}:
                return job
            time.sleep(.01)
        self.fail("Local processing job did not finish")

    def test_index_assets_and_real_document_summary(self):
        index = self.client.get("/")
        self.assertIn("Invoice workspace", index.text)
        self.assertIn("httponly", index.headers["set-cookie"].lower())
        self.assertIn("default-src 'self'", index.headers["content-security-policy"])
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)
        self.assertEqual(self.client.get("/static/styles.css").status_code, 200)
        data = self.client.get("/api/documents").json()
        self.assertEqual(data["counts"], {"total": 4, "pending": 4, "confirmed": 0, "needs_review": 1})
        self.assertTrue(data["demo"])
        self.assertNotIn("source_path", self.doc())
        self.assertIn("Northstar", self.doc()["pages"][0]["text"])

    def test_search_filters_and_empty_state(self):
        result = self.client.get("/api/documents", params={"q": "Cedar"}).json()
        self.assertEqual(result["total"], 1)
        self.assertEqual(self.client.get("/api/documents", params={"status": "needs_review"}).json()["total"], 1)
        self.assertEqual(self.client.get("/api/documents", params={"q": "does-not-exist"}).json()["items"], [])
        self.assertEqual(self.client.get("/api/documents", params={"page": 0}).status_code, 422)
        self.assertEqual(self.client.get("/api/documents", params={"status": "invalid"}).status_code, 400)

    def test_draft_keeps_original_extraction_and_audit_history(self):
        document = self.doc(4)
        self.assertIsNone(document["fields"]["total_amount"])
        fields = {**document["fields"], "total_amount": "100.00"}
        response = self.save(fields, document_id=4, note="Controlled manual edit")
        self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()
        self.assertEqual(saved["review_status"], "draft")
        self.assertEqual(saved["fields"]["total_amount"], "100.00")
        self.assertIsNone(saved["original_fields"]["total_amount"])
        self.assertEqual(saved["validation"]["status"], "passed")
        with DocumentStore(self.database, readonly=True) as store:
            self.assertIsNone(store.get(4)["fields"]["total_amount"])
            self.assertEqual(len(store.get(4)["reviews"]), 1)

    def test_confirmation_and_later_draft_control_export_of_latest_revision(self):
        self.assertEqual(self.csv_rows(), [])
        response = self.save(confirmed=True)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.doc()["review_status"], "confirmed")
        self.assertEqual(len(self.csv_rows()), 1)
        self.assertEqual(self.save(note="New draft after confirmation").status_code, 200)
        self.assertEqual(self.csv_rows(), [])
        self.assertEqual(len(self.doc()["reviews"]), 2)

    def test_confirmation_requires_acknowledgement_and_note_for_unresolved_checks(self):
        self.assertEqual(self.save(confirmed=True, acknowledged=False).status_code, 400)
        self.assertEqual(self.save(document_id=4, confirmed=True).status_code, 400)
        response = self.save(document_id=4, confirmed=True, note="The source has no stated total; retained as missing.")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["review_status"], "confirmed")
        self.assertEqual(response.json()["validation"]["status"], "needs_review")
        row = self.csv_rows()[0]
        self.assertEqual(row["total_amount"], "")
        self.assertEqual(row["validation_status"], "needs_review")
        self.assertEqual(row["input_kind"], "reference_fixture")

    def test_stale_tab_cannot_overwrite_a_newer_review(self):
        self.assertEqual(self.save(revision=0).status_code, 200)
        response = self.save(revision=0, note="Outdated tab")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(len(self.doc()["reviews"]), 1)

    def test_csv_has_unique_documents_corrected_values_and_formula_escaping(self):
        fields = {**self.doc()["fields"], "supplier": '=HYPERLINK("https://example.invalid")'}
        self.assertEqual(self.save(fields, confirmed=True).status_code, 200)
        rows = self.csv_rows("all")
        self.assertEqual(len(rows), 4)
        self.assertEqual(len({row["document_id"] for row in rows}), 4)
        self.assertEqual(rows[0]["supplier"], "'" + fields["supplier"])
        self.assertEqual(rows[0]["total_amount"], "1234.50")
        self.assertEqual(self.client.get("/api/export.csv?scope=bad").status_code, 400)

    def test_api_rejects_missing_session_bad_csrf_foreign_origin_and_host(self):
        self.assertEqual(self.client.post("/api/documents/1/review", json={}, headers={"X-CSRF-Token": "bad"}).status_code, 403)
        self.assertEqual(self.client.post("/api/documents/1/review", json={}, headers={"Origin": "https://example.invalid"}).status_code, 403)
        self.assertEqual(self.client.get("/api/documents", headers={"Host": "example.invalid"}).status_code, 403)
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/api/documents").status_code, 403)

    def test_bad_review_shapes_and_types_are_not_saved(self):
        for payload in ({}, [], {"fields": self.doc()["fields"], "expected_revision": True},
                        {"fields": {**self.doc()["fields"], "total_amount": 12.34}, "expected_revision": 0}):
            with self.subTest(payload=payload):
                response = self.client.post("/api/documents/1/review", json=payload)
                self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.doc()["reviews"], [])

    def test_pdf_snapshot_matches_source_and_changed_path_is_not_served(self):
        response = self.client.get("/api/documents/1/pdf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, (SAMPLES / "01_invoice_pln.pdf").read_bytes())
        altered = self.work / "altered.pdf"
        altered.write_bytes(b"%PDF-1.4 changed")
        with DocumentStore(self.database, input_kind="reference_fixture") as store:
            with store.transaction():
                store.connection.execute("UPDATE documents SET source_path=? WHERE id=1", (str(altered),))
        response = self.client.get("/api/documents/1/pdf")
        self.assertEqual(response.status_code, 400)
        self.assertIn("changed", response.json()["error"])
        self.assertTrue(self.doc()["pages"])

    def test_demo_upload_of_duplicate_reuses_id_and_keeps_review(self):
        self.assertEqual(self.save(confirmed=True).status_code, 200)
        response = self.client.post("/api/uploads?filename=copy.pdf", content=(SAMPLES / "05_invoice_duplicate.pdf").read_bytes())
        self.assertEqual(response.status_code, 202, response.text)
        job = self.wait_job(response.json()["id"])
        self.assertEqual(job["state"], "completed", job)
        self.assertTrue(job["duplicate"])
        self.assertEqual(job["document_id"], 1)
        self.assertEqual(self.doc()["review_status"], "confirmed")

    def test_invalid_pdf_large_upload_and_unknown_demo_content_are_rejected(self):
        for content, status in ((b"not a PDF", 400), (b"%PDF-1.4 new", 400), (b"x" * (MAX_UPLOAD_BYTES + 1), 413)):
            with self.subTest(size=len(content)):
                response = self.client.post("/api/uploads?filename=invoice.pdf", content=content)
                self.assertEqual(response.status_code, status, response.text)
        self.assertEqual(self.app.state.workspace.jobs, {})

    def test_upload_path_names_are_sanitized_and_queue_is_bounded(self):
        self.assertEqual(safe_filename("../../file.pdf"), "file.pdf")
        self.assertEqual(safe_filename("C:\\private\\CON.pdf"), "_CON.pdf")
        with self.app.state.workspace.lock:
            self.app.state.workspace.jobs = {str(i): {"state": "queued"} for i in range(10)}
        response = self.client.post("/api/uploads?filename=file.pdf", content=(SAMPLES / "01_invoice_pln.pdf").read_bytes())
        self.assertEqual(response.status_code, 400)
        self.assertIn("queue", response.json()["error"])


class WebLiveAndMigrationTests(unittest.TestCase):
    @unittest.skipUnless(LOCAL_TCP_AVAILABLE, "Local TCP/socketpair is blocked by this execution environment")
    def test_live_upload_archives_pdf_processes_and_preserves_original_name(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            work = Path(temporary)
            env = work / ".env"
            env.write_text("LLM_MODEL=test-model\nLLM_API_KEY=test-key\n", encoding="utf-8")
            fields = {"supplier": "Test Supplier", "invoice_number": "TEST-001", "invoice_date": "2026-10-20",
                      "total_amount": "1234.50", "currency": "PLN"}
            app = create_app(work / "live.sqlite3", uploads=work / "uploads", exports=work / "exports", env_file=env, today=lambda: TODAY)
            with TestClient(app, base_url="http://127.0.0.1") as client:
                root = client.get("/")
                client.headers["X-CSRF-Token"] = re.search(r'name="csrf-token" content="([^"]+)"', root.text).group(1)
                with patch("document_automation.pipeline.request_invoice", return_value=LLMResult(fields, "test-model", {})) as model:
                    response = client.post("/api/uploads?filename=uploaded-invoice.pdf", content=(SAMPLES / "01_invoice_pln.pdf").read_bytes())
                    self.assertEqual(response.status_code, 202, response.text)
                    for _ in range(200):
                        job = client.get("/api/jobs/" + response.json()["id"]).json()
                        if job["state"] in {"completed", "failed"}:
                            break
                        time.sleep(.01)
                    self.assertEqual(job["state"], "completed", job)
                    model.assert_called_once()
                document = client.get("/api/documents/1").json()
                self.assertEqual(document["source_file"], "uploaded-invoice.pdf")
                self.assertEqual(document["input_kind"], "llm")
                self.assertEqual(len(list((work / "uploads").glob("*/*.pdf"))), 1)
                self.assertEqual(client.get("/api/documents/1/pdf").status_code, 200)

    def test_v1_database_migrates_without_replacing_existing_rows(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as temporary:
            path = Path(temporary) / "v1.sqlite3"
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.executescript(SCHEMA + f"PRAGMA application_id={APPLICATION_ID}; PRAGMA user_version=1;")
                connection.execute("INSERT INTO store_info VALUES(1,'llm')")
                connection.execute("INSERT INTO documents(sha256,source_file,source_path,state,created_at,updated_at) VALUES(?,?,'local','failed','original-time','original-time')", ("a" * 64, "existing.pdf"))
            with DocumentStore(path, readonly=True) as store:
                self.assertEqual(store.schema_version, 1)
                self.assertEqual(store.get(1)["reviews"], [])
            with DocumentStore(path) as store:
                self.assertEqual(store.schema_version, 2)
                self.assertEqual(store.get(1)["source_file"], "existing.pdf")
                self.assertEqual(store.get(1)["created_at"], "original-time")
                self.assertEqual(store.review_rows()[0]["review_status"], "unreviewed")


class WorkspaceOperationTests(unittest.TestCase):
    """Exercise actual operations even on hosts that cannot start asyncio."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / "tests")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.database = self.work / "demo.sqlite3"
        with redirect_stdout(io.StringIO()):
            seed_demo(["--database", str(self.database), "--as-of", TODAY.isoformat()])
        self.workspace = Workspace(self.database, demo=True, uploads=self.work / "uploads", today=lambda: TODAY)
        self.addCleanup(self.workspace.close)

    def save(self, document_id=1, **options):
        document = self.workspace.document(document_id)
        payload = {"fields": document["fields"], "expected_revision": document["review_revision"],
                   "confirmed": False, "acknowledged": True, "note": "", **options}
        return self.workspace.save_review(document_id, payload)

    def rows(self, scope="confirmed"):
        return list(csv.DictReader(io.StringIO(self.workspace.export_csv(scope).decode("utf-8-sig"))))

    def test_list_search_filters_and_detail_use_real_records(self):
        summary = self.workspace.list_documents()
        self.assertEqual(summary["counts"], {"total": 4, "pending": 4, "confirmed": 0, "needs_review": 1})
        self.assertEqual(self.workspace.list_documents("Cedar")["total"], 1)
        self.assertEqual(self.workspace.list_documents(status="needs_review")["total"], 1)
        self.assertEqual(self.workspace.list_documents("no-match")["items"], [])
        self.assertNotIn("source_path", self.workspace.document(1))

    def test_manual_edit_is_versioned_and_original_extraction_is_unchanged(self):
        original = self.workspace.document(4)
        fields = {**original["fields"], "total_amount": "100.00"}
        saved = self.save(4, fields=fields, note="Controlled manual edit")
        self.assertEqual(saved["fields"]["total_amount"], "100.00")
        self.assertIsNone(saved["original_fields"]["total_amount"])
        self.assertEqual(saved["review_status"], "draft")
        self.assertEqual(saved["validation"]["status"], "passed")
        self.assertEqual(len(saved["reviews"]), 1)

    def test_confirmed_export_uses_latest_revision_and_draft_removes_confirmation(self):
        self.assertEqual(self.rows(), [])
        self.save(confirmed=True)
        self.assertEqual(len(self.rows()), 1)
        self.save(note="Edited again")
        self.assertEqual(self.rows(), [])
        self.assertEqual(len(self.workspace.document(1)["reviews"]), 2)

    def test_outstanding_checks_require_note_and_explicit_acknowledgement(self):
        with self.assertRaises(ValueError):
            self.save(confirmed=True, acknowledged=False)
        with self.assertRaises(ValueError):
            self.save(4, confirmed=True)
        saved = self.save(4, confirmed=True, note="Missing total verified in the source.")
        self.assertEqual(saved["review_status"], "confirmed")
        self.assertEqual(saved["validation"]["status"], "needs_review")
        self.assertEqual(self.rows()[0]["total_amount"], "")

    def test_stale_revision_and_bad_field_types_are_rejected(self):
        from document_automation.storage import ReviewConflictError
        self.save(expected_revision=0)
        with self.assertRaises(ReviewConflictError):
            self.save(expected_revision=0)
        fields = {**self.workspace.document(1)["fields"], "total_amount": 1.5}
        with self.assertRaises(ValueError):
            self.save(fields=fields)

    def test_csv_provenance_formula_escaping_and_document_uniqueness(self):
        fields = {**self.workspace.document(1)["fields"], "supplier": '=HYPERLINK("https://example.invalid")'}
        self.save(fields=fields, confirmed=True)
        data = self.workspace.export_csv("all")
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
        rows = self.rows("all")
        self.assertEqual(len(rows), 4)
        self.assertEqual(len({row["document_id"] for row in rows}), 4)
        self.assertEqual(rows[0]["supplier"], "'" + fields["supplier"])
        self.assertEqual(rows[0]["input_kind"], "reference_fixture")

    def test_pdf_preview_verifies_the_original_snapshot(self):
        self.assertEqual(self.workspace.pdf_bytes(1), (SAMPLES / "01_invoice_pln.pdf").read_bytes())
        altered = self.work / "altered.pdf"
        altered.write_bytes(b"%PDF-1.4 changed")
        with self.workspace.store() as store:
            with store.transaction():
                store.connection.execute("UPDATE documents SET source_path=? WHERE id=1", (str(altered),))
        with self.assertRaisesRegex(ValueError, "changed"):
            self.workspace.pdf_bytes(1)
        self.assertTrue(self.workspace.document(1)["pages"])

    def test_demo_upload_is_deduplicated_and_rejects_unknown_data(self):
        self.save(confirmed=True)
        job = self.workspace.submit("copy.pdf", (SAMPLES / "05_invoice_duplicate.pdf").read_bytes())
        for _ in range(200):
            result = self.workspace.job(job["id"])
            if result["state"] in {"completed", "failed"}:
                break
            time.sleep(.01)
        self.assertEqual(result["state"], "completed", result)
        self.assertTrue(result["duplicate"])
        self.assertEqual(self.workspace.document(1)["review_status"], "confirmed")
        with self.assertRaises(ValueError):
            self.workspace.submit("new.pdf", b"%PDF-1.4 unknown")
        self.assertEqual(safe_filename("../CON.pdf"), "_CON.pdf")

    def test_live_upload_runs_the_existing_pipeline_and_archives_pdf(self):
        env = self.work / ".env"
        env.write_text("LLM_MODEL=test-model\nLLM_API_KEY=test-key\n", encoding="utf-8")
        live = Workspace(self.work / "live.sqlite3", uploads=self.work / "live-uploads", exports=self.work / "exports", env_file=env, today=lambda: TODAY)
        self.addCleanup(live.close)
        fields = self.workspace.document(1)["fields"]
        with patch("document_automation.pipeline.request_invoice", return_value=LLMResult(fields, "test-model", {})) as model:
            job = live.submit("uploaded.pdf", (SAMPLES / "01_invoice_pln.pdf").read_bytes())
            for _ in range(200):
                result = live.job(job["id"])
                if result["state"] in {"completed", "failed"}:
                    break
                time.sleep(.01)
            self.assertEqual(result["state"], "completed", result)
            model.assert_called_once()
        self.assertEqual(live.document(1)["source_file"], "uploaded.pdf")
        self.assertEqual(len(list((self.work / "live-uploads").glob("*/*.pdf"))), 1)

    def test_oversized_upload_and_bounded_queue_fail_before_processing(self):
        with self.assertRaises(ValueError):
            self.workspace.submit("big.pdf", b"x" * (MAX_UPLOAD_BYTES + 1))
        with self.workspace.lock:
            self.workspace.jobs = {str(i): {"state": "queued"} for i in range(10)}
        with self.assertRaisesRegex(ValueError, "queue"):
            self.workspace.submit("copy.pdf", (SAMPLES / "01_invoice_pln.pdf").read_bytes())

    def immediate(self, coroutine):
        """Run only coroutines whose awaits are immediate, in-memory operations."""
        try:
            coroutine.send(None)
        except StopIteration as done:
            return done.value
        else:
            self.fail("Endpoint unexpectedly needs an event loop")
        finally:
            coroutine.close()

    def test_route_functions_render_bootstrap_and_save_a_real_review_without_tcp(self):
        app = create_app(self.database, demo=True, uploads=self.work / "route-uploads", today=lambda: TODAY)
        self.addCleanup(app.state.workspace.close)
        endpoints = {getattr(route, "path", ""): getattr(route, "endpoint", None) for route in app.routes}
        root = endpoints["/"]()
        self.assertIn("httponly", root.headers["set-cookie"].lower())
        self.assertIn(b'Invoice workspace', root.body)
        self.assertEqual(endpoints["/api/documents"](q="", status="all", page=1)["counts"]["total"], 4)
        self.assertTrue(endpoints["/api/documents/{document_id}/pdf"](1).body.startswith(b"%PDF-"))
        document = app.state.workspace.document(1)
        body = json.dumps({"fields": document["fields"], "expected_revision": 0,
                           "confirmed": True, "acknowledged": True, "note": "Checked"}).encode()
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}
        request = Request({"type": "http", "method": "POST", "path": "/api/documents/1/review",
                           "query_string": b"", "headers": [], "scheme": "http"}, receive=receive)
        saved = self.immediate(endpoints["/api/documents/{document_id}/review"](1, request))
        self.assertEqual(saved["review_status"], "confirmed")
        exported = endpoints["/api/export.csv"](scope="confirmed")
        rows = list(csv.DictReader(io.StringIO(exported.body.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 1)

    def test_session_and_csrf_middleware_rejects_foreign_requests_without_tcp(self):
        app = create_app(self.database, demo=True, uploads=self.work / "guard-uploads", today=lambda: TODAY)
        self.addCleanup(app.state.workspace.close)
        root_endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", "") == "/")
        token = re.search(b'name="csrf-token" content="([^"]+)"', root_endpoint().body).group(1)
        middleware = app.user_middleware[0].kwargs["dispatch"]
        async def next_response(request):
            return JSONResponse({"ok": True})
        def invoke(headers):
            request = Request({"type": "http", "method": "POST", "path": "/api/documents/1/review",
                               "root_path": "", "query_string": b"", "headers": headers, "scheme": "http",
                               "server": ("127.0.0.1", 80)})
            return self.immediate(middleware(request, next_response))
        valid = [(b"host", b"127.0.0.1"), (b"cookie", b"doc_workspace=" + token), (b"x-csrf-token", token)]
        self.assertEqual(invoke(valid).status_code, 200)
        self.assertIn("default-src 'self'", invoke(valid).headers["content-security-policy"])
        self.assertEqual(invoke(valid[:1]).status_code, 403)
        self.assertEqual(invoke(valid[:2] + [(b"x-csrf-token", b"bad")]).status_code, 403)
        self.assertEqual(invoke(valid + [(b"origin", b"https://example.invalid")]).status_code, 403)


if __name__ == "__main__":
    unittest.main()
