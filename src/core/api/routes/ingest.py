from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from core.clawbot.dependencies import get_container_from_request
from core.clawbot.schemas import TurnResponse

router = APIRouter()


@router.post("/{session_id}/ingest", response_model=TurnResponse)
async def ingest_message(
    session_id: str,
    text: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    container=Depends(get_container_from_request),
) -> TurnResponse:
    try:
        return await container.clawbot_service.ingest(
            session_id=session_id,
            text=text,
            upload=file,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        # Treat validation/parsing errors as client input errors instead of 500.
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{session_id}/reply", response_model=TurnResponse)
async def reply_message(
    session_id: str,
    text: str = Form(...),
    container=Depends(get_container_from_request),
) -> TurnResponse:
    try:
        return await container.clawbot_service.reply(session_id=session_id, text=text)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
