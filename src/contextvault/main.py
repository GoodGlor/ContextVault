"""FastAPI application entrypoint."""

from fastapi import FastAPI

from contextvault.api.health import router as health_router
from contextvault.core.config import get_settings


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = get_settings()
    app = FastAPI(title=settings.app_name)
    app.include_router(health_router)
    return app


app = create_app()
