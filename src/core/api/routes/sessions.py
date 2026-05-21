from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from core.clawbot.dependencies import get_container_from_request
from core.clawbot.schemas import (
    AgentRunDetailResponse,
    AgentRunSummaryResponse,
    CreateSessionResponse,
    DeleteItemResponse,
    ItemDetailResponse,
    ItemSummaryResponse,
)

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


@router.delete("/{session_id}/items/{item_id}", response_model=DeleteItemResponse)
def delete_item(
    session_id: str,
    item_id: str,
    container=Depends(get_container_from_request),
) -> DeleteItemResponse:
    try:
        return container.clawbot_service.delete_item(session_id=session_id, item_id=item_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{session_id}/runs", response_model=list[AgentRunSummaryResponse])
def list_agent_runs(
    session_id: str,
    container=Depends(get_container_from_request),
) -> list[AgentRunSummaryResponse]:
    return container.clawbot_service.list_agent_runs(session_id=session_id)


@router.get("/{session_id}/runs/{run_id}", response_model=AgentRunDetailResponse)
def get_agent_run(
    session_id: str,
    run_id: str,
    container=Depends(get_container_from_request),
) -> AgentRunDetailResponse:
    try:
        return container.clawbot_service.get_agent_run(session_id=session_id, run_id=run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
