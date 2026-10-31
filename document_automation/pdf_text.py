"""Extract a PDF's text layer without reference answers or AI."""
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path

import pypdfium2 as pdfium


class TextExtractionError(Exception):
    """A document could not be read; its message is suitable for the CLI."""


@dataclass(frozen=True)
class PageText:
    page_number: int
    text: str


@dataclass(frozen=True)
class ExtractedDocument:
    source_file: str
    pages: tuple[PageText, ...]
    warnings: tuple[str, ...]

    @property
    def text(self) -> str:
        """Join pages without changing dates, amounts, or other content."""
        return "\n\n".join(page.text for page in self.pages)

    def to_dict(self) -> dict:
        """Produce JSON-compatible data for the next pipeline stage."""
        return {
            "schema_version": 1,
            "source_file": self.source_file,
            "page_count": len(self.pages),
            "text": self.text,
            "pages": [asdict(page) for page in self.pages],
            "warnings": list(self.warnings),
        }


def extract_pdf_text(path: Path, *, content: bytes | None = None) -> ExtractedDocument:
    """Read all pages; reject unreadable, password-locked, and textless PDFs."""
    path = Path(path)
    if content is None and not path.exists():
        raise TextExtractionError(f"File not found: {path}")
    if content is None and not path.is_file():
        raise TextExtractionError(f"Expected a PDF file: {path}")
    if path.suffix.lower() != ".pdf":
        raise TextExtractionError(f"Only .pdf files are supported: {path.name}")

    pages = []
    warnings = []
    try:
        # A supplied snapshot makes hashing and extraction use identical bytes.
        with pdfium.PdfDocument(path if content is None else content) as reader:
            if len(reader) == 0:
                raise TextExtractionError("The PDF contains no pages.")
            for index in range(len(reader)):
                # PDFium uses native resources: release each page immediately.
                with closing(reader[index]) as page:
                    with closing(page.get_textpage()) as text_page:
                        text = text_page.get_text_bounded(errors="strict")
                # Normalize line endings only; preserve the invoice content.
                text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
                pages.append(PageText(page_number=index + 1, text=text))
                if not text:
                    warnings.append(
                        f"Page {index + 1} has no extractable text; "
                        "it may be blank or need OCR."
                    )
    except pdfium.PdfiumError as exc:
        if exc.err_code == pdfium.raw.FPDF_ERR_PASSWORD:
            raise TextExtractionError(
                "This PDF requires a password. Password input is not supported "
                "at this stage; export an unlocked copy first."
            ) from exc
        raise TextExtractionError(
            "Cannot parse this PDF. It may be damaged or use unsupported features."
        ) from exc
    except OSError as exc:
        raise TextExtractionError(f"Cannot open or read the PDF: {exc}") from exc
    except (ValueError, UnicodeError) as exc:
        raise TextExtractionError("Cannot decode this PDF's text layer.") from exc

    if not any(page.text for page in pages):
        raise TextExtractionError(
            "No extractable text was found. The PDF may be blank or contain "
            "only images. Scans need OCR, which is not included in stage 2."
        )
    return ExtractedDocument(path.name, tuple(pages), tuple(warnings))
