from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1.routes import router as api_router
from app.core.config import settings
from app.web import FRONTEND_DIR, router as web_router
from app.web_backups import router as web_backups_router

app = FastAPI(
    title="DocsFlow API",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["system"])
def health_check() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(
    api_router,
    prefix="/api/v1",
)

app.mount(
    "/assets",
    StaticFiles(directory=FRONTEND_DIR / "assets"),
    name="frontend_assets",
)
app.include_router(web_router)
app.include_router(web_backups_router)
