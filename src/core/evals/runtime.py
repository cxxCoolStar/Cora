from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime
import os
from pathlib import Path
import shutil

import core.clawbot.dependencies as clawbot_dependencies
from core.clawbot.dependencies import get_clawbot_container
from core.evals.judge import evaluate_step
from core.evals.models import EvalAssertionFailure, EvalCase, EvalCaseResult, EvalObservedState, EvalStepResult
from core.storage.repositories import ItemRepository, PendingStateRepository


class EvalRuntime:
    def __init__(self, *, project_root: Path) -> None:
        self.project_root = project_root

    def run_case(self, case: EvalCase) -> EvalCaseResult:
        started = datetime.now(UTC)
        step_results: list[EvalStepResult] = []
        error_message: str | None = None
        failure_category: str | None = None
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
            ):
                clawbot_dependencies._container = None
                container = get_clawbot_container()
                container.initialize()
                session = container.clawbot_service.create_session()
                for index, step in enumerate(case.steps, start=1):
                    response = asyncio.run(container.clawbot_service.reply(session_id=session.id, text=step.input.text))
                    observed_state = observe_state(
                        database=container.database,
                        session_id=session.id,
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


@contextmanager
def isolated_settings_env(*, project_root: Path, sandbox_root: Path, user_memory_path: Path, workspace_root: Path):
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
}


def observe_state(*, database, session_id: str, user_memory_path: Path, workspace_root: Path) -> EvalObservedState:
    item_repository = ItemRepository(database)
    pending_repository = PendingStateRepository(database)
    active_items = item_repository.list_by_session(session_id=session_id, include_deleted=False)
    all_items = item_repository.list_by_session(session_id=session_id, include_deleted=True)
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
