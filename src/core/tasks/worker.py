from __future__ import annotations

import asyncio
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
from math import ceil
from pathlib import Path
from typing import Any, Callable

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows path
    fcntl = None
    try:
        import msvcrt
    except ImportError:  # pragma: no cover - defensive
        msvcrt = None
else:  # pragma: no cover - Unix path
    msvcrt = None

from core.clawbot.planner import ToolPlan
from core.clawbot.service import ClawBotService
from core.storage.repositories import ScheduledTaskRepository
from core.tasks.execution import ScheduledTaskExecution
from core.tasks.schedule import DEFAULT_SILENT_REPLY
from core.tasks.script_runner import ScheduledTaskScriptRunner

logger = logging.getLogger(__name__)


class _TickLock(AbstractContextManager["_TickLock"]):
    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd = None

    def __enter__(self) -> "_TickLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = open(self.path, "w", encoding="utf-8")
        try:
            if fcntl is not None:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            elif msvcrt is not None:
                msvcrt.locking(self._fd.fileno(), msvcrt.LK_NBLCK, 1)
        except (OSError, IOError):
            self._fd.close()
            self._fd = None
            raise RuntimeError("tick_locked")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._fd is None:
            return None
        try:
            if fcntl is not None:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            elif msvcrt is not None:
                try:
                    msvcrt.locking(self._fd.fileno(), msvcrt.LK_UNLCK, 1)
                except (OSError, IOError):
                    pass
        finally:
            self._fd.close()
            self._fd = None
        return None


