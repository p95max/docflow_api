from pathlib import Path

import fitz
import pytest
from PIL import Image

from app.services import text_extraction
from app.services.text_extraction import extract_text_from_file


PDF_WITH_TEXT_BYTES = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>
endobj
4 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj
5 0 obj
<< /Length 72 >>
stream
BT
/F1 24 Tf
72 720 Td
(Invoice number 12345) Tj
0 -30 Td
(Amount 99.95 EUR) Tj
ET
endstream
endobj
xref
0 6
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000241 00000 n 
0000000311 00000 n 
trailer
<< /Size 6 /Root 1 0 R >>
startxref
433
%%EOF
"""


def test_extract_text_from_pdf_returns_plain_text(tmp_path: Path) -> None:
    file_path = tmp_path / "invoice.pdf"
    file_path.write_bytes(PDF_WITH_TEXT_BYTES)

    text = extract_text_from_file(
        file_path=file_path,
        content_type="application/pdf",
    )

    assert "Invoice number 12345" in text
    assert "Amount 99.95 EUR" in text


def test_extract_text_from_scanned_pdf_uses_local_ocr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "scan.png"
    Image.new("RGB", (100, 50), color="white").save(image_path)

    file_path = tmp_path / "scan.pdf"
    with fitz.open() as pdf_document:
        page = pdf_document.new_page(width=200, height=100)
        page.insert_image(page.rect, filename=str(image_path))
        pdf_document.save(file_path)

    received_images: list[Image.Image] = []

    def fake_run_tesseract_ocr(image: Image.Image) -> str:
        received_images.append(image.copy())
        return "  OCR result from scanned PDF\n"

    monkeypatch.setattr(
        text_extraction,
        "_run_tesseract_ocr",
        fake_run_tesseract_ocr,
    )

    text = extract_text_from_file(
        file_path=file_path,
        content_type="application/pdf",
    )

    assert text == "OCR result from scanned PDF"
    assert len(received_images) == 1
    assert received_images[0].mode == "RGB"


def test_extract_text_from_image_uses_local_ocr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file_path = tmp_path / "scan.png"
    Image.new("RGB", (20, 20), color="white").save(file_path)

    def fake_image_to_string(*args, **kwargs) -> str:
        return "  OCR result from local engine\n\n"

    monkeypatch.setattr(
        text_extraction.pytesseract,
        "image_to_string",
        fake_image_to_string,
    )

    text = extract_text_from_file(
        file_path=file_path,
        content_type="image/png",
    )

    assert text == "OCR result from local engine"


def test_extract_text_rejects_unsupported_content_type(tmp_path: Path) -> None:
    file_path = tmp_path / "notes.txt"
    file_path.write_text("plain text")

    with pytest.raises(ValueError, match="Unsupported file content type"):
        extract_text_from_file(
            file_path=file_path,
            content_type="text/plain",
        )
