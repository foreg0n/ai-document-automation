"""Deterministic demo-policy checks. Passing is not proof of invoice accuracy."""
import re
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal

from .invoice import FIELD_DESCRIPTIONS

RULES_VERSION = "invoice_demo_v1"
SUPPORTED_CURRENCIES = frozenset({"PLN", "EUR", "USD"})
PLACEHOLDERS = frozenset({"null", "none", "n/a", "na", "unknown", "not provided", "not available", "missing", "-", "?"})
DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
AMOUNT_PATTERN = re.compile(r"-?(?:0|[1-9][0-9]*)\.[0-9]{2}")


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    field: str | None
    message: str


@dataclass(frozen=True)
class ValidationReport:
    reference_date: date
    issues: tuple[ValidationIssue, ...]

    @property
    def requires_review(self) -> bool:
        return bool(self.issues)

    @property
    def status(self) -> str:
        return "needs_review" if self.requires_review else "passed"

    def to_dict(self) -> dict:
        return {
            "rules_version": RULES_VERSION,
            "reference_date": self.reference_date.isoformat(),
            "status": self.status,
            "requires_review": self.requires_review,
            "issues": [asdict(issue) for issue in self.issues],
        }


def validate_invoice(
    fields: object, *, reference_date: date, extraction_warnings: tuple[str, ...] = (),
) -> ValidationReport:
    """Return review reasons without modifying values or consulting a model.

    The caller supplies the comparison date so the same inputs give the same
    report. The currency list and positive-total rule are this demo's policy,
    not universal accounting rules.
    """
    issues = []

    def add(code: str, field: str | None, message: str) -> None:
        issues.append(ValidationIssue(code, field, message))

    usable = {}
    if not isinstance(fields, dict):
        add("invalid_structure", None, "Invoice fields must be a JSON object.")
    else:
        for name in sorted(set(fields) - set(FIELD_DESCRIPTIONS), key=str):
            add("unexpected_field", str(name), "Unexpected field outside the five-field contract.")
        for name in FIELD_DESCRIPTIONS:
            value = fields.get(name)
            if value is None:
                add("missing_value", name, "Required invoice value is missing; check the source document.")
            elif not isinstance(value, str):
                add("invalid_type", name, "Expected a string or null.")
            elif not value.strip() or value.strip().casefold() in PLACEHOLDERS:
                add("placeholder_value", name, "Empty text or a placeholder is not an extracted value.")
            elif value != value.strip():
                add("surrounding_whitespace", name, "Value has leading or trailing whitespace; review before normalization.")
            else:
                usable[name] = value

    value = usable.get("invoice_date")
    if value is not None:
        if not DATE_PATTERN.fullmatch(value):
            add("invalid_date_format", "invoice_date", "Use the exact date format YYYY-MM-DD.")
        else:
            try:
                invoice_date = date.fromisoformat(value)
            except ValueError:
                add("invalid_date", "invoice_date", "This calendar date does not exist.")
            else:
                if invoice_date > reference_date:
                    add("future_date", "invoice_date", "Invoice date is later than the validation reference date.")

    value = usable.get("total_amount")
    if value is not None:
        if not AMOUNT_PATTERN.fullmatch(value):
            add("invalid_amount_format", "total_amount", "Use a decimal string with exactly two places and no grouping, e.g. 1234.50.")
        elif Decimal(value) <= 0:
            add("non_positive_amount", "total_amount", "Zero or negative totals need review; credit notes are outside this demo policy.")

    value = usable.get("currency")
    if value is not None:
        if not re.fullmatch(r"[A-Z]{3}", value):
            add("invalid_currency_format", "currency", "Use a three-letter uppercase currency code.")
        elif value not in SUPPORTED_CURRENCIES:
            add("unsupported_currency", "currency", "This demo supports PLN, EUR, and USD; review other currencies.")

    for warning in dict.fromkeys(extraction_warnings):
        add("text_extraction_warning", None, warning)
    return ValidationReport(reference_date, tuple(issues))
