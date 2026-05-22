from __future__ import annotations

import asyncio
from typing import Any
from contextlib import contextmanager
from datetime import UTC, datetime
from html import escape
import json
import os
from pathlib import Path
import shutil

import core.clawbot.dependencies as clawbot_dependencies
import httpx
from core.channels.wechat.service import WechatGatewayService
from core.channels.wechat.types import WechatInboundEvent
from core.clawbot.dependencies import get_clawbot_container
from core.clawbot.schemas import TurnResponse
from core.evals.judge import evaluate_step
from core.evals.models import EvalAssertionFailure, EvalCase, EvalCaseResult, EvalObservedState, EvalSetup, EvalStepResult
from core.storage.repositories import (
    ChannelEventRepository,
    ChannelSessionMapRepository,
    ItemRepository,
    PendingStateRepository,
    SqlAgentRunRecordRepository,
)


class EvalRuntime:
    def __init__(self, *, project_root: Path) -> None:
        self.project_root = project_root

    def run_case(self, case: EvalCase) -> EvalCaseResult:
        started = datetime.now(UTC)
        step_results: list[EvalStepResult] = []
        error_message: str | None = None
        failure_category: str | None = None
        mock_web_client: httpx.Client | None = None
        try:
            sandbox_root = self.project_root / ".cora" / "eval-workspaces" / case.id
            if sandbox_root.exists():
                shutil.rmtree(sandbox_root)
            sandbox_root.mkdir(parents=True, exist_ok=True)

            workspace_root = sandbox_root / "workspace"
            workspace_root.mkdir(parents=True, exist_ok=True)
            self._write_workspace_files(workspace_root=workspace_root, workspace_files=case.setup.workspace_files)

            user_memory_path = sandbox_root / "user-memory" / "USER.md"
            if case.setup.user_memory_markdown:
                user_memory_path.parent.mkdir(parents=True, exist_ok=True)
                user_memory_path.write_text(case.setup.user_memory_markdown, encoding="utf-8")

            with isolated_settings_env(
                project_root=self.project_root,
                sandbox_root=sandbox_root,
                user_memory_path=user_memory_path,
                workspace_root=workspace_root,
                model_mode=case.setup.model_mode,
            ):
                clawbot_dependencies._container = None
                container = get_clawbot_container()
                container.initialize()
                self._configure_harness_tool_delay(container=container, setup=case.setup)
                self._configure_harness_prepare_failure(container=container, setup=case.setup)
                self._configure_planner_stub(container=container, setup=case.setup)
                mock_web_client = self._configure_mock_web_tools(container=container, setup=case.setup)
                session = container.clawbot_service.create_session()
                wechat_gateway = self._build_wechat_gateway(container=container)
                for index, step in enumerate(case.steps, start=1):
                    observed_session_id = session.id
                    try:
                        if step.input.channel == "wechat":
                            with self._wechat_step_budget(container=container, step_budget=step.input.run_budget):
                                wechat_result = asyncio.run(
                                    wechat_gateway.handle_inbound_event(
                                        event=WechatInboundEvent(
                                            event_id=step.input.external_event_id or f"{case.id}-{index}",
                                            user_id=step.input.external_user_id or f"eval-user-{case.id}",
                                            text=step.input.text,
                                        )
                                    )
                                )
                            observed_session_id = wechat_result.session_id
                            response = _turn_response_from_wechat_result(wechat_result)
                        elif step.input.hitl_action == "approve":
                            hitl_id = step.input.hitl_id or _latest_pending_hitl_id(
                                database=container.database,
                                session_id=session.id,
                            )
                            source_metadata = _eval_step_source_metadata(step.input)
                            response = asyncio.run(
                                container.clawbot_service.approve_hitl_and_resume(
                                    session_id=session.id,
                                    hitl_id=hitl_id,
                                    text=step.input.text or None,
                                    run_budget=step.input.run_budget,
                                    source_metadata=source_metadata or None,
                                )
                            )
                        elif str(step.input.agent_role or "").strip() == "execute":
                            source_metadata = _eval_step_source_metadata(step.input)
                            response = asyncio.run(
                                container.clawbot_service.execute_plan_turn(
                                    session_id=session.id,
                                    text=step.input.text,
                                    source_metadata=source_metadata or None,
                                )
                            )
                        elif str(step.input.agent_role or "").strip() == "spawn":
                            source_metadata = _eval_step_source_metadata(step.input)
                            response = asyncio.run(
                                container.clawbot_service.spawn_worker_turn(
                                    session_id=session.id,
                                    text=step.input.text,
                                    run_budget=step.input.run_budget,
                                    source_metadata=source_metadata or None,
                                )
                            )
                        elif str(step.input.agent_role or "").strip() == "planner":
                            source_metadata = _eval_step_source_metadata(step.input)
                            response = asyncio.run(
                                container.clawbot_service.plan_turn(
                                    session_id=session.id,
                                    text=step.input.text,
                                    run_budget=step.input.run_budget,
                                    source_metadata=source_metadata or None,
                                )
                            )
                        else:
                            source_metadata = _eval_step_source_metadata(step.input)
                            response = asyncio.run(
                                container.clawbot_service.reply(
                                    session_id=session.id,
                                    text=step.input.text,
                                    run_budget=step.input.run_budget,
                                    source_metadata=source_metadata or None,
                                )
                            )
                    except Exception as exc:
                        if not step.expect.error_contains_all:
                            raise
                        if step.input.channel == "wechat":
                            observed_session_id = _session_id_for_wechat_user(
                                database=container.database,
                                external_user_id=step.input.external_user_id or f"eval-user-{case.id}",
                            ) or observed_session_id
                        response = _failed_turn_response(exc)
                    observed_state = observe_state(
                        database=container.database,
                        session_id=observed_session_id,
                        user_memory_path=user_memory_path,
                        workspace_root=workspace_root,
                    )
                    step_results.append(
                        evaluate_step(
                            case=case,
                            step=step,
                            index=index,
                            response=response,
                            observed_state=observed_state,
                        )
                    )
        except Exception as exc:
            failure_category = "infrastructure_failure"
            error_message = f"{exc.__class__.__name__}: {exc}"
            next_step_index = len(step_results) + 1
            label = case.steps[next_step_index - 1].label if next_step_index <= len(case.steps) else case.id
            step_results.append(
                EvalStepResult(
                    index=next_step_index,
                    label=label or f"{case.id}#{next_step_index}",
                    ok=False,
                    failures=[EvalAssertionFailure(message=error_message)],
                    response={},
                    tool_names=[],
                    failure_category=failure_category,
                    observed_state=None,
                )
            )
        finally:
            if mock_web_client is not None:
                mock_web_client.close()
            clawbot_dependencies._container = None
        duration_seconds = max(0.0, (datetime.now(UTC) - started).total_seconds())
        return EvalCaseResult(
            case_id=case.id,
            description=case.description,
            ok=all(step.ok for step in step_results),
            tags=list(case.tags),
            step_results=step_results,
            duration_seconds=duration_seconds,
            source_path=str(case.source_path),
            failure_category=failure_category,
            error_message=error_message,
        )

    @staticmethod
    def _write_workspace_files(*, workspace_root: Path, workspace_files: dict[str, str]) -> None:
        for relative_path, content in workspace_files.items():
            target = _resolve_within_root(root=workspace_root, relative_path=relative_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    @staticmethod
    def _configure_mock_web_tools(*, container, setup: EvalSetup) -> httpx.Client | None:
        if not setup.web_search_results and not setup.web_pages:
            return None
        mock_web_client = _build_mock_web_client(setup=setup)
        web_store = container.tool_executor.web_tools.store
        web_store.http_client = mock_web_client
        if setup.web_search_results or any(page.provider == "tavily_extract" for page in setup.web_pages.values()):
            web_store.tavily_api_key = "eval-tavily-key"
        return mock_web_client

    @staticmethod
    def _configure_harness_tool_delay(*, container, setup: EvalSetup) -> None:
        delay_seconds = setup.harness_tool_delay_seconds
        if delay_seconds is None or delay_seconds <= 0:
            return
        original_execute_tool_call = container.tool_executor.execute_tool_call

        async def delayed_execute_tool_call(**kwargs):
            await asyncio.sleep(float(delay_seconds))
            return await original_execute_tool_call(**kwargs)

        container.tool_executor.execute_tool_call = delayed_execute_tool_call

    @staticmethod
    def _configure_planner_stub(*, container, setup: EvalSetup) -> None:
        if str(setup.model_mode or "").strip().lower() == "live":
            os.environ.pop("CORA_EVAL_PLANNER_STUB", None)
            return
        mode = str(setup.planner_stub_mode or "").strip().lower()
        if not mode:
            return
        os.environ["CORA_EVAL_PLANNER_STUB"] = mode

    @staticmethod
    def _configure_harness_prepare_failure(*, container, setup: EvalSetup) -> None:
        message = setup.harness_prepare_failure_message
        if not message:
            return

        def failing_history_loader(**kwargs):
            raise RuntimeError(message)

        container.clawbot_service._agent_turn_runner.history_loader = failing_history_loader

    @staticmethod
    def _build_wechat_gateway(*, container) -> WechatGatewayService:
        session_map_repository = ChannelSessionMapRepository(container.database)
        gateway = WechatGatewayService(
            clawbot_service=container.clawbot_service,
            event_repository=ChannelEventRepository(container.database),
            session_map_repository=session_map_repository,
            session_idle_minutes=container.settings.wechat_session_idle_minutes,
            session_daily_reset_hour=container.settings.wechat_session_daily_reset_hour,
            session_timezone=container.settings.wechat_session_timezone,
            enable_manual_reset=container.settings.wechat_session_enable_manual_reset,
        )
        container.configure_gateway(gateway, session_map_repository=session_map_repository)
        return gateway

    @staticmethod
    @contextmanager
    def _wechat_step_budget(*, container, step_budget):
        if not _run_budget_has_values(step_budget):
            yield
            return
        original_budget_for_turn = container.clawbot_service._run_budget_for_turn

        def budget_for_turn(**kwargs):
            budget = original_budget_for_turn(**kwargs)
            return step_budget if _run_budget_has_values(step_budget) else budget

        container.clawbot_service._run_budget_for_turn = budget_for_turn
        try:
            yield
        finally:
            container.clawbot_service._run_budget_for_turn = original_budget_for_turn


def live_evals_enabled() -> bool:
    flag = str(os.environ.get("CORA_RUN_LIVE_EVALS") or "").strip().lower()
    if flag not in {"1", "true", "yes", "on"}:
        return False
    return bool(str(os.environ.get("CORA_OPENAI_API_KEY") or "").strip())


@contextmanager
def isolated_settings_env(
    *,
    project_root: Path,
    sandbox_root: Path,
    user_memory_path: Path,
    workspace_root: Path,
    model_mode: str | None = None,
):
    original_cwd = Path.cwd()
    previous_values = {key: os.environ.get(key) for key in _OVERRIDDEN_ENV}
    sandbox_cora = sandbox_root / ".cora"
    sandbox_cora.mkdir(parents=True, exist_ok=True)
    user_memory_path.parent.mkdir(parents=True, exist_ok=True)
    workspace_root.mkdir(parents=True, exist_ok=True)
    if not user_memory_path.exists():
        user_memory_path.write_text("# User Memory\n", encoding="utf-8")
    os.environ["CORA_CLAWBOT_DATABASE_PATH"] = str(sandbox_cora / "clawbot.db")
    os.environ["CORA_FILES_STORAGE_DIR"] = str(sandbox_cora / "files")
    os.environ["CORA_ARCHIVE_ROOT_DIR"] = str(sandbox_cora / "archive")
    os.environ["CORA_USER_MEMORY_PATH"] = str(user_memory_path)
    os.environ["CORA_FILE_TOOL_ROOT"] = str(workspace_root)
    if str(model_mode or "").strip().lower() == "live":
        provider = str(os.environ.get("CORA_MODEL_PROVIDER") or "openai").strip().lower()
        if provider in {"dev", "development", ""}:
            provider = "openai"
        os.environ["CORA_MODEL_PROVIDER"] = provider
        os.environ.pop("CORA_EVAL_PLANNER_STUB", None)
    else:
        os.environ["CORA_MODEL_PROVIDER"] = "dev"
    os.chdir(project_root)
    try:
        yield
    finally:
        os.chdir(original_cwd)
        for key, value in previous_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


_OVERRIDDEN_ENV = {
    "CORA_CLAWBOT_DATABASE_PATH",
    "CORA_FILES_STORAGE_DIR",
    "CORA_ARCHIVE_ROOT_DIR",
    "CORA_USER_MEMORY_PATH",
    "CORA_FILE_TOOL_ROOT",
    "CORA_MODEL_PROVIDER",
    "CORA_EVAL_PLANNER_STUB",
}


def _failed_turn_response(exc: Exception):
    return TurnResponse(
        reply=f"{exc.__class__.__name__}: {exc}",
        status="failed",
        disposition="error",
        action="error",
        item_id=None,
        needs_clarification=False,
        artifacts=[],
        trace=[],
        decision_source="exception",
    )


def _turn_response_from_wechat_result(result) -> TurnResponse:
    action = str(getattr(result, "action", "") or "")
    disposition = getattr(result, "disposition", None) or "respond"
    status = "failed" if action.endswith("_failed") or disposition == "error" else "completed"
    return TurnResponse(
        reply=result.reply,
        status=status,
        disposition=disposition,
        action=result.action,
        item_id=None,
        needs_clarification=bool(getattr(result, "needs_clarification", False)),
        artifacts=[],
        trace=[],
        decision_source="wechat_gateway",
    )


def _session_id_for_wechat_user(*, database, external_user_id: str) -> str | None:
    binding = ChannelSessionMapRepository(database).get_binding(channel="wechat", external_user_id=external_user_id)
    return binding.session_id if binding is not None else None


def _eval_step_source_metadata(step_input) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if step_input.platform:
        metadata["platform"] = step_input.platform
    if step_input.spawn_depth is not None:
        metadata["spawn_depth"] = int(step_input.spawn_depth)
    return metadata


def _run_budget_has_values(budget) -> bool:
    return bool(
        getattr(budget, "policy_profile", None)
        or getattr(budget, "max_steps", None) is not None
        or getattr(budget, "timeout_seconds", None) is not None
        or getattr(budget, "max_tool_calls", None) is not None
        or getattr(budget, "max_spawn_depth", None) is not None
        or getattr(budget, "max_child_runs", None) is not None
        or list(getattr(budget, "allowed_tool_names", []) or [])
        or list(getattr(budget, "denied_tool_names", []) or [])
        or list(getattr(budget, "approved_tool_names", []) or [])
    )


def _latest_pending_hitl_id(*, database, session_id: str) -> str:
    agent_run_repository = SqlAgentRunRecordRepository(database)
    agent_runs = agent_run_repository.list_by_session(session_id=session_id)
    if not agent_runs:
        raise ValueError(f"No agent runs found for session {session_id}")
    for event in agent_runs[0].trace_events:
        if event.event_type != "tool.requested":
            continue
        hitl_id = str(event.metadata.get("hitl_id") or "").strip()
        if hitl_id:
            return hitl_id
    raise ValueError(f"No pending HITL id found in latest agent run for session {session_id}")


def observe_state(*, database, session_id: str, user_memory_path: Path, workspace_root: Path) -> EvalObservedState:
    item_repository = ItemRepository(database)
    pending_repository = PendingStateRepository(database)
    agent_run_repository = SqlAgentRunRecordRepository(database)
    active_items = item_repository.list_by_session(session_id=session_id, include_deleted=False)
    all_items = item_repository.list_by_session(session_id=session_id, include_deleted=True)
    agent_runs = agent_run_repository.list_by_session(session_id=session_id)
    latest_agent_run = agent_runs[0] if agent_runs else None
    deleted_item_count = sum(1 for item in all_items if bool(getattr(item, "is_deleted", 0)))
    pending = pending_repository.get_latest_pending(session_id=session_id)
    pending_kind = None
    if pending is not None:
        payload = dict(pending.pending_payload_json or {})
        pending_kind = str(payload.get("type") or "").strip() or None
    user_memory_text = user_memory_path.read_text(encoding="utf-8") if user_memory_path.exists() else ""
    return EvalObservedState(
        item_count=len(active_items),
        deleted_item_count=deleted_item_count,
        pending_exists=pending is not None,
        pending_kind=pending_kind,
        agent_run_count=len(agent_runs),
        latest_agent_run_status=latest_agent_run.status if latest_agent_run is not None else None,
        latest_agent_run_outcome=latest_agent_run.outcome if latest_agent_run is not None else None,
        latest_agent_run_failure_category=latest_agent_run.failure_category if latest_agent_run is not None else None,
        latest_agent_run_cleanup_status=latest_agent_run.cleanup_status if latest_agent_run is not None else None,
        latest_agent_run_input_metadata=dict(latest_agent_run.input_metadata or {}) if latest_agent_run is not None else {},
        latest_agent_run_error=latest_agent_run.error if latest_agent_run is not None else None,
        latest_agent_run_trace_events=[
            event.event_type for event in list(latest_agent_run.trace_events or [])
        ]
        if latest_agent_run is not None
        else [],
        user_memory_text=user_memory_text,
        workspace_root=str(workspace_root),
    )


def _resolve_within_root(*, root: Path, relative_path: str) -> Path:
    cleaned = relative_path.strip()
    if not cleaned:
        raise ValueError("workspace file path cannot be empty")
    candidate = (root / cleaned).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"workspace file path escapes eval workspace: {relative_path}") from exc
    return candidate


