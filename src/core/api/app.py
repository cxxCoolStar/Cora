from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from core.api.routes import ingest, sessions
from core.clawbot.dependencies import get_clawbot_container


def create_app() -> FastAPI:
    container = get_clawbot_container()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        container.initialize()
        yield

    app = FastAPI(title="ClawBot API", version="0.1.0", lifespan=lifespan)
    app.state.container = container
    app.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
    app.include_router(ingest.router, prefix="/sessions", tags=["ingest"])
    return app


app = create_app()
