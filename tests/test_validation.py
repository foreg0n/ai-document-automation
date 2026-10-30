"""Deterministic rules and offline CLI behavior; never contact an LLM."""
import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import patch

from document_automation.cli import main
from document_automation.validation import validate_invoice

ROOT = Path(__file__).resolve().parents[1]
AS_OF = date(2026, 10, 30)
FIELDS = {
    "supplier": "Example Supplier", "invoice_number": "TEST-001",
    "invoice_date": "2026-10-20", "total_amount": "1234.50", "currency": "PLN",
}


def reasons(fields, **options):
    return validate_invoice(fields, reference_date=AS_OF, **options).issues


class ValidationRuleTests(unittest.TestCase):
    def test_supported_currencies_and_positive_totals_pass(self):
        for currency in ("PLN", "EUR", "USD"):
            for amount in ("0.01", "1234.50", "999999999999999999999999999999.99"):
                with self.subTest(currency=currency, amount=amount):
                    report = validate_invoice({**FIELDS, "currency": currency, "total_amount": amount},
                                              reference_date=AS_OF)
                    self.assertEqual(report.status, "passed")
                    self.assertFalse(report.requires_review)

    def test_each_null_gets_its_own_review_reason_and_is_not_replaced(self):
        fields = dict.fromkeys(FIELDS)
        original = copy.deepcopy(fields)
        report = validate_invoice(fields, reference_date=AS_OF)
        self.assertEqual(report.status, "needs_review")
        self.assertEqual([issue.field for issue in report.issues], list(FIELDS))
        self.assertTrue(all(issue.code == "missing_value" for issue in report.issues))
        self.assertEqual(fields, original)

    def test_placeholders_are_not_treated_as_extracted_values(self):
        for placeholder in ("", "  ", "null", "N/A", "Unknown", "Not provided", "-"):
            with self.subTest(value=placeholder):
                issues = reasons({**FIELDS, "supplier": placeholder})
                self.assertEqual([(issue.field, issue.code) for issue in issues],
                                 [("supplier", "placeholder_value")])

    def test_whitespace_is_reported_without_silent_normalization(self):
        fields = {**FIELDS, "currency": " PLN "}
        self.assertEqual(reasons(fields)[0].code, "surrounding_whitespace")
        self.assertEqual(fields["currency"], " PLN ")

    def test_date_requires_exact_iso_spelling(self):
        for value in ("20261020", "2026-W43-2", "20.10.2026", "2026-1-2", "2026-10-20T00:00:00"):
            with self.subTest(value=value):
                self.assertEqual(reasons({**FIELDS, "invoice_date": value})[0].code, "invalid_date_format")

    def test_impossible_calendar_dates_are_reported(self):
        for value in ("2026-02-29", "2026-04-31", "0000-01-01", "2026-13-01"):
            with self.subTest(value=value):
                self.assertEqual(reasons({**FIELDS, "invoice_date": value})[0].code, "invalid_date")

    def test_leap_date_and_reference_date_pass_but_future_date_needs_review(self):
        for value in ("2024-02-29", AS_OF.isoformat()):
            self.assertFalse(reasons({**FIELDS, "invoice_date": value}))
        self.assertEqual(reasons({**FIELDS, "invoice_date": "2026-10-31"})[0].code, "future_date")

    def test_amount_requires_plain_decimal_string_with_two_places(self):
        for value in ("1234,50", "1,234.50", "1234", "1234.5", "1e3", "NaN", "Infinity", "+5.00", "01.00", "１２.００"):
            with self.subTest(value=value):
                self.assertEqual(reasons({**FIELDS, "total_amount": value})[0].code, "invalid_amount_format")

    def test_zero_and_negative_totals_are_reviewed_by_demo_policy(self):
        for value in ("0.00", "-0.00", "-12.34"):
            with self.subTest(value=value):
                self.assertEqual(reasons({**FIELDS, "total_amount": value})[0].code, "non_positive_amount")

    def test_currency_format_is_separate_from_supported_currency_policy(self):
        for value in ("pln", "PL", "PLNN", "123", "€"):
            with self.subTest(value=value):
                self.assertEqual(reasons({**FIELDS, "currency": value})[0].code, "invalid_currency_format")
        self.assertEqual(reasons({**FIELDS, "currency": "CHF"})[0].code, "unsupported_currency")

    def test_textless_page_warning_prevents_a_pass_even_with_complete_fields(self):
        warning = "Page 2 has no extractable text; it may be blank or need OCR."
        report = validate_invoice(FIELDS, reference_date=AS_OF, extraction_warnings=(warning, warning))
        self.assertEqual(report.status, "needs_review")
        self.assertEqual(len(report.issues), 1)
        self.assertEqual(report.issues[0].code, "text_extraction_warning")
        self.assertIn("Page 2", report.issues[0].message)

    def test_same_inputs_produce_identical_serializable_reports_without_mutation(self):
        fields = {**FIELDS, "invoice_date": "2026-02-30", "total_amount": None, "currency": "CHF"}
        original = copy.deepcopy(fields)
        first = validate_invoice(fields, reference_date=AS_OF)
        second = validate_invoice(fields, reference_date=AS_OF)
        self.assertEqual(json.dumps(first.to_dict()), json.dumps(second.to_dict()))
        self.assertEqual(first.to_dict()["reference_date"], "2026-10-30")
        self.assertEqual(fields, original)
        self.assertEqual(len(first.issues), 3)

    def test_invalid_shape_missing_keys_and_wrong_types_never_pass(self):
        self.assertEqual(reasons([])[0].code, "invalid_structure")
        self.assertEqual(len(reasons({})), 5)
        for value in (True, 1234.5, [], {}):
            with self.subTest(value=value):
                self.assertEqual(reasons({**FIELDS, "total_amount": value})[0].code, "invalid_type")
        self.assertEqual(reasons({**FIELDS, "extra": "unexpected"})[0].code, "unexpected_field")


class SavedValidationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / "tests")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.output = self.work / "reports"
        self.source = self.work / "invoice.pdf.fields.json"
        self.source.write_text(json.dumps(FIELDS), encoding="utf-8")
        for target in ("document_automation.cli.load_settings", "document_automation.llm_client.build_opener"):
            guard = patch(target, side_effect=AssertionError("Offline validation must not load credentials or call API"))
            guard.start()
            self.addCleanup(guard.stop)

    def run_cli(self, source=None, *options):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["validate", str(source or self.source), "--output-dir", str(self.output),
                         "--as-of", AS_OF.isoformat(), *options])
        return code, stdout.getvalue(), stderr.getvalue()

    def load_report(self, name="invoice.pdf.validation.json"):
        return json.loads((self.output / name).read_text(encoding="utf-8"))

    def test_valid_saved_fields_are_checked_without_credentials_or_network(self):
        original = self.source.read_bytes()
        code, stdout, stderr = self.run_cli()
        self.assertEqual(code, 0, stderr)
        self.assertIn("PASSED", stdout)
        report = self.load_report()
        self.assertFalse(report["text_warnings_checked"])
        self.assertEqual(report["validation"]["status"], "passed")
        self.assertEqual(self.source.read_bytes(), original)

    def test_missing_total_writes_review_report_and_returns_exit_three(self):
        self.source.write_text(json.dumps({**FIELDS, "total_amount": None}), encoding="utf-8")
        code, stdout, stderr = self.run_cli()
        self.assertEqual(code, 3, stderr)
        self.assertIn("NEEDS_REVIEW", stdout)
        self.assertIn("[missing_value] total_amount", stdout)
        self.assertEqual(self.load_report()["validation"]["issues"][0]["field"], "total_amount")

    def test_batch_ignores_other_json_and_prioritizes_errors_over_review_exit(self):
        (self.work / "missing.fields.json").write_text(json.dumps({**FIELDS, "total_amount": None}), encoding="utf-8")
        (self.work / "bad.fields.json").write_text("not JSON", encoding="utf-8")
        (self.work / "preview.request.json").write_text("not fields", encoding="utf-8")
        code, stdout, stderr = self.run_cli(self.work)
        self.assertEqual(code, 1)
        self.assertIn("1 passed, 1 need review, 1 failed", stdout)
        self.assertNotIn("Traceback", stderr)
        self.assertEqual(len(list(self.output.glob("*.validation.json"))), 2)

    def test_companion_metadata_warning_is_retained_without_mutating_metadata(self):
        metadata = self.work / "invoice.pdf.meta.json"
        metadata.write_text(json.dumps({"source_file": "invoice.pdf", "warnings": ["Page 2 has no text."]}), encoding="utf-8")
        original = metadata.read_bytes()
        code, stdout, stderr = self.run_cli()
        self.assertEqual(code, 3, stderr)
        self.assertIn("Page 2", stdout)
        report = self.load_report()
        self.assertTrue(report["text_warnings_checked"])
        self.assertEqual(report["validation"]["issues"][0]["code"], "text_extraction_warning")
        self.assertEqual(metadata.read_bytes(), original)

    def test_bad_or_mismatched_companion_metadata_is_an_error(self):
        metadata = self.work / "invoice.pdf.meta.json"
        for content in (
            "bad JSON", "[]", json.dumps({"source_file": "other.pdf", "warnings": []}),
            json.dumps({"source_file": "invoice.pdf", "warnings": "not a list"}),
        ):
            with self.subTest(content=content):
                metadata.write_text(content, encoding="utf-8")
                code, _, stderr = self.run_cli()
                self.assertEqual(code, 1)
                self.assertIn("ERROR", stderr)
                self.assertFalse(self.output.exists())

    def test_duplicate_keys_are_an_error_not_a_pass(self):
        self.source.write_text('{"supplier":"first","supplier":"second"}', encoding="utf-8")
        code, _, stderr = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn("duplicate", stderr)
        self.assertFalse(self.output.exists())

    def test_report_is_protected_and_overwrite_can_use_a_new_reference_date(self):
        self.assertEqual(self.run_cli()[0], 0)
        path = self.output / "invoice.pdf.validation.json"
        original = path.read_bytes()
        code, _, stderr = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn("--overwrite", stderr)
        self.assertEqual(path.read_bytes(), original)
        code, _, stderr = self.run_cli(None, "--overwrite", "--as-of", "2026-10-19")
        self.assertEqual(code, 3, stderr)
        self.assertEqual(self.load_report()["validation"]["issues"][0]["code"], "future_date")

    def test_failed_revalidation_preserves_an_earlier_report(self):
        self.assertEqual(self.run_cli()[0], 0)
        path = self.output / "invoice.pdf.validation.json"
        original = path.read_bytes()
        self.source.write_text("not JSON", encoding="utf-8")
        self.assertEqual(self.run_cli(None, "--overwrite")[0], 1)
        self.assertEqual(path.read_bytes(), original)

    def test_invalid_reference_date_is_a_cli_argument_error(self):
        for value in ("2026-02-30", "20261030", "2026-W44-5"):
            with self.subTest(value=value), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as result:
                    self.run_cli(None, "--as-of", value)
                self.assertEqual(result.exception.code, 2)

    def test_missing_input_empty_folder_and_wrong_extension_are_errors(self):
        empty = self.work / "empty"
        empty.mkdir()
        not_json = self.work / "invoice.pdf"
        not_json.write_bytes(b"not JSON")
        for path in (empty, self.work / "missing.json", not_json):
            with self.subTest(path=path):
                code, _, stderr = self.run_cli(path)
                self.assertEqual(code, 1)
                self.assertIn("ERROR", stderr)

    def test_unicode_filename_round_trip(self):
        source = self.work / "рахунок_Łódź.fields.json"
        source.write_text(json.dumps(FIELDS), encoding="utf-8")
        code, _, stderr = self.run_cli(source)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.load_report("рахунок_Łódź.validation.json")["fields_file"], source.name)


if __name__ == "__main__":
    unittest.main()
