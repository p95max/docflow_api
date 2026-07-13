from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, RedirectResponse


FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"
INDEX_FILE = FRONTEND_DIR / "index.html"

router = APIRouter(include_in_schema=False)


@router.get("/")
def frontend_root() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=302)


@router.get("/login")
@router.get("/register")
@router.get("/documents")
@router.get("/documents/upload")
@router.get("/documents/{document_id}")
def frontend_page() -> FileResponse:
    """Return the Bootstrap application shell for a client-side page."""
    return FileResponse(INDEX_FILE, media_type="text/html")
