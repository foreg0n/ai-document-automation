"""Generate the five synthetic invoices and their reference answers."""
import csv
import hashlib
import json
import shutil
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from fixture_pdf import PdfPage

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "data" / "samples"
FIELD_NAMES = ("supplier", "invoice_number", "invoice_date", "total_amount", "currency")

# Explicit, readable source data. These are not real companies or invoices.
INVOICES = [
    {
        "file": "01_invoice_pln.pdf",
        "supplier": "Northstar Office Demo",
        "invoice_number": "DEMO-2026-001",
        "invoice_date": "2026-10-20",
        "total_amount": "1234.50",
        "currency": "PLN",
        "layout": "classic",
        "services": ["Office equipment setup", "Workstation configuration"],
    },
    {
        "file": "02_invoice_eur.pdf",
        "supplier": "Blue Harbor Studio Demo",
        "invoice_number": "DEMO-2026-002",
        "invoice_date": "2026-10-21",
        "total_amount": "980.00",
        "currency": "EUR",
        "layout": "compact",
        "services": ["Landing page design", "Frontend implementation"],
    },
    {
        "file": "03_invoice_usd.pdf",
        "supplier": "Cedar Cloud Services Demo",
        "invoice_number": "DEMO-2026-003",
        "invoice_date": "2026-10-22",
        "total_amount": "2750.00",
        "currency": "USD",
        "layout": "table",
        "services": ["Cloud environment setup", "Application deployment"],
    },
    {
        "file": "04_invoice_missing_total.pdf",
        "supplier": "Riverbend Analytics Demo",
        "invoice_number": "DEMO-2026-004",
        "invoice_date": "2026-10-23",
        "total_amount": None,
        "currency": "PLN",
        "layout": "classic",
        "services": ["Data quality review", "Reporting automation"],
    },
]


def format_amount(amount: str | None, currency: str, layout: str) -> str:
    """Format only the document display; reference amounts stay normalized."""
    if amount is None:
        return "Not provided"
    formatted = f"{Decimal(amount):,.2f}"
    if layout == "compact":
        formatted = formatted.replace(",", " ").replace(".", ",")
    return f"{formatted} {currency}"


def render_invoice(invoice: dict, destination: Path) -> None:
    """Draw the supplied fields as selectable text, with one of three layouts."""
    page = PdfPage()
    layout = invoice["layout"]
    accent = {
        "classic": (0.07, 0.34, 0.47),
        "compact": (0.18, 0.30, 0.48),
        "table": (0.12, 0.38, 0.30),
    }[layout]
    page.rect(0, 0, 595.28, 12, accent)
    page.text(44, 43, "AI DOCUMENT AUTOMATION / SYNTHETIC DATA", 9, color=accent)
    issue_date = date.fromisoformat(invoice["invoice_date"])

    if layout == "compact":
        page.text(44, 98, invoice["supplier"], 20, True)
        page.text(44, 122, "Supplier / design and development services", 10)
        page.text(388, 160, "INVOICE", 26, True, accent)
        page.text(44, 194, f"Invoice no: {invoice['invoice_number']}", 12, True)
        page.text(44, 218, f"Date of issue: {issue_date:%d.%m.%Y}", 11)
        page.text(330, 218, f"Currency: {invoice['currency']}", 11)
        page.line(44, 244, 551, 244)
    elif layout == "table":
        page.rect(44, 72, 507, 84, accent)
        page.text(60, 112, "INVOICE", 29, True, (1, 1, 1))
        page.text(60, 139, invoice["invoice_number"], 11, color=(1, 1, 1))
        page.text(44, 193, "ISSUED BY", 9, True)
        page.text(44, 216, invoice["supplier"], 17, True)
        page.text(44, 246, f"Invoice date: {issue_date:%B %d, %Y}", 11)
        page.text(318, 246, f"Due date: {issue_date + timedelta(days=14):%B %d, %Y}", 10)
    else:
        page.text(44, 102, "INVOICE", 30, True, accent)
        page.text(44, 133, invoice["invoice_number"], 12, True)
        page.text(44, 181, "SUPPLIER", 9, True)
        page.text(44, 205, invoice["supplier"], 18, True)
        page.text(44, 239, f"Invoice date: {invoice['invoice_date']}", 11)
        page.text(350, 239, f"Currency: {invoice['currency']}", 11)

    page.text(44, 293, "BILL TO", 9, True)
    page.text(44, 316, "Example Client Demo", 14, True)
    page.text(44, 337, "Internal demonstration project", 10)
    page.rect(44, 376, 507, 30, (0.93, 0.95, 0.97))
    page.text(58, 396, "SERVICES", 10, True, accent)
    for index, service in enumerate(invoice["services"], start=1):
        page.text(58, 431 + (index - 1) * 32, f"{index:02d}   {service}", 11)
    page.line(44, 493, 551, 493)
    page.text(44, 526, f"Billing currency: {invoice['currency']}", 11)
    page.rect(300, 552, 251, 84, (0.93, 0.95, 0.97))
    page.text(316, 578, "TOTAL AMOUNT", 10, True, accent)
    page.text(
        316, 612,
        format_amount(invoice["total_amount"], invoice["currency"], layout),
        21, True,
    )
    page.text(44, 686, "Service descriptions only; no line-item amounts are provided.", 10)
    page.line(44, 754, 551, 754)
    page.text(44, 777, "SYNTHETIC SAMPLE - NOT VALID FOR PAYMENT", 10, True, accent)
    page.text(44, 796, "Fictional supplier and client. No bank or tax information.", 9)
    page.save(destination)


