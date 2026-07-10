"""FastAPI application entrypoint."""

from fastapi import FastAPI

from contextvault.api.auth import router as auth_router
from contextvault.api.health import router as health_router
from contextvault.api.invitations import router as invitations_router
from contextvault.api.query import router as query_router
from contextvault.api.repositories import router as repositories_router
from contextvault.api.sources import router as sources_router
from contextvault.core.config import get_settings


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = get_settings()
    app = FastAPI(title=settings.app_name)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(invitations_router)
    app.include_router(sources_router)
    app.include_router(repositories_router)
    app.include_router(query_router)
    return app


app = create_app()
