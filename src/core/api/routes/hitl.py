from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from core.clawbot.dependencies import get_container_from_request
from core.clawbot.schemas import HitlActionResponse, HitlRequestResponse

router = APIRouter()


def _hitl_response(request) -> HitlRequestResponse:
    payload = request.to_dict()
    return HitlRequestResponse(
        hitl_id=payload["hitl_id"],
        run_id=payload["run_id"],
        session_id=payload["session_id"],
        tool_name=payload["tool_name"],
        status=payload["status"],
        reason=payload["reason"],
        policy_profile=payload.get("policy_profile"),
        tool_risk=payload["tool_risk"],
        tool_arguments=payload.get("tool_arguments") or {},
        created_at=payload["created_at"],
        resolved_at=payload.get("resolved_at"),
        metadata=payload.get("metadata") or {},
    )


@router.get("/{session_id}/hitl/{hitl_id}", response_model=HitlRequestResponse)
def get_hitl_request(
    session_id: str,
    hitl_id: str,
    container=Depends(get_container_from_request),
) -> HitlRequestResponse:
    request = container.clawbot_service.hitl_store.get(hitl_id=hitl_id)
    if request is None or request.session_id != session_id:
        raise HTTPException(status_code=404, detail="HITL request not found")
    return _hitl_response(request)


@router.post(
    "/{session_id}/runs/{run_id}/hitl/{hitl_id}/approve",
    response_model=HitlActionResponse,
)
async def approve_hitl_and_resume(
    session_id: str,
    run_id: str,
    hitl_id: str,
    container=Depends(get_container_from_request),
) -> HitlActionResponse:
    request = container.clawbot_service.hitl_store.get(hitl_id=hitl_id)
    if request is None or request.session_id != session_id or request.run_id != run_id:
        raise HTTPException(status_code=404, detail="HITL request not found")
    try:
        turn = await container.clawbot_service.approve_hitl_and_resume(
            session_id=session_id,
            hitl_id=hitl_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    approved = container.clawbot_service.hitl_store.get(hitl_id=hitl_id)
    return HitlActionResponse(
        hitl=_hitl_response(approved),
        turn=turn,
    )


@router.post(
    "/{session_id}/runs/{run_id}/hitl/{hitl_id}/reject",
    response_model=HitlRequestResponse,
)
def reject_hitl(
    session_id: str,
    run_id: str,
    hitl_id: str,
    container=Depends(get_container_from_request),
) -> HitlRequestResponse:
    request = container.clawbot_service.hitl_store.get(hitl_id=hitl_id)
    if request is None or request.session_id != session_id or request.run_id != run_id:
        raise HTTPException(status_code=404, detail="HITL request not found")
    try:
        rejected = container.clawbot_service.reject_hitl(
            session_id=session_id,
            hitl_id=hitl_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _hitl_response(rejected)
