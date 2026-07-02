"""FastAPI application factory and route wiring."""

from __future__ import annotations

from fastapi import FastAPI

from app.logging import configure_logging, get_logger

logger = get_logger(__name__)


def create_app() -> FastAPI:
    """Build the FastAPI app and wire routers.

    Routers are attached as their implementation units land (interactions in
    U3/U8/U9, admin in U10). U1 ships only the health check.
    """
    configure_logging()
    app = FastAPI(title="GitHub Activity Digest Bot", version="0.1.0")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # --- Routers (attached by later units, guarded so the app boots at any
    # point during the build) ---
    try:
        from app.discord.interactions import router as interactions_router

        app.include_router(interactions_router)
    except ImportError:  # interactions endpoint (U3) not yet present
        pass

    try:
        from app.discord.handlers import install_handlers

        install_handlers()
    except ImportError:  # command handlers (U7) not yet present
        pass

    try:
        from app.digest.pipeline import install_digest

        install_digest()
    except ImportError:  # digest pipeline (U9) not yet present
        pass

    try:
        from app.admin.api import router as admin_router

        app.include_router(admin_router)
    except ImportError:  # admin panel (U10) not yet present
        pass

    logger.info("app_initialized")
    return app


app = create_app()
