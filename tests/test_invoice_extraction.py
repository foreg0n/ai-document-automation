"""Stage 3 checks: real PDF reading, controlled API replies, no external calls.

These tests verify the integration and contract, not a real model's accuracy.
"""
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.client import IncompleteRead
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from document_automation.cli import main
from document_automation.config import ConfigurationError, LLMSettings, load_settings, read_env_file
from document_automation.invoice import (
    FIELD_DESCRIPTIONS, MAX_INPUT_CHARACTERS, InvoiceFormatError,
    build_request, parse_invoice_fields,
)
from document_automation.llm_client import APIError, MAX_RESPONSE_BYTES, parse_completion, request_invoice
from document_automation.pdf_text import ExtractedDocument, PageText, extract_pdf_text
from document_automation.storage import DocumentStore

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "data" / "samples"
FIELDS = {
    "supplier": "Example Supplier", "invoice_number": "TEST-001",
    "invoice_date": "2026-10-20", "total_amount": "1234.50", "currency": "PLN",
}
SECRET = "test-secret-do-not-log"


def completion(fields=None, **overrides):
    return {
        "model": "actual-test-model",
        "choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": json.dumps(FIELDS if fields is None else fields),
        }}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        **overrides,
    }


class InvoiceExtractionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / "tests")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.output = self.work / "output"
        self.env_file = self.work / ".env"
        self.env_file.write_text(f"LLM_MODEL=test-model\nLLM_API_KEY={SECRET}\n", encoding="utf-8")
        clean_environment = {key: value for key, value in os.environ.items() if not key.startswith("LLM_")}
        environment_patch = patch.dict(os.environ, clean_environment, clear=True)
        environment_patch.start()
        self.addCleanup(environment_patch.stop)
        # Every test must opt into a fake response; unexpected calls fail closed.
        transport_patch = patch("document_automation.llm_client.build_opener")
        self.transport = transport_patch.start()
        self.addCleanup(transport_patch.stop)
        self.opener = Mock()
        self.opener.open.side_effect = AssertionError("Unexpected API request in a test")
        self.transport.return_value = self.opener
        self.settings = LLMSettings("test-model", SECRET)

    def fake_reply(self, reply):
        raw = json.dumps(reply, ensure_ascii=False).encode("utf-8")
        self.opener.open.side_effect = lambda *args, **kwargs: io.BytesIO(raw)

    def run_cli(self, source, *options):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([
                "process", str(source), "--output-dir", str(self.output),
                "--env-file", str(self.env_file), "--as-of", "2026-10-30", *options,
                "--database", str(self.work / "results.sqlite3"),
            ])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_env_file_quotes_bom_comments_and_no_expansion(self):
        self.env_file.write_text(
            '\ufeff# comment\nLLM_MODEL="vendor/model"\nLLM_API_KEY=\'a=b#c\'\nOTHER=$HOME\n',
            encoding="utf-8",
        )
        values = read_env_file(self.env_file)
        self.assertEqual(values["LLM_MODEL"], "vendor/model")
        self.assertEqual(values["LLM_API_KEY"], "a=b#c")
        self.assertEqual(values["OTHER"], "$HOME")

    def test_cli_then_environment_then_file_precedence(self):
        with patch.dict(os.environ, {"LLM_MODEL": "environment-model"}):
            self.assertEqual(load_settings(self.env_file).model, "environment-model")
            self.assertEqual(load_settings(self.env_file, model="cli-model").model, "cli-model")
            self.assertEqual(load_settings(self.env_file, response_format="json_object").response_format,
                             "json_object")

    def test_key_is_required_only_for_live_runs_and_is_not_in_repr(self):
        self.env_file.write_text("LLM_MODEL=test-model\n", encoding="utf-8")
        with self.assertRaisesRegex(ConfigurationError, "LLM_API_KEY"):
            load_settings(self.env_file)
        self.assertEqual(load_settings(self.env_file, require_api_key=False).api_key, "")
        self.assertNotIn(SECRET, repr(self.settings))

    def test_missing_model_and_bad_timeout_are_reported(self):
        self.env_file.write_text("", encoding="utf-8")
        with self.assertRaisesRegex(ConfigurationError, "LLM_MODEL"):
            load_settings(self.env_file, require_api_key=False)
        for timeout in ("0", "-1", "nan", "inf", "301", "not-a-number"):
            with self.subTest(timeout=timeout), patch.dict(os.environ, {"LLM_TIMEOUT_SECONDS": timeout}):
                with self.assertRaisesRegex(ConfigurationError, "LLM_TIMEOUT_SECONDS"):
                    load_settings(self.env_file, model="test", require_api_key=False)

    def test_invalid_env_error_does_not_echo_secret_assignment(self):
        self.env_file.write_text(f"LLM_API_KEY='{SECRET}\n", encoding="utf-8")
        code, _, stderr = self.run_cli(SAMPLES, "--dry-run", "--model", "test")
        self.assertEqual(code, 1)
        self.assertIn("line 1", stderr)
        self.assertNotIn(SECRET, stderr)
        self.opener.open.assert_not_called()

    def test_invalid_base_urls_and_format_are_rejected(self):
        for address in (
            "http://remote.example/v1", "https://user:password@example.com/v1",
            "https://example.com/v1?key=secret", "https://example.com:bad/v1",
            "https://example.com/v1#fragment", "https://bad host/v1",
        ):
            with self.subTest(address=address), patch.dict(os.environ, {"LLM_BASE_URL": address}):
                with self.assertRaises(ConfigurationError):
                    load_settings(self.env_file)
        with patch.dict(os.environ, {"LLM_RESPONSE_FORMAT": "invalid"}):
            with self.assertRaisesRegex(ConfigurationError, "LLM_RESPONSE_FORMAT"):
                load_settings(self.env_file)

    def test_payload_contains_only_page_text_and_no_secret_or_reference_answers(self):
        document = extract_pdf_text(SAMPLES / "04_invoice_missing_total.pdf")
        payload = build_request(document, self.settings)
        user_data = json.loads(payload["messages"][1]["content"])
        self.assertEqual(set(user_data), {"pages"})
        self.assertIn("Not provided", user_data["pages"][0]["text"])
        schema = payload["response_format"]["json_schema"]["schema"]
        self.assertEqual(set(schema["required"]), set(FIELDS))
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["total_amount"]["type"], ["string", "null"])
        serialized = json.dumps(payload)
        for forbidden in (SECRET, "ground_truth", "expected_fields", document.source_file):
            self.assertNotIn(forbidden, serialized)

    def test_json_object_mode_is_explicit_and_works_for_other_api_roots(self):
        settings = LLMSettings("model", SECRET, "https://api.example/v1", "json_object")
        document = ExtractedDocument("invoice.pdf", (PageText(1, "Invoice text"),), ())
        payload = build_request(document, settings)
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertNotIn("provider", payload)
        self.assertEqual(len(payload["messages"]), 2)

    def test_empty_and_oversized_documents_are_not_sent_or_silently_truncated(self):
        for text in ("", " " * 10, "x" * (MAX_INPUT_CHARACTERS + 1)):
            with self.subTest(length=len(text)), self.assertRaises(InvoiceFormatError):
                build_request(ExtractedDocument("a.pdf", (PageText(1, text),), ()), self.settings)
        self.opener.open.assert_not_called()

    def test_null_is_preserved_and_all_five_keys_are_required(self):
        fields = {**FIELDS, "total_amount": None}
        self.assertEqual(parse_invoice_fields(json.dumps(fields)), fields)
        self.assertEqual(parse_invoice_fields(json.dumps(dict.fromkeys(FIELDS))), dict.fromkeys(FIELDS))

    def test_invalid_model_json_is_rejected_without_repair(self):
        for content in (
            "not JSON", "```json\n" + json.dumps(FIELDS) + "\n```",
            json.dumps(FIELDS) + " trailing text", "[]", "null",
            json.dumps({**FIELDS, "extra": "invented"}),
            json.dumps({key: value for key, value in FIELDS.items() if key != "currency"}),
            '{"supplier":"a","supplier":"b"}',
            json.dumps(FIELDS).replace('"1234.50"', "NaN"),
        ):
            with self.subTest(content=content), self.assertRaises(InvoiceFormatError):
                parse_invoice_fields(content)

    def test_amount_numbers_booleans_empty_strings_and_nested_values_are_rejected(self):
        for value in (1234.5, True, "", "   ", [], {"amount": "1234.50"}, "\ud800"):
            with self.subTest(value=value), self.assertRaises(InvoiceFormatError):
                parse_invoice_fields(json.dumps({**FIELDS, "total_amount": value}))

    def test_truncated_refused_and_tool_responses_are_rejected(self):
        variants = [completion() for _ in range(4)]
        variants[0]["choices"][0]["finish_reason"] = "length"
        variants[1]["choices"][0]["message"]["refusal"] = "Cannot comply"
        variants[2]["choices"][0]["message"]["tool_calls"] = [{"id": "tool"}]
        variants[3]["choices"][0]["message"]["content"] = None
        for reply in variants:
            with self.subTest(reply=reply), self.assertRaises(InvoiceFormatError):
                parse_completion(reply, "requested-model")

    def test_api_response_shapes_and_error_envelope_are_handled(self):
        for reply in ([], {}, {"choices": []}, {"choices": [None]}, {"choices": [1, 2]}):
            with self.subTest(reply=reply), self.assertRaises(InvoiceFormatError):
                parse_completion(reply, "requested-model")
        with self.assertRaises(APIError):
            parse_completion({"error": {"message": SECRET}}, "requested-model")

    def test_live_pipeline_serializes_request_and_saves_fields_and_metadata(self):
        self.fake_reply(completion())
        code, stdout, stderr = self.run_cli(SAMPLES / "01_invoice_pln.pdf")
        self.assertEqual(code, 0, stderr)
        self.assertIn("1 succeeded", stdout)
        self.opener.open.assert_called_once()
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + SECRET)
        self.assertEqual(request.get_method(), "POST")
        body = json.loads(request.data.decode("utf-8"))
        self.assertIn("Northstar Office Demo", body["messages"][1]["content"])
        self.assertEqual(self.opener.open.call_args.kwargs["timeout"], 60.0)
        fields = json.loads((self.output / "01_invoice_pln.pdf.fields.json").read_text(encoding="utf-8"))
        metadata = json.loads((self.output / "01_invoice_pln.pdf.meta.json").read_text(encoding="utf-8"))
        self.assertEqual(fields, FIELDS)
        self.assertEqual(metadata["model"], "actual-test-model")
        self.assertEqual(metadata["usage"]["total_tokens"], 150)
        self.assertEqual(metadata["validation_status"], "passed")
        self.assertEqual(metadata["validation_report"], "01_invoice_pln.pdf.validation.json")
        report_file = self.output / metadata["validation_report"]
        self.assertEqual(json.loads(report_file.read_text(encoding="utf-8"))["validation"]["status"], "passed")
        self.assertNotIn(SECRET, stdout + stderr + json.dumps(metadata) + json.dumps(fields))
        self.assertNotIn(SECRET.encode(), (self.work / "results.sqlite3").read_bytes())

    def test_missing_total_in_controlled_reply_stays_null(self):
        self.fake_reply(completion({**FIELDS, "total_amount": None}))
        code, _, stderr = self.run_cli(SAMPLES / "04_invoice_missing_total.pdf")
        self.assertEqual(code, 3, stderr)
        path = self.output / "04_invoice_missing_total.pdf.fields.json"
        self.assertIsNone(json.loads(path.read_text(encoding="utf-8"))["total_amount"])

    def test_dry_run_all_samples_needs_no_key_and_creates_no_field_results(self):
        self.env_file.write_text("LLM_MODEL=preview-model\n", encoding="utf-8")
        code, stdout, stderr = self.run_cli(SAMPLES, "--dry-run")
        self.assertEqual(code, 0, stderr)
        self.assertIn("DRY RUN", stdout)
        self.opener.open.assert_not_called()
        self.assertEqual(len(list(self.output.glob("*.request.json"))), 5)
        self.assertFalse(list(self.output.glob("*.fields.json")))
        self.assertFalse(list(self.output.glob("*.validation.json")))
        self.assertFalse((self.work / "results.sqlite3").exists())
        preview = json.loads((self.output / "01_invoice_pln.pdf.request.json").read_text(encoding="utf-8"))
        self.assertEqual(preview["mode"], "dry_run")
        self.assertEqual(preview["request_body"]["model"], "preview-model")

    def test_dry_run_never_writes_a_configured_secret(self):
        code, stdout, stderr = self.run_cli(SAMPLES / "01_invoice_pln.pdf", "--dry-run")
        self.assertEqual(code, 0, stderr)
        content = (self.output / "01_invoice_pln.pdf.request.json").read_text(encoding="utf-8")
        self.assertNotIn(SECRET, content + stdout + stderr)
        self.opener.open.assert_not_called()

    def test_missing_key_stops_before_export_and_network(self):
        self.env_file.write_text("LLM_MODEL=test\n", encoding="utf-8")
        code, _, stderr = self.run_cli(SAMPLES)
        self.assertEqual(code, 1)
        self.assertIn("LLM_API_KEY", stderr)
        self.opener.open.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_existing_output_stops_before_a_billable_request(self):
        self.output.mkdir()
        fields = self.output / "01_invoice_pln.pdf.fields.json"
        fields.write_text("earlier result", encoding="utf-8")
        code, _, stderr = self.run_cli(SAMPLES / "01_invoice_pln.pdf")
        self.assertEqual(code, 1)
        self.assertIn("--overwrite", stderr)
        self.assertEqual(fields.read_text(encoding="utf-8"), "earlier result")
        self.opener.open.assert_not_called()

    def test_explicit_overwrite_replaces_previous_results(self):
        self.output.mkdir()
        fields = self.output / "01_invoice_pln.pdf.fields.json"
        fields.write_text("earlier result", encoding="utf-8")
        self.fake_reply(completion())
        code, _, stderr = self.run_cli(SAMPLES / "01_invoice_pln.pdf", "--overwrite")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(fields.read_text(encoding="utf-8")), FIELDS)

    def test_bad_pdf_does_not_call_model_and_other_files_continue(self):
        source = self.work / "input"
        source.mkdir()
        (source / "00_bad.pdf").write_bytes(b"not a PDF")
        shutil.copyfile(SAMPLES / "01_invoice_pln.pdf", source / "01_good.pdf")
        self.fake_reply(completion())
        code, stdout, stderr = self.run_cli(source)
        self.assertEqual(code, 1)
        self.assertIn("1 succeeded, 1 failed, 0 skipped", stdout)
        self.assertNotIn("Traceback", stderr)
        self.opener.open.assert_called_once()
        self.assertTrue((self.output / "01_good.pdf.fields.json").is_file())

    def test_auth_or_rate_limit_stops_batch_without_retries_or_raw_error_logging(self):
        for status in (401, 429):
            with self.subTest(status=status):
                self.opener.open.reset_mock()
                self.opener.open.side_effect = HTTPError(
                    "https://api.example", status, SECRET, {}, io.BytesIO(SECRET.encode()),
                )
                code, stdout, stderr = self.run_cli(SAMPLES)
                self.assertEqual(code, 1)
                self.assertIn(f"HTTP {status}", stderr)
                self.assertIn("0 succeeded, 1 failed, 4 skipped", stdout)
                self.assertNotIn(SECRET, stdout + stderr)
                self.opener.open.assert_called_once()
                self.assertFalse(list(self.output.glob("*.fields.json")))

    def test_timeout_or_network_error_creates_no_invoice_fields(self):
        for error in (TimeoutError(SECRET), URLError(SECRET), IncompleteRead(b"partial")):
            with self.subTest(error=type(error).__name__):
                self.opener.open.side_effect = error
                code, stdout, stderr = self.run_cli(SAMPLES / "01_invoice_pln.pdf")
                self.assertEqual(code, 1)
                self.assertIn("timed out", stderr)
                self.assertNotIn(SECRET, stdout + stderr)
                self.assertFalse(list(self.output.glob("*.fields.json")))

    def test_invalid_or_oversized_http_body_is_not_accepted(self):
        for raw in (b"<html>not JSON</html>", b"x" * (MAX_RESPONSE_BYTES + 1)):
            with self.subTest(size=len(raw)):
                self.opener.open.side_effect = lambda *args, **kwargs: io.BytesIO(raw)
                with self.assertRaises(APIError):
                    request_invoice({}, self.settings)

    def test_valid_json_with_wrong_field_types_creates_no_outputs(self):
        self.fake_reply(completion({**FIELDS, "total_amount": 1234.5}))
        code, _, stderr = self.run_cli(SAMPLES / "01_invoice_pln.pdf")
        self.assertEqual(code, 1)
        self.assertIn("total_amount", stderr)
        self.assertFalse(list(self.output.glob("*.json")))

    def test_usage_metadata_ignores_unexpected_values(self):
        reply = completion(usage={"prompt_tokens": True, "completion_tokens": -1,
                                  "total_tokens": 150, "secret": SECRET})
        result = parse_completion(reply, "requested-model")
        self.assertEqual(result.usage, {"total_tokens": 150})

    def test_review_report_preserves_problematic_model_values(self):
        fields = {**FIELDS, "invoice_date": "2026-02-30", "total_amount": "-3.00", "currency": "CHF"}
        self.fake_reply(completion(fields))
        code, stdout, stderr = self.run_cli(SAMPLES / "01_invoice_pln.pdf")
        self.assertEqual(code, 3, stderr)
        self.assertIn("NEEDS_REVIEW", stdout)
        saved = json.loads((self.output / "01_invoice_pln.pdf.fields.json").read_text(encoding="utf-8"))
        report = json.loads((self.output / "01_invoice_pln.pdf.validation.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, fields)
        self.assertEqual({issue["code"] for issue in report["validation"]["issues"]},
                         {"invalid_date", "non_positive_amount", "unsupported_currency"})

    def test_existing_validation_report_prevents_another_api_call(self):
        self.output.mkdir()
        report = self.output / "01_invoice_pln.pdf.validation.json"
        report.write_text("earlier report", encoding="utf-8")
        code, _, stderr = self.run_cli(SAMPLES / "01_invoice_pln.pdf")
        self.assertEqual(code, 1)
        self.assertIn("--overwrite", stderr)
        self.opener.open.assert_not_called()
        self.assertEqual(report.read_text(encoding="utf-8"), "earlier report")

    def test_renamed_duplicate_reuses_result_without_reading_credentials(self):
        self.fake_reply(completion())
        self.assertEqual(self.run_cli(SAMPLES / "01_invoice_pln.pdf")[0], 0)
        self.env_file.write_text("invalid configuration", encoding="utf-8")
        code, stdout, stderr = self.run_cli(SAMPLES / "05_invoice_duplicate.pdf", "--as-of", "2020-01-01")
        self.assertEqual(code, 0, stderr)
        self.assertIn("DUPLICATE", stdout)
        self.opener.open.assert_called_once()
        metadata = json.loads((self.output / "05_invoice_duplicate.pdf.meta.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["duplicate_of"], 1)
        self.assertEqual(metadata["original_source_file"], "01_invoice_pln.pdf")
        cached_report = json.loads((self.output / "05_invoice_duplicate.pdf.validation.json").read_text(encoding="utf-8"))
        self.assertEqual(cached_report["validation"]["reference_date"], "2026-10-30")
        with DocumentStore(self.work / "results.sqlite3", readonly=True) as store:
            self.assertEqual(len(store.history()), 1)
            self.assertEqual(store.history()[0]["duplicates"], 1)

    def test_repeat_preserves_existing_exports_and_explicit_overwrite_uses_cache(self):
        self.fake_reply(completion())
        self.assertEqual(self.run_cli(SAMPLES / "01_invoice_pln.pdf")[0], 0)
        fields = self.output / "01_invoice_pln.pdf.fields.json"
        fields.write_text("keep my local export", encoding="utf-8")
        code, stdout, stderr = self.run_cli(SAMPLES / "01_invoice_pln.pdf")
        self.assertEqual(code, 0, stderr)
        self.assertIn("existing JSON exports were kept", stdout)
        self.assertEqual(fields.read_text(encoding="utf-8"), "keep my local export")
        self.assertEqual(self.run_cli(SAMPLES / "01_invoice_pln.pdf", "--overwrite")[0], 0)
        self.assertEqual(json.loads(fields.read_text(encoding="utf-8")), FIELDS)
        self.opener.open.assert_called_once()

    def test_changed_bytes_under_same_filename_create_a_new_document(self):
        path = self.work / "invoice.pdf"
        shutil.copyfile(SAMPLES / "01_invoice_pln.pdf", path)
        self.fake_reply(completion())
        self.assertEqual(self.run_cli(path)[0], 0)
        shutil.copyfile(SAMPLES / "02_invoice_eur.pdf", path)
        self.assertEqual(self.run_cli(path, "--overwrite")[0], 0)
        self.assertEqual(self.opener.open.call_count, 2)
        with DocumentStore(self.work / "results.sqlite3", readonly=True) as store:
            self.assertEqual(len(store.history()), 2)

    def test_export_failure_keeps_committed_result_and_retry_needs_no_model_call(self):
        self.fake_reply(completion())
        with patch("document_automation.pipeline._write_outputs", side_effect=OSError("disk write failed")):
            self.assertEqual(self.run_cli(SAMPLES / "01_invoice_pln.pdf")[0], 1)
        with DocumentStore(self.work / "results.sqlite3", readonly=True) as store:
            stored = store.get(1)
            self.assertEqual(stored["state"], "completed")
            self.assertEqual(stored["attempts"][0]["export_status"], "failed")
        self.env_file.write_text("", encoding="utf-8")
        code, _, stderr = self.run_cli(SAMPLES / "01_invoice_pln.pdf")
        self.assertEqual(code, 0, stderr)
        self.opener.open.assert_called_once()
        self.assertTrue((self.output / "01_invoice_pln.pdf.fields.json").is_file())

    def test_failed_api_attempt_is_retried_and_not_treated_as_completed_duplicate(self):
        self.opener.open.side_effect = URLError("unavailable")
        self.assertEqual(self.run_cli(SAMPLES / "01_invoice_pln.pdf")[0], 1)
        self.fake_reply(completion())
        code, stdout, stderr = self.run_cli(SAMPLES / "01_invoice_pln.pdf")
        self.assertEqual(code, 0, stderr)
        self.assertNotIn("DUPLICATE ", stdout)
        self.assertEqual(self.opener.open.call_count, 2)
        with DocumentStore(self.work / "results.sqlite3", readonly=True) as store:
            self.assertEqual([item["state"] for item in store.get(1)["attempts"]], ["failed", "completed"])

    def test_five_file_batch_calls_api_four_times_and_reuses_review_results_later(self):
        def reply(request, **kwargs):
            fields = {**FIELDS, "total_amount": None} if "Not provided" in request.data.decode("utf-8") else FIELDS
            return io.BytesIO(json.dumps(completion(fields)).encode("utf-8"))
        self.opener.open.side_effect = reply
        code, stdout, stderr = self.run_cli(SAMPLES)
        self.assertEqual(code, 3, stderr)
        self.assertIn("Duplicates reused: 1", stdout)
        self.assertEqual(self.opener.open.call_count, 4)
        self.env_file.write_text("", encoding="utf-8")
        code, stdout, stderr = self.run_cli(SAMPLES)
        self.assertEqual(code, 3, stderr)
        self.assertIn("Duplicates reused: 5", stdout)
        self.assertEqual(self.opener.open.call_count, 4)
        with DocumentStore(self.work / "results.sqlite3", readonly=True) as store:
            self.assertEqual(len(store.history()), 4)


if __name__ == "__main__":
    unittest.main()
