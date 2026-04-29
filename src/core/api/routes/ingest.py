from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from core.clawbot.dependencies import get_container_from_request
from core.clawbot.schemas import IngestResponse, SessionReplyResponse

router = APIRouter()


@router.post("/{session_id}/ingest", response_model=IngestResponse)
async def ingest_message(
    session_id: str,
    text: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    container=Depends(get_container_from_request),
) -> IngestResponse:
    try:
        return await container.clawbot_service.ingest(
            session_id=session_id,
            text=text,
            upload=file,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{session_id}/reply", response_model=SessionReplyResponse)
async def reply_message(
    session_id: str,
    text: str = Form(...),
    container=Depends(get_container_from_request),
) -> SessionReplyResponse:
    try:
        return await container.clawbot_service.reply(session_id=session_id, text=text)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
