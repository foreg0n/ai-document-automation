"""Behavior checks using real PDFs; no network or LLM calls."""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pypdfium2 as pdfium

from document_automation.cli import collect_pdf_files, main, save_extraction
from document_automation.pdf_text import TextExtractionError, extract_pdf_text

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "data" / "samples"


class TextExtractionTests(unittest.TestCase):
    def setUp(self):
        # Keep temporary files inside the workspace on restricted Windows hosts.
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / "tests")
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)

    def run_cli(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def make_blank_pdf(self, name="blank.pdf"):
        path = self.work / name
        with pdfium.PdfDocument.new() as document:
            page = document.new_page(300, 300)
            page.close()
            document.save(path)
        return path

    def test_all_sample_layouts_preserve_visible_values(self):
        cases = {
            "01_invoice_pln.pdf": (
                "Northstar Office Demo", "DEMO-2026-001", "2026-10-20", "1,234.50 PLN",
            ),
            "02_invoice_eur.pdf": (
                "Blue Harbor Studio Demo", "DEMO-2026-002", "21.10.2026", "980,00 EUR",
            ),
            "03_invoice_usd.pdf": (
                "Cedar Cloud Services Demo", "DEMO-2026-003",
                "October 22, 2026", "November 05, 2026", "2,750.00 USD",
            ),
            "04_invoice_missing_total.pdf": (
                "Riverbend Analytics Demo", "DEMO-2026-004", "Not provided",
            ),
            "05_invoice_duplicate.pdf": ("Northstar Office Demo", "1,234.50 PLN"),
        }
        for filename, expected_fragments in cases.items():
            with self.subTest(filename=filename):
                result = extract_pdf_text(SAMPLES / filename)
                self.assertEqual(len(result.pages), 1)
                self.assertEqual(result.pages[0].page_number, 1)
                self.assertFalse(result.warnings)
                for fragment in expected_fragments:
                    self.assertIn(fragment, result.text)
                self.assertNotIn("\r", result.text)

    def test_duplicate_has_identical_text_but_its_own_source_name(self):
        original = extract_pdf_text(SAMPLES / "01_invoice_pln.pdf")
        duplicate = extract_pdf_text(SAMPLES / "05_invoice_duplicate.pdf")
        self.assertEqual(original.text, duplicate.text)
        self.assertNotEqual(original.source_file, duplicate.source_file)

    def test_multiple_pages_keep_order_and_page_numbers(self):
        path = self.work / "multiple.pdf"
        with pdfium.PdfDocument.new() as combined:
            for filename in ("01_invoice_pln.pdf", "03_invoice_usd.pdf"):
                with pdfium.PdfDocument(SAMPLES / filename) as source:
                    combined.import_pages(source)
            combined.save(path)
        result = extract_pdf_text(path)
        self.assertEqual([page.page_number for page in result.pages], [1, 2])
        self.assertIn("Northstar", result.pages[0].text)
        self.assertIn("Cedar Cloud", result.pages[1].text)
        self.assertLess(result.text.index("Northstar"), result.text.index("Cedar Cloud"))

    def test_blank_page_in_mixed_pdf_is_reported_and_retained(self):
        path = self.work / "mixed.pdf"
        with pdfium.PdfDocument.new() as combined:
            with pdfium.PdfDocument(SAMPLES / "01_invoice_pln.pdf") as source:
                combined.import_pages(source)
            combined.new_page(300, 300).close()
            combined.save(path)
        result = extract_pdf_text(path)
        self.assertEqual(len(result.pages), 2)
        self.assertEqual(result.pages[1].text, "")
        self.assertIn("Page 2", result.warnings[0])
        _, json_path = save_extraction(result, self.work / "results")
        self.assertEqual(json.loads(json_path.read_text(encoding="utf-8"))["warnings"],
                         list(result.warnings))

    def test_blank_document_is_not_a_successful_extraction(self):
        with self.assertRaisesRegex(TextExtractionError, "OCR"):
            extract_pdf_text(self.make_blank_pdf())

    def test_image_only_document_requires_ocr(self):
        path = self.work / "image-only.pdf"
        with pdfium.PdfDocument.new() as document:
            page = document.new_page(300, 300)
            bitmap = pdfium.PdfBitmap.new_native(2, 2, pdfium.raw.FPDFBitmap_BGR)
            try:
                image = pdfium.PdfImage.new(document)
                image.set_bitmap(bitmap)
                image.set_matrix(pdfium.PdfMatrix(200, 0, 0, 200, 20, 20))
                page.insert_obj(image)
                page.gen_content()
                document.save(path)
            finally:
                bitmap.close()
                page.close()
        with self.assertRaisesRegex(TextExtractionError, "OCR"):
            extract_pdf_text(path)

    def test_missing_file_has_actionable_error(self):
        with self.assertRaisesRegex(TextExtractionError, "File not found"):
            extract_pdf_text(self.work / "missing.pdf")

    def test_directory_is_not_accepted_by_extraction_function(self):
        with self.assertRaisesRegex(TextExtractionError, "Expected a PDF file"):
            extract_pdf_text(self.work)

    def test_non_pdf_extension_is_rejected(self):
        path = self.work / "invoice.txt"
        path.write_text("not a PDF", encoding="utf-8")
        with self.assertRaisesRegex(TextExtractionError, "Only .pdf"):
            extract_pdf_text(path)

    def test_empty_and_damaged_pdf_are_rejected(self):
        for index, content in enumerate((b"", b"%PDF-1.4\nbroken\n", b"not a PDF")):
            with self.subTest(content=content):
                path = self.work / f"bad-{index}.pdf"
                path.write_bytes(content)
                with self.assertRaises(TextExtractionError):
                    extract_pdf_text(path)

    def test_empty_folder_reports_no_pdfs(self):
        with self.assertRaisesRegex(ValueError, "No PDF files"):
            collect_pdf_files(self.work)

    def test_unicode_path_and_uppercase_extension_round_trip(self):
        path = self.work / "рахунок_Łódź.PDF"
        shutil.copyfile(SAMPLES / "01_invoice_pln.pdf", path)
        self.assertEqual(collect_pdf_files(self.work), [path])
        result = extract_pdf_text(path)
        _, json_path = save_extraction(result, self.work / "результати")
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["source_file"], path.name)
        self.assertEqual(payload["text"], result.text)

    def test_saved_json_and_text_match_real_extraction(self):
        source = SAMPLES / "01_invoice_pln.pdf"
        original_bytes = source.read_bytes()
        result = extract_pdf_text(source)
        text_path, json_path = save_extraction(result, self.work)
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["page_count"], 1)
        self.assertEqual(payload["pages"][0]["text"], payload["text"])
        self.assertEqual(text_path.read_text(encoding="utf-8"), result.text + "\n")
        self.assertEqual(source.read_bytes(), original_bytes)

    def test_existing_output_is_preserved_without_overwrite(self):
        result = extract_pdf_text(SAMPLES / "01_invoice_pln.pdf")
        json_path = self.work / f"{result.source_file}.json"
        json_path.write_text("my earlier result", encoding="utf-8")
        with self.assertRaisesRegex(FileExistsError, "--overwrite"):
            save_extraction(result, self.work)
        self.assertEqual(json_path.read_text(encoding="utf-8"), "my earlier result")
        self.assertFalse((self.work / f"{result.source_file}.txt").exists())

    def test_overwrite_explicitly_replaces_previous_output(self):
        result = extract_pdf_text(SAMPLES / "01_invoice_pln.pdf")
        text_path, json_path = save_extraction(result, self.work)
        text_path.write_text("old", encoding="utf-8")
        json_path.write_text("old", encoding="utf-8")
        save_extraction(result, self.work, overwrite=True)
        self.assertEqual(text_path.read_text(encoding="utf-8"), result.text + "\n")
        self.assertEqual(json.loads(json_path.read_text(encoding="utf-8"))["text"],
                         result.text)

    def test_batch_continues_after_error_and_returns_failure(self):
        (self.work / "00_bad.pdf").write_bytes(b"damaged")
        good = self.work / "01_good.pdf"
        shutil.copyfile(SAMPLES / "01_invoice_pln.pdf", good)
        output = self.work / "results"
        code, stdout, stderr = self.run_cli(
            "extract", str(self.work), "--output-dir", str(output),
        )
        self.assertEqual(code, 1)
        self.assertIn("1 succeeded, 1 failed", stdout)
        self.assertIn("ERROR 00_bad.pdf", stderr)
        self.assertNotIn("Traceback", stderr)
        self.assertTrue((output / "01_good.pdf.json").is_file())
        self.assertFalse((output / "00_bad.pdf.json").exists())

    def test_output_path_that_is_a_file_reports_error(self):
        output = self.work / "blocked"
        output.write_text("keep me", encoding="utf-8")
        code, _, stderr = self.run_cli(
            "extract", str(SAMPLES / "01_invoice_pln.pdf"),
            "--output-dir", str(output),
        )
        self.assertEqual(code, 1)
        self.assertIn("ERROR", stderr)
        self.assertEqual(output.read_text(encoding="utf-8"), "keep me")

    def test_cli_warns_about_textless_page(self):
        path = self.work / "mixed.pdf"
        with pdfium.PdfDocument.new() as combined:
            with pdfium.PdfDocument(SAMPLES / "01_invoice_pln.pdf") as source:
                combined.import_pages(source)
            combined.new_page(300, 300).close()
            combined.save(path)
        code, _, stderr = self.run_cli(
            "extract", str(path), "--output-dir", str(self.work / "results"),
        )
        self.assertEqual(code, 0)
        self.assertIn("WARNING mixed.pdf: Page 2", stderr)

    def test_module_entry_point_processes_all_five_samples(self):
        output = self.work / "results"
        process = subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "document_automation",
             "extract", str(SAMPLES), "--output-dir", str(output)],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
            env={**os.environ, "PYTHONUTF8": "1"}, check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("5 succeeded, 0 failed", process.stdout)
        self.assertEqual(len(list(output.glob("*.json"))), 5)
        self.assertEqual(len(list(output.glob("*.txt"))), 5)


if __name__ == "__main__":
    unittest.main()
