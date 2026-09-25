from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import asynccontextmanager, suppress

from anyio import to_thread

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from . import __version__
from .api import router
from .backup_api import router as backup_router
from .external_api import router as external_router
from .remote_api import router as remote_router
from .remote_worker import tick as download_tick
from .remote_settings import MAX_CONCURRENCY
from .backup_service import BackupService
from .auth import ensure_local_user, require_request_access
from .config import get_settings
from .database import SessionLocal
from .migrations import run_migrations
from .processing import ensure_builtin_profiles
from .platform_api import auth_router, platform_router
from .storage import LocalArtifactStorage
from .locking import maintenance_lock
from .library_publication import LibraryPublication
from .maintenance import guard_storage_mutation, sweep_storage
from .library import LibraryMaterializer
from .browser_security import browser_request_error


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
    with maintenance_lock(settings.storage_root, exclusive=True, blocking=True), SessionLocal() as session:
        storage = LocalArtifactStorage(
            settings.storage_root, settings.library_root, settings.library_link_mode,
            settings.library_project_template, settings.library_filename_template,
        )
        LibraryPublication(storage).recover(session)
        if not settings.restore_mode:
            LibraryMaterializer(storage).refresh_run_summaries(session)
    with SessionLocal() as session:
        if settings.restore_mode:
            from .external_api import reset_restored_inventory

            reset_restored_inventory(session)
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
    async def maintenance_loop():
        while True:
            try:
                await to_thread.run_sync(sweep_storage)
            except Exception:
                logging.getLogger(__name__).exception("Storage maintenance failed")
            await asyncio.sleep(300)

    async def backup_loop():
        while True:
            try:
                await to_thread.run_sync(BackupService().tick)
            except Exception:
                logging.getLogger(__name__).exception("Backup scheduler failed")
            await asyncio.sleep(5)

    download_stop = threading.Event()

    async def download_loop(slot: int):
        while not download_stop.is_set():
            try:
                await to_thread.run_sync(download_tick, download_stop, slot)
            except Exception:
                logging.getLogger(__name__).exception("Download worker failed")
            await asyncio.sleep(2)

    download_tasks = [asyncio.create_task(download_loop(slot)) for slot in range(MAX_CONCURRENCY)] if not settings.restore_mode and settings.remote_imports_enabled else []
    maintenance_task = asyncio.create_task(maintenance_loop())
    backup_task = asyncio.create_task(backup_loop()) if not settings.restore_mode else None
    try:
        yield
    finally:
        download_stop.set()
        tasks = download_tasks + [maintenance_task] + ([backup_task] if backup_task else [])
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task


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
    if error := browser_request_error(request, get_settings()):
        return JSONResponse({"detail": error}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.scope['path'].startswith(settings.api_prefix):
        response.headers["Cache-Control"] = "no-store"
    if settings.dashboard_root and not request.url.path.startswith((settings.api_prefix, "/docs", "/redoc")):
        response.headers["Content-Security-Policy"] = chr(59).join([
            "default-src 'self'", "script-src 'self'", "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data:", "font-src 'self'", "connect-src 'self'",
            "object-src 'none'", "base-uri 'none'", "frame-ancestors 'none'", "form-action 'self'",
        ])
    return response
app.include_router(auth_router, prefix=settings.api_prefix)
app.include_router(backup_router, prefix=settings.api_prefix)
app.include_router(router, prefix=settings.api_prefix, dependencies=[Depends(require_request_access), Depends(guard_storage_mutation)])
app.include_router(external_router, prefix=settings.api_prefix, dependencies=[Depends(require_request_access), Depends(guard_storage_mutation)])
app.include_router(remote_router, prefix=settings.api_prefix, dependencies=[Depends(require_request_access), Depends(guard_storage_mutation)])
app.include_router(platform_router, prefix=settings.api_prefix, dependencies=[Depends(require_request_access), Depends(guard_storage_mutation)])


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