def sha256_file(path: Path) -> str:
    """Return a fingerprint of the complete file bytes, not its filename."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_samples() -> list[dict]:
    """Create four original PDFs, then one exact duplicate of the first."""
    SAMPLES.mkdir(parents=True, exist_ok=True)
    cases = []
    for invoice in INVOICES:
        path = SAMPLES / invoice["file"]
        render_invoice(invoice, path)
        expected = {field: invoice[field] for field in FIELD_NAMES}
        cases.append({
            "file": invoice["file"],
            "scenario": "missing_total" if invoice["total_amount"] is None else "valid",
            "sha256": sha256_file(path),
            "expected_fields": expected,
            "expected_missing_fields": [key for key, value in expected.items() if value is None],
            "duplicate_of": None,
        })

    # Copy bytes: rendering a second document is not guaranteed to make an
    # identical file once a future renderer adds timestamps or metadata.
    duplicate_name = "05_invoice_duplicate.pdf"
    shutil.copyfile(SAMPLES / cases[0]["file"], SAMPLES / duplicate_name)
    cases.append({
        **cases[0],
        "file": duplicate_name,
        "scenario": "duplicate",
        "duplicate_of": cases[0]["file"],
    })
    return cases


def write_ground_truth(cases: list[dict]) -> None:
    """Write machine-readable JSON and a convenient spreadsheet-style CSV."""
    payload = {
        "schema_version": 1,
        "synthetic": True,
        "document_type": "invoice",
        "normalization": {
            "invoice_date": "YYYY-MM-DD",
            "total_amount": "Decimal string with two places; null when absent",
            "currency": "Three-letter uppercase currency code",
            "missing_values": "JSON null; never guess",
        },
        "cases": cases,
    }
    (ROOT / "data" / "ground_truth.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
    )
    # UTF-8 BOM makes the file easier to open in Excel on Windows.
    with (ROOT / "data" / "ground_truth.csv").open(
        "w", encoding="utf-8-sig", newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["file", *FIELD_NAMES, "scenario", "duplicate_of"],
        )
        writer.writeheader()
        for case in cases:
            writer.writerow({
                "file": case["file"], **case["expected_fields"],
                "scenario": case["scenario"], "duplicate_of": case["duplicate_of"],
            })


def main() -> None:
    cases = build_samples()
    write_ground_truth(cases)
    print(f"Created {len(cases)} PDFs in {SAMPLES}")
    print("Created data/ground_truth.json and data/ground_truth.csv")


if __name__ == "__main__":
    main()
