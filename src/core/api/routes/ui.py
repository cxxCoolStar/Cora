from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from core.clawbot.dependencies import get_container_from_request

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def chat_page(request: Request, container=Depends(get_container_from_request)) -> HTMLResponse:
    templates = Jinja2Templates(directory=container.templates_dir)
    return templates.TemplateResponse(
        request=request,
        name="chat.html",
        context={},
    )


@router.get("/debug", response_class=HTMLResponse)
def debug_page(
    request: Request,
    session_id: str | None = None,
    container=Depends(get_container_from_request),
) -> HTMLResponse:
    templates = Jinja2Templates(directory=container.templates_dir)
    sessions = container.clawbot_service.list_sessions()
    selected = None
    if session_id:
        selected = container.clawbot_service.get_session_debug(session_id=session_id)
    elif sessions:
        selected = container.clawbot_service.get_session_debug(session_id=sessions[0].id)
    return templates.TemplateResponse(
        request=request,
        name="debug.html",
        context={
            "sessions": sessions,
            "selected_session": selected,
        },
    )
