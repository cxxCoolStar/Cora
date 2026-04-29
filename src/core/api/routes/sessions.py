from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from core.clawbot.dependencies import get_container_from_request
from core.clawbot.schemas import CreateSessionResponse, ItemDetailResponse, ItemSummaryResponse

router = APIRouter()


@router.post("", response_model=CreateSessionResponse)
def create_session(container=Depends(get_container_from_request)) -> CreateSessionResponse:
    session = container.clawbot_service.create_session()
    return CreateSessionResponse(session_id=session.id)


@router.get("/{session_id}/items", response_model=list[ItemSummaryResponse])
def list_items(session_id: str, container=Depends(get_container_from_request)) -> list[ItemSummaryResponse]:
    try:
        return container.clawbot_service.list_items(session_id=session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{session_id}/items/{item_id}", response_model=ItemDetailResponse)
def get_item(
    session_id: str,
    item_id: str,
    container=Depends(get_container_from_request),
) -> ItemDetailResponse:
    try:
        return container.clawbot_service.get_item(session_id=session_id, item_id=item_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
