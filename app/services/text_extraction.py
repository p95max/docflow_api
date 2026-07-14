from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import fitz
import pytesseract
from PIL import Image, UnidentifiedImageError
from pytesseract import TesseractError, TesseractNotFoundError

from app.core.config import settings

if TYPE_CHECKING:
    from app.models.document import Document


SUPPORTED_TEXT_EXTRACTION_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
}


class LocalOCRError(RuntimeError):
    """Raised when the local Tesseract OCR engine cannot process a document."""


@dataclass(frozen=True)
class ExtractedTextPage:
    page_number: int
    text: str


def extract_text_from_document(document: "Document") -> str:
    """Extract text from a stored document using only local processing."""
    if not document.storage_key:
        raise ValueError("Document has no storage key.")

    if document.content_type not in SUPPORTED_TEXT_EXTRACTION_MIME_TYPES:
        raise ValueError(f"Unsupported document content type: {document.content_type}")

    file_path = Path(settings.local_storage_path) / document.storage_key

    if not file_path.exists():
        raise FileNotFoundError(f"Stored file does not exist: {document.storage_key}")

    pages = extract_text_pages_from_file(
        file_path=file_path,
        content_type=document.content_type,
    )
    return _join_extracted_pages(pages)


def extract_text_pages_from_document(document: "Document") -> list[ExtractedTextPage]:
    """Extract local text while preserving source page numbers for indexing."""
    if not document.storage_key:
        raise ValueError("Document has no storage key.")

    if document.content_type not in SUPPORTED_TEXT_EXTRACTION_MIME_TYPES:
        raise ValueError(f"Unsupported document content type: {document.content_type}")

    file_path = Path(settings.local_storage_path) / document.storage_key

    if not file_path.exists():
        raise FileNotFoundError(f"Stored file does not exist: {document.storage_key}")

    return extract_text_pages_from_file(
        file_path=file_path,
        content_type=document.content_type,
    )


def extract_text_from_file(file_path: Path, content_type: str) -> str:
    """Extract plain text from a local PDF, JPG or PNG file."""
    return _join_extracted_pages(
        extract_text_pages_from_file(
            file_path=file_path,
            content_type=content_type,
        )
    )


def extract_text_pages_from_file(
    file_path: Path,
    content_type: str,
) -> list[ExtractedTextPage]:
    """Extract plain text with page numbers; images are represented as page 1."""
    if content_type == "application/pdf":
        return _extract_text_pages_from_pdf(file_path)

    if content_type in {"image/jpeg", "image/png"}:
        return [
            ExtractedTextPage(
                page_number=1,
                text=_extract_text_from_image(file_path),
            )
        ]

    raise ValueError(f"Unsupported file content type: {content_type}")


def _extract_text_pages_from_pdf(file_path: Path) -> list[ExtractedTextPage]:
    try:
        with fitz.open(file_path) as pdf_document:
            pages: list[ExtractedTextPage] = []

            for page_number, page in enumerate(pdf_document, start=1):
                page_text = page.get_text("text").strip()

                if not page_text:
                    page_text = _extract_text_from_pdf_page_image(page)

                pages.append(
                    ExtractedTextPage(
                        page_number=page_number,
                        text=_normalize_extracted_text(page_text),
                    )
                )
    except LocalOCRError:
        raise
    except Exception as exc:
        raise ValueError(f"Could not extract text from PDF: {file_path.name}") from exc

    return pages


def _extract_text_from_pdf_page_image(page: fitz.Page) -> str:
    pixmap = page.get_pixmap(
        matrix=fitz.Matrix(2.5, 2.5),
        colorspace=fitz.csRGB,
        alpha=False,
    )
    image = Image.frombytes(
        "RGB",
        (pixmap.width, pixmap.height),
        pixmap.samples,
    )

    try:
        return _run_tesseract_ocr(image).strip()
    finally:
        image.close()


def _extract_text_from_image(file_path: Path) -> str:
    try:
        with Image.open(file_path) as image:
            image.load()
            prepared_image = image.convert("RGB")
            extracted_text = _run_tesseract_ocr(prepared_image)
    except UnidentifiedImageError as exc:
        raise ValueError(f"Could not read image file: {file_path.name}") from exc

    return _normalize_extracted_text(extracted_text)


def _run_tesseract_ocr(image: Image.Image) -> str:
    try:
        return pytesseract.image_to_string(
            image,
            lang=settings.local_ocr_languages,
        )
    except TesseractNotFoundError as exc:
        raise LocalOCRError("Tesseract OCR is not installed.") from exc
    except TesseractError as exc:
        if settings.local_ocr_languages == "eng":
            raise LocalOCRError("Local OCR failed.") from exc

        try:
            return pytesseract.image_to_string(image, lang="eng")
        except TesseractNotFoundError as fallback_exc:
            raise LocalOCRError("Tesseract OCR is not installed.") from fallback_exc
        except TesseractError as fallback_exc:
            raise LocalOCRError("Local OCR failed.") from fallback_exc


def _normalize_extracted_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    normalized_lines: list[str] = []

    previous_line_empty = False

    for line in lines:
        if not line:
            if not previous_line_empty and normalized_lines:
                normalized_lines.append("")
            previous_line_empty = True
            continue

        normalized_lines.append(line)
        previous_line_empty = False

    return "\n".join(normalized_lines).strip()


def _join_extracted_pages(pages: list[ExtractedTextPage]) -> str:
    return _normalize_extracted_text(
        "\n\n".join(page.text for page in pages if page.text)
    )
