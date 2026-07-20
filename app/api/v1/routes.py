from fastapi import APIRouter

from app.api.v1 import (
    routes_auth,
    routes_backups,
    routes_calendar,
    routes_document_ai_analysis,
    routes_document_deletion,
    routes_documents,
    routes_knowledge,
    routes_users,
)

router = APIRouter()

router.include_router(
    routes_auth.router,
    prefix="/auth",
    tags=["auth"],
)

router.include_router(
    routes_users.router,
    prefix="/users",
    tags=["users"],
)

router.include_router(
    routes_calendar.router,
    prefix="/calendar",
    tags=["calendar"],
)

router.include_router(
    routes_documents.router,
    prefix="/documents",
    tags=["documents"],
)

router.include_router(
    routes_document_ai_analysis.router,
    prefix="/documents",
    tags=["documents"],
)

router.include_router(
    routes_document_deletion.router,
    prefix="/documents",
    tags=["documents"],
)

router.include_router(
    routes_backups.router,
    prefix="/backups",
    tags=["backups"],
)

router.include_router(
    routes_knowledge.router,
    prefix="/knowledge",
    tags=["knowledge"],
)
