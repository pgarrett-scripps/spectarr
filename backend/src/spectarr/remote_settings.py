"""Read the shared download limit without restarting API workers."""
from .config import get_settings
from .database import SessionLocal
from .models import RemoteDownloadSettings

MAX_CONCURRENCY = 8


def download_settings():
    settings = get_settings()
    with SessionLocal() as session:
        saved = session.get(RemoteDownloadSettings, 1)
        return {
            "concurrency": saved.concurrency if saved else settings.remote_download_concurrency,
            "default_concurrency": settings.remote_download_concurrency,
            "overridden": saved is not None,
            "enabled": settings.remote_imports_enabled and not settings.restore_mode,
            "restore_mode": settings.restore_mode,
        }
