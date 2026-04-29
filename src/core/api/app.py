from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from core.api.routes import debug_retrieval, ingest, sessions, ui
from core.clawbot.dependencies import get_clawbot_container


def create_app() -> FastAPI:
    app = FastAPI(title="ClawBot API", version="0.1.0")
    container = get_clawbot_container()
    container.initialize()
    app.state.container = container
    app.include_router(ui.router)
    app.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
    app.include_router(ingest.router, prefix="/sessions", tags=["ingest"])
    app.include_router(debug_retrieval.router, tags=["debug"])
    app.mount("/static", StaticFiles(directory=container.templates_static_dir), name="static")
    return app


app = create_app()
