"""Minimal one-page PDF writer used only to create synthetic test fixtures.

The production document parser will be a separate component. This helper
supports ASCII text and basic shapes, which is sufficient for these samples.
"""
from pathlib import Path

PAGE_WIDTH = 595.28
PAGE_HEIGHT = 841.89


def escape_pdf_text(value: str) -> str:
    """Escape characters that have a special meaning in a PDF string."""
    value.encode("ascii")  # Fail clearly instead of silently damaging text.
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


class PdfPage:
    """Collect drawing commands using coordinates measured from the top."""

    def __init__(self) -> None:
        self.commands: list[str] = []

    def rect(
        self, x: float, y: float, width: float, height: float,
        color: tuple[float, float, float],
    ) -> None:
        red, green, blue = color
        self.commands.append(
            f"{red:.3f} {green:.3f} {blue:.3f} rg "
            f"{x:.2f} {PAGE_HEIGHT - y - height:.2f} "
            f"{width:.2f} {height:.2f} re f"
        )

    def line(self, x1: float, y1: float, x2: float, y2: float) -> None:
        self.commands.append(
            f"0.82 0.85 0.89 RG 0.7 w "
            f"{x1:.2f} {PAGE_HEIGHT - y1:.2f} m "
            f"{x2:.2f} {PAGE_HEIGHT - y2:.2f} l S"
        )

    def text(
        self, x: float, y: float, value: str, size: int = 11,
        bold: bool = False, color: tuple[float, float, float] = (0.13, 0.19, 0.27),
    ) -> None:
        red, green, blue = color
        font = "F2" if bold else "F1"
        self.commands.append(
            f"BT /{font} {size} Tf {red:.3f} {green:.3f} {blue:.3f} rg "
            f"1 0 0 1 {x:.2f} {PAGE_HEIGHT - y:.2f} Tm "
            f"({escape_pdf_text(value)}) Tj ET"
        )

    def save(self, path: Path) -> None:
        """Write a valid PDF with fonts, a text stream and object offsets."""
        stream = "\n".join(self.commands).encode("ascii")
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox "
                f"[0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
                "/Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> "
                "/Contents 6 0 R >>"
            ).encode("ascii"),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii")
            + stream + b"\nendstream",
        ]
        output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0]
        for index, obj in enumerate(objects, start=1):
            offsets.append(len(output))
            output.extend(f"{index} 0 obj\n".encode("ascii") + obj + b"\nendobj\n")
        xref_offset = len(output)
        output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
        output.extend(b"0000000000 65535 f \n")
        for offset in offsets[1:]:
            output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
        output.extend(
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
        )
        path.write_bytes(output)