@dataclass(slots=True)
class ScheduledTaskWorker:
    repository: ScheduledTaskRepository
    clawbot_service: ClawBotService
    send_text: Callable[[str, str], Any] | None = None
    lock_path: Path | None = None
    poll_interval_seconds: int = 30
    lease_seconds: int = 600
    silent_reply_token: str = DEFAULT_SILENT_REPLY

    async def run_forever(self) -> None:
        logger.info("scheduled task worker started")
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception("scheduled task worker tick failed; retry in %s seconds", self.poll_interval_seconds)
            await asyncio.sleep(self.next_sleep_seconds())

    async def tick(self, *, limit: int = 20) -> int:
        lock_path = self.lock_path or Path(".cora/scheduled-tasks/.tick.lock")
        try:
            with _TickLock(lock_path):
                return await self._tick_locked(limit=limit)
        except RuntimeError as exc:
            if str(exc) == "tick_locked":
                logger.debug("scheduled task tick skipped because another worker holds the lock")
                return 0
            raise

    async def _tick_locked(self, *, limit: int) -> int:
        now = datetime.now(UTC)
        due_tasks = self.repository.list_due(now=now, limit=limit)
        if not due_tasks:
            logger.debug("scheduled task tick found no due tasks")
            return 0
        logger.info("scheduled task tick picked up %d due task(s)", len(due_tasks))
        completed = 0
        for task in due_tasks:
            claimed = self.repository.claim_for_run(
                task_id=task.id,
                now=now,
                lease_seconds=max(30, int(self.lease_seconds)),
            )
            if claimed is None:
                continue
            await self._run_one(task=claimed)
            completed += 1
        return completed

    def next_sleep_seconds(self, *, now: datetime | None = None) -> int:
        default_sleep = max(1, int(self.poll_interval_seconds))
        current_time = (now or datetime.now(UTC)).astimezone(UTC)
        try:
            next_run_at = self.repository.peek_next_run_at()
        except Exception:
            logger.exception("scheduled task worker failed to inspect the next run time")
            return default_sleep
        if next_run_at is None:
            return default_sleep
        remaining_seconds = ceil((next_run_at - current_time).total_seconds())
        if remaining_seconds <= 0:
            return 1
        return max(1, min(default_sleep, remaining_seconds))

    async def _run_one(self, *, task: Any) -> None:
        logger.info("scheduled task run start id=%s name=%s", task.id, task.name)
        success = False
        error: str | None = None
        delivery_error: str | None = None
        reply_preview = ""
        execution_session_id: str | None = None
        execution_mode = "agent_prompt"
        try:
            execution = ScheduledTaskExecution.from_metadata(
                prompt_text=task.prompt_text,
                metadata=dict(getattr(task, "metadata_json", {}) or {}),
            )
            execution_mode = execution.mode
            execution_session_id = self._create_execution_session_id(task=task, execution=execution)
            status, reply_text = await self._execute_task(
                task=task,
                execution=execution,
                execution_session_id=execution_session_id,
            )
            reply_text = (reply_text or "").strip()
            if reply_text == self.silent_reply_token:
                reply_text = ""
            reply_preview = reply_text[:500]
            if status == "failed":
                error = reply_text or f"scheduled task {execution.mode} execution failed"
            elif reply_text and self.send_text and str(task.owner_external_user_id or "").strip():
                try:
                    maybe_result = self.send_text(str(task.owner_external_user_id), reply_text)
                    if hasattr(maybe_result, "__await__"):
                        await maybe_result
                except Exception as exc:  # pragma: no cover - defensive
                    delivery_error = str(exc)
                    logger.exception("scheduled task delivery failed id=%s", task.id)
            success = error is None
        except Exception as exc:  # pragma: no cover - defensive
            error = str(exc)
            logger.exception("scheduled task run failed id=%s", task.id)
        finally:
            finished_at = datetime.now(UTC)
            self.repository.finish_run(
                task_id=task.id,
                finished_at=finished_at,
                success=success,
                error=error,
                delivery_error=delivery_error,
                reply_preview=reply_preview,
                run_metadata={
                    "origin_session_id": str(task.session_id or "").strip() or None,
                    "execution_session_id": execution_session_id,
                    "execution_mode": execution_mode,
                    "finished_at": finished_at.isoformat(),
                    "success": success,
                    "delivery_error": delivery_error,
                    "error": error,
                },
            )
            logger.info(
                "scheduled task run done id=%s execution_session_id=%s success=%s delivery_error=%s error=%s",
                task.id,
                execution_session_id or "",
                success,
                bool(delivery_error),
                error or "",
            )

    def _create_execution_session_id(self, *, task: Any, execution: ScheduledTaskExecution) -> str:
        session = self.clawbot_service.create_job_execution_session(
            origin_session_id=str(task.session_id or "").strip(),
            scheduled_task_id=str(task.id or "").strip(),
            task_name=str(task.name or "").strip() or "scheduled task",
            execution_mode=execution.mode,
            owner_external_user_id=str(task.owner_external_user_id or "").strip() or None,
        )
        return str(session.id)

    async def _execute_task(
        self,
        *,
        task: Any,
        execution: ScheduledTaskExecution,
        execution_session_id: str,
    ) -> tuple[str, str]:
        if execution.mode == "skill":
            return await self._execute_skill_task(
                task=task,
                execution=execution,
                execution_session_id=execution_session_id,
            )
        if execution.mode == "script":
            return await self._execute_script_task(
                task=task,
                execution=execution,
                execution_session_id=execution_session_id,
            )
        return await self._execute_agent_prompt_task(
            task=task,
            execution=execution,
            execution_session_id=execution_session_id,
        )

    async def _execute_agent_prompt_task(
        self,
        *,
        task: Any,
        execution: ScheduledTaskExecution,
        execution_session_id: str,
    ) -> tuple[str, str]:
        prompt_text = self._compose_prompt_text(task=task, execution=execution)
        outcome = await self.clawbot_service.reply_outcome(
            session_id=execution_session_id,
            text=prompt_text,
            source_metadata=self._source_metadata(
                task=task,
                execution=execution,
                execution_session_id=execution_session_id,
            ),
        )
        return outcome.status, outcome.reply

    async def _execute_skill_task(
        self,
        *,
        task: Any,
        execution: ScheduledTaskExecution,
        execution_session_id: str,
    ) -> tuple[str, str]:
        outcome = await self.clawbot_service.execute_tool_plan_outcome(
            session_id=execution_session_id,
            text=str(task.prompt_text or "").strip() or f"[scheduled skill task: {task.name}]",
            source_metadata=self._source_metadata(
                task=task,
                execution=execution,
                execution_session_id=execution_session_id,
            ),
            plan=ToolPlan(
                tool="skill_run",
                arguments={
                    "name": execution.skill_name,
                    "script_path": execution.script_path,
                    "input": dict(execution.input_payload),
                },
                reason=f"Execute scheduled task skill for {task.name}.",
                source="scheduled_task",
            ),
        )
        return outcome.status, outcome.reply

    async def _execute_script_task(
        self,
        *,
        task: Any,
        execution: ScheduledTaskExecution,
        execution_session_id: str,
    ) -> tuple[str, str]:
        runner = ScheduledTaskScriptRunner(
            script_root=Path(getattr(self.clawbot_service, "file_tool_root", Path("."))),
        )
        result = await asyncio.to_thread(
            runner.run,
            script_path=str(execution.script_path or ""),
            input_payload={
                "session_id": execution_session_id,
                "origin_session_id": str(task.session_id or "").strip() or None,
                "execution_session_id": execution_session_id,
                "task": {
                    "id": task.id,
                    "name": task.name,
                    "prompt": task.prompt_text,
                    "owner_external_user_id": task.owner_external_user_id,
                    "origin_session_id": str(task.session_id or "").strip() or None,
                    "execution_session_id": execution_session_id,
                },
                "arguments": dict(execution.input_payload),
            },
        )
        return result.status, result.message

    def _compose_prompt_text(self, *, task: Any, execution: ScheduledTaskExecution) -> str:
        prompt_text = str(task.prompt_text or "").strip()
        if not execution.attached_skills:
            return prompt_text
        skill_loader = getattr(self.clawbot_service, "skill_loader", None)
        if skill_loader is None:
            return prompt_text
        sections: list[str] = []
        missing: list[str] = []
        for skill_name in execution.attached_skills:
            viewed = skill_loader.view_skill(skill_name)
            if viewed is None:
                missing.append(skill_name)
                continue
            sections.append(f"[Attached skill: {skill_name}]")
            sections.append(viewed.content.strip())
        if prompt_text:
            sections.extend(["", "Task instruction:", prompt_text])
        if missing:
            sections.insert(0, f"[Missing attached skills: {', '.join(missing)}]")
        combined = "\n\n".join(part for part in sections if part)
        return combined or prompt_text

    @staticmethod
    def _source_metadata(
        *,
        task: Any,
        execution: ScheduledTaskExecution,
        execution_session_id: str,
    ) -> dict[str, Any]:
        return {
            "channel": "scheduled_task",
            "event_type": "scheduled_task",
            "external_user_id": str(task.owner_external_user_id or "").strip() or None,
            "scheduled_task_id": task.id,
            "scheduled_task_name": task.name,
            "scheduled_task_execution_mode": execution.mode,
            "scheduled_task_origin_session_id": str(task.session_id or "").strip() or None,
            "scheduled_task_execution_session_id": execution_session_id,
            "session_kind": "job_execution",
        }