def _build_mock_web_client(*, setup: EvalSetup) -> httpx.Client:
    search_results = {
        _normalize_space(query): hits
        for query, hits in setup.web_search_results.items()
    }
    web_pages = {
        _normalize_mock_url(url): page
        for url, page in setup.web_pages.items()
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.tavily.com" and request.url.path == "/search":
            payload = json.loads(request.content.decode("utf-8") or "{}")
            query = _normalize_space(str(payload.get("query") or ""))
            hits = search_results.get(query)
            if hits is None:
                return httpx.Response(
                    404,
                    json={"error": f"no mock web search fixture for query: {query}"},
                    request=request,
                )
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"title": hit.title, "url": hit.url, "content": hit.snippet}
                        for hit in hits
                    ]
                },
                request=request,
            )
        if request.url.host == "api.tavily.com" and request.url.path == "/extract":
            payload = json.loads(request.content.decode("utf-8") or "{}")
            urls = payload.get("urls") or []
            requested_url = _normalize_mock_url(str(urls[0] or "")) if urls else ""
            page = web_pages.get(requested_url)
            if page is None or page.provider != "tavily_extract":
                return httpx.Response(
                    404,
                    json={
                        "failed_results": [
                            {"url": requested_url, "error": "no mock Tavily extract fixture"}
                        ]
                    },
                    request=request,
                )
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": page.final_url or requested_url,
                            "raw_content": page.content,
                        }
                    ]
                },
                request=request,
            )
        requested_url = _normalize_mock_url(str(request.url))
        page = web_pages.get(requested_url)
        if page is None:
            return httpx.Response(404, text="no mock web fixture", request=request)
        if page.provider == "tavily_extract":
            raise httpx.ConnectError("mock blocked direct fetch", request=request)
        content_type = page.content_type
        body = _mock_response_body(page=page)
        return httpx.Response(
            200,
            text=body,
            headers={"content-type": content_type},
            request=request,
        )

    return httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )


def _mock_response_body(*, page) -> str:
    content_type = page.content_type.lower()
    if "text/html" not in content_type or "<html" in page.content.lower():
        return page.content
    title = f"<title>{escape(page.title)}</title>" if page.title else ""
    content = page.content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"<html><head>{title}</head><body><main><p>{content}</p></main></body></html>"


def _normalize_space(value: str) -> str:
    return " ".join(str(value or "").split())


def _normalize_mock_url(url: str) -> str:
    return str(url or "").strip()
