from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from . import __version__
from .api import router
from .auth import ensure_local_user, require_request_access
from .config import get_settings
from .database import SessionLocal
from .migrations import run_migrations
from .processing import ensure_builtin_profiles
from .platform_api import auth_router, platform_router
from .storage import LocalArtifactStorage


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    if settings.database_url.startswith("sqlite"):
        database_path = settings.database_url.removeprefix("sqlite:///")
        if database_path != ":memory:":
            from pathlib import Path

            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    run_migrations()
    with SessionLocal() as session:
        ensure_builtin_profiles(session)
        if settings.effective_auth_mode == "local":
            ensure_local_user(session, settings)
    LocalArtifactStorage(
        settings.storage_root,
        settings.library_root,
        settings.library_link_mode,
        settings.library_project_template,
        settings.library_filename_template,
    )
    yield


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version=__version__,
    description="Self-hosted mass spectrometry data library and processing API",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.url.path.startswith(f"{settings.api_prefix}/auth/"):
        response.headers["Cache-Control"] = "no-store"
    return response
app.include_router(auth_router, prefix=settings.api_prefix)
app.include_router(router, prefix=settings.api_prefix, dependencies=[Depends(require_request_access)])
app.include_router(platform_router, prefix=settings.api_prefix, dependencies=[Depends(require_request_access)])


@app.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


if settings.dashboard_root and settings.dashboard_root.is_dir():
    dashboard_root = settings.dashboard_root.resolve()

    @app.get("/{dashboard_path:path}", include_in_schema=False)
    async def dashboard(dashboard_path: str) -> FileResponse:
        if dashboard_path == "api" or dashboard_path.startswith("api/"):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
        requested = (dashboard_root / dashboard_path).resolve()
        if requested.is_relative_to(dashboard_root) and requested.is_file():
            return FileResponse(requested)
        return FileResponse(dashboard_root / "index.html")
