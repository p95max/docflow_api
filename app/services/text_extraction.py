from pathlib import Path
from typing import TYPE_CHECKING

import fitz
import pytesseract
from PIL import Image, UnidentifiedImageError
from pytesseract import TesseractError

from app.core.config import settings

if TYPE_CHECKING:
    from app.models.document import Document


SUPPORTED_TEXT_EXTRACTION_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
}


def extract_text_from_document(document: "Document") -> str:
    """Extract text from a stored document using only local processing."""
    if not document.storage_key:
        raise ValueError("Document has no storage key.")

    if document.content_type not in SUPPORTED_TEXT_EXTRACTION_MIME_TYPES:
        raise ValueError(f"Unsupported document content type: {document.content_type}")

    file_path = Path(settings.local_storage_path) / document.storage_key

    if not file_path.exists():
        raise FileNotFoundError(f"Stored file does not exist: {document.storage_key}")

    return extract_text_from_file(
        file_path=file_path,
        content_type=document.content_type,
    )


def extract_text_from_file(file_path: Path, content_type: str) -> str:
    """Extract plain text from a local PDF, JPG or PNG file."""
    if content_type == "application/pdf":
        return _extract_text_from_pdf(file_path)

    if content_type in {"image/jpeg", "image/png"}:
        return _extract_text_from_image(file_path)

    raise ValueError(f"Unsupported file content type: {content_type}")


def _extract_text_from_pdf(file_path: Path) -> str:
    try:
        with fitz.open(file_path) as pdf_document:
            pages_text = [
                page.get_text("text").strip()
                for page in pdf_document
            ]
    except Exception as exc:
        raise ValueError(f"Could not extract text from PDF: {file_path.name}") from exc

    return _normalize_extracted_text("\n\n".join(pages_text))


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
    except TesseractError as exc:
        if settings.local_ocr_languages == "eng":
            raise RuntimeError("Local OCR failed.") from exc

        try:
            return pytesseract.image_to_string(image, lang="eng")
        except TesseractError as fallback_exc:
            raise RuntimeError("Local OCR failed.") from fallback_exc


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