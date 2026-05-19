from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from core.agent.skill_loader import SkillLoader
from core.storage.repositories import ScheduledTaskRepository
from core.tasks.execution import ScheduledTaskExecution, normalize_task_metadata
from core.tasks.schedule import format_schedule, normalize_schedule_input
from core.tools.file_tools import FileToolStore
from core.tools.session_search_tools import SessionSearchToolStore
from core.tools.terminal_tools import TerminalToolStore
from core.tools.web_tools import WebToolStore
from core.user_memory import UserMemoryStore
import httpx

if TYPE_CHECKING:
    from core.tools import ToolInvocation


@dataclass(slots=True)
class DomainToolReply:
    reply: str
    action: str
    status: str = "completed"
    metadata: dict[str, Any] | None = None


@dataclass(slots=True)
class UserMemoryToolHandler:
    store: UserMemoryStore

    @classmethod
    def from_path(cls, path: Path) -> "UserMemoryToolHandler":
        return cls(store=UserMemoryStore(path))

    def execute(self, invocation: "ToolInvocation") -> DomainToolReply:
        action = str(invocation.plan.arguments.get("action") or "").strip()
        try:
            if action == "read":
                return DomainToolReply(reply=self.store.render(), action="memory")
            if action == "add":
                content = str(invocation.plan.arguments.get("content") or "").strip()
                return DomainToolReply(reply=self.store.add(content), action="memory")
            if action == "replace":
                old_text = str(invocation.plan.arguments.get("old_text") or "").strip()
                new_content = str(invocation.plan.arguments.get("new_content") or "").strip()
                return DomainToolReply(
                    reply=self.store.replace(old_text, new_content),
                    action="memory",
                )
            if action == "remove":
                old_text = str(invocation.plan.arguments.get("old_text") or "").strip()
                return DomainToolReply(reply=self.store.remove(old_text), action="memory")
        except ValueError as exc:
            return DomainToolReply(reply=str(exc), action="memory")
        return DomainToolReply(reply="我暂时还不能处理这个 user_memory 动作。", action="memory")


@dataclass(slots=True)
class FileToolHandler:
    store: FileToolStore

    @classmethod
    def from_root(cls, root: Path) -> "FileToolHandler":
        return cls(store=FileToolStore(root))

    def list_files(self, invocation: "ToolInvocation") -> DomainToolReply:
        try:
            reply = self.store.list_files(
                path=str(invocation.plan.arguments.get("path") or "."),
                recursive=bool(invocation.plan.arguments.get("recursive") or False),
                max_results=int(invocation.plan.arguments.get("max_results") or 50),
                include_hidden=bool(invocation.plan.arguments.get("include_hidden") or False),
            )
        except ValueError as exc:
            return DomainToolReply(reply=str(exc), action="inspect")
        return DomainToolReply(reply=reply, action="inspect")

    def search_files(self, invocation: "ToolInvocation") -> DomainToolReply:
        try:
            reply = self.store.search_files(
                query=str(invocation.plan.arguments.get("query") or ""),
                path=str(invocation.plan.arguments.get("path") or "."),
                file_pattern=str(invocation.plan.arguments.get("file_pattern") or "").strip() or None,
                case_sensitive=bool(invocation.plan.arguments.get("case_sensitive") or False),
                max_results=int(invocation.plan.arguments.get("max_results") or 20),
                include_hidden=bool(invocation.plan.arguments.get("include_hidden") or False),
            )
        except ValueError as exc:
            return DomainToolReply(reply=str(exc), action="inspect")
        return DomainToolReply(reply=reply, action="inspect")

    def read_file(self, invocation: "ToolInvocation") -> DomainToolReply:
        try:
            reply = self.store.read_file(
                path=str(invocation.plan.arguments.get("path") or ""),
                start_line=int(invocation.plan.arguments.get("start_line") or 1),
                end_line=(
                    int(invocation.plan.arguments["end_line"])
                    if invocation.plan.arguments.get("end_line") is not None
                    else None
                ),
            )
        except ValueError as exc:
            return DomainToolReply(reply=str(exc), action="inspect")
        return DomainToolReply(reply=reply, action="inspect")

    def write_file(self, invocation: "ToolInvocation") -> DomainToolReply:
        try:
            reply = self.store.write_file(
                path=str(invocation.plan.arguments.get("path") or ""),
                content=str(invocation.plan.arguments.get("content") or ""),
                append=bool(invocation.plan.arguments.get("append") or False),
            )
        except ValueError as exc:
            return DomainToolReply(reply=str(exc), action="edit")
        return DomainToolReply(reply=reply, action="edit")


@dataclass(slots=True)
class SkillToolHandler:
    loader: SkillLoader

    @classmethod
    def from_roots(cls, roots: list[Path] | None = None) -> "SkillToolHandler":
        return cls(loader=SkillLoader(skill_roots=roots))

    def list_skills(self, invocation: "ToolInvocation") -> DomainToolReply:
        category_filter = str(invocation.plan.arguments.get("category") or "").strip()
        skills = self.loader.list_skills()
        if category_filter:
            skills = [skill for skill in skills if (skill.category or "") == category_filter]
        if not skills:
            if category_filter:
                return DomainToolReply(
                    reply=f"没有找到分类为 {category_filter} 的可用 skills。",
                    action="skill",
                )
            return DomainToolReply(reply="当前没有可用 skills。", action="skill")

        lines = ["Available skills:"]
        for skill in skills:
            description = skill.description or "No description provided."
            if skill.category:
                lines.append(f"- {skill.name} [{skill.category}]: {description}")
            else:
                lines.append(f"- {skill.name}: {description}")
        lines.append("Use skill_view with the exact skill name to load the full instructions.")
        return DomainToolReply(reply="\n".join(lines), action="skill")

    def view_skill(self, invocation: "ToolInvocation") -> DomainToolReply:
        name = str(invocation.plan.arguments.get("name") or "").strip()
        file_path = str(invocation.plan.arguments.get("file_path") or "").strip() or None
        if not name:
            return DomainToolReply(reply="skill_view 需要提供 skill 名称。", action="skill")

        try:
            viewed = self.loader.view_skill(name=name, file_path=file_path)
        except ValueError as exc:
            return DomainToolReply(reply=str(exc), action="skill")
        if viewed is None:
            return DomainToolReply(reply=f"没有找到名为 {name} 的 skill。", action="skill")

        if viewed.file_path:
            reply = (
                f"Skill file: {viewed.skill.name} / {viewed.file_path}\n"
                f"Path: {viewed.absolute_path}\n\n"
                f"{viewed.content}"
            )
            return DomainToolReply(reply=reply, action="skill")

        lines = [
            f"Skill: {viewed.skill.name}",
            f"Path: {viewed.absolute_path}",
        ]
        if viewed.skill.description:
            lines.append(f"Description: {viewed.skill.description}")
        if viewed.skill.category:
            lines.append(f"Category: {viewed.skill.category}")
        lines.extend(["", viewed.content])
        if viewed.skill.linked_files:
            lines.extend(["", "Supporting files:"])
            for section in ("references", "templates", "assets", "scripts"):
                for linked_file in viewed.skill.linked_files.get(section, []):
                    lines.append(f"- {linked_file}")
            lines.append("Use skill_view again with file_path to load one of these files.")
        return DomainToolReply(reply="\n".join(lines), action="skill")


@dataclass(slots=True)
class ScheduledTaskToolHandler:
    repository: ScheduledTaskRepository
    default_timezone: str | None = None
    source_event_repository: Any | None = None

    def execute(
        self,
        invocation: "ToolInvocation",
        *,
        owner_external_user_id: str | None,
    ) -> DomainToolReply:
        arguments = dict(invocation.plan.arguments or {})
        action = str(arguments.get("action") or "").strip().lower()
        try:
            if action == "create":
                return self._create(invocation=invocation, owner_external_user_id=owner_external_user_id, arguments=arguments)
            if action == "list":
                return self._list(invocation=invocation, owner_external_user_id=owner_external_user_id)
            if action == "get":
                return self._get(invocation=invocation, owner_external_user_id=owner_external_user_id, arguments=arguments)
            if action == "update":
                return self._update(invocation=invocation, owner_external_user_id=owner_external_user_id, arguments=arguments)
            if action == "pause":
                return self._set_enabled(invocation=invocation, owner_external_user_id=owner_external_user_id, arguments=arguments, enabled=False)
            if action == "resume":
                return self._set_enabled(invocation=invocation, owner_external_user_id=owner_external_user_id, arguments=arguments, enabled=True)
            if action == "delete":
                return self._delete(invocation=invocation, owner_external_user_id=owner_external_user_id, arguments=arguments)
            if action == "run_now":
                return self._run_now(invocation=invocation, owner_external_user_id=owner_external_user_id, arguments=arguments)
        except ValueError as exc:
            return DomainToolReply(reply=str(exc), action="automation", status="failed")
        except KeyError as exc:
            return DomainToolReply(reply=str(exc), action="automation", status="failed")
        return DomainToolReply(reply="scheduled_tasks requires a supported action.", action="automation", status="failed")

    def _scope_records(
        self,
        *,
        invocation: "ToolInvocation",
        owner_external_user_id: str | None,
    ):
        return self.repository.list_for_scope(
            session_id=invocation.session_id,
            owner_external_user_id=owner_external_user_id,
        )

    def _resolve_task(
        self,
        *,
        invocation: "ToolInvocation",
        owner_external_user_id: str | None,
        arguments: dict[str, Any],
    ):
        task_ref = str(arguments.get("task_ref") or arguments.get("id") or "").strip()
        if not task_ref:
            raise ValueError("scheduled_tasks requires task_ref for this action.")
        record = self.repository.resolve_for_scope(
            task_ref=task_ref,
            session_id=invocation.session_id,
            owner_external_user_id=owner_external_user_id,
        )
        if record is None:
            raise KeyError(f"Scheduled task not found in this scope: {task_ref}")
        return record

    def _reference_now(self, *, invocation: "ToolInvocation") -> datetime:
        source_event_time = self._source_event_time(invocation=invocation)
        return source_event_time or datetime.now(UTC)

    def _source_event_time(self, *, invocation: "ToolInvocation") -> datetime | None:
        event_id = str(invocation.context.get("current_source_event_id") or "").strip()
        if event_id and self.source_event_repository is not None:
            try:
                record = self.source_event_repository.get_any(event_id=event_id)
            except KeyError:
                record = None
            if record is not None:
                parsed = self._extract_reference_time(
                    metadata=dict(getattr(record, "metadata_json", {}) or {}),
                    fallback=getattr(record, "created_at", None),
                )
                if parsed is not None:
                    return parsed
        recent_events = invocation.context.get("recent_events") or []
        if isinstance(recent_events, list):
            for event in recent_events:
                if not isinstance(event, dict):
                    continue
                if event_id and str(event.get("source_event_id") or "").strip() != event_id:
                    continue
                parsed = self._extract_reference_time(metadata=event, fallback=event.get("created_at"))
                if parsed is not None:
                    return parsed
        return None

    @classmethod
    def _extract_reference_time(
        cls,
        *,
        metadata: dict[str, Any],
        fallback: Any = None,
    ) -> datetime | None:
        parsed = cls._parse_datetime_value(metadata.get("source_created_at"))
        if parsed is not None:
            return parsed
        source_create_time_ms = metadata.get("source_create_time_ms")
        if source_create_time_ms is not None:
            try:
                return datetime.fromtimestamp(max(0, int(source_create_time_ms)) / 1000, tz=UTC)
            except (TypeError, ValueError, OSError):
                pass
        return cls._parse_datetime_value(fallback)

    @staticmethod
    def _parse_datetime_value(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return ScheduledTaskToolHandler._as_utc(value)
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        return ScheduledTaskToolHandler._as_utc(parsed)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _display_timezone(self) -> tuple[Any, str]:
        timezone_name = str(self.default_timezone or "").strip()
        if timezone_name:
            try:
                return ZoneInfo(timezone_name), timezone_name
            except Exception:
                pass
        local_tz = datetime.now().astimezone().tzinfo or UTC
        label = getattr(local_tz, "key", None) or datetime.now(UTC).astimezone(local_tz).tzname() or str(local_tz) or "local"
        return local_tz, str(label)

    def _format_datetime_for_reply(self, value: datetime | None) -> str:
        if value is None:
            return "未安排"
        utc_value = self._as_utc(value)
        tzinfo, label = self._display_timezone()
        local_value = utc_value.astimezone(tzinfo)
        return f"{local_value.strftime('%Y-%m-%d %H:%M:%S')} ({label})"

    def _format_schedule_for_reply(self, schedule: dict[str, Any]) -> str:
        kind = str(schedule.get("kind") or "").strip().lower()
        if kind == "once":
            return f"一次，于 {self._format_datetime_for_reply(self._parse_datetime_value(schedule.get('at')))}"
        return format_schedule(schedule)

    def _queue_status_line(self, *, next_run_at: datetime | None, now: datetime) -> str | None:
        if next_run_at is None:
            return None
        if self._as_utc(next_run_at) <= now:
            return "提醒时间已到，任务会在下一次 worker tick 立刻执行。"
        return None

    @staticmethod
    def _state_label(state: str) -> str:
        labels = {
            "scheduled": "已安排",
            "paused": "已暂停",
            "running": "执行中",
            "completed": "已完成",
            "error": "出错",
        }
        return labels.get(str(state or "").strip().lower(), state)

    @staticmethod
    def _execution_argument(arguments: dict[str, Any]) -> dict[str, Any] | None:
        execution = arguments.get("execution")
        if execution is None:
            return None
        if not isinstance(execution, dict):
            raise ValueError("scheduled_tasks execution must be an object.")
        return dict(execution)

    def _merged_metadata(
        self,
        *,
        prompt_text: str,
        arguments: dict[str, Any],
        existing_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata_payload = dict(existing_metadata or {})
        if "metadata" in arguments:
            raw_metadata = arguments.get("metadata")
            if raw_metadata is None:
                metadata_payload = {}
            elif not isinstance(raw_metadata, dict):
                raise ValueError("scheduled_tasks metadata must be an object.")
            else:
                metadata_payload = dict(raw_metadata)
        execution = self._execution_argument(arguments)
        if execution is not None:
            metadata_payload["execution"] = execution
        return normalize_task_metadata(
            prompt_text=prompt_text,
            metadata=metadata_payload,
        )

    @staticmethod
    def _default_task_name(*, prompt: str, metadata: dict[str, Any]) -> str:
        if prompt.strip():
            return prompt[:40].strip() or "scheduled task"
        execution = ScheduledTaskExecution.from_metadata(prompt_text="__scheduled__", metadata=metadata)
        if execution.mode == "skill" and execution.skill_name:
            return execution.skill_name
        if execution.script_path:
            return Path(execution.script_path).stem or "scheduled task"
        return "scheduled task"

    @staticmethod
    def _execution_summary(record: Any) -> str:
        metadata = dict(getattr(record, "metadata_json", {}) or {})
        return ScheduledTaskExecution.summarize(metadata)

    def _create(
        self,
        *,
        invocation: "ToolInvocation",
        owner_external_user_id: str | None,
        arguments: dict[str, Any],
    ) -> DomainToolReply:
        prompt = str(arguments.get("prompt") or "").strip()
        metadata = self._merged_metadata(
            prompt_text=prompt,
            arguments=arguments,
        )
        if not prompt:
            execution = ScheduledTaskExecution.from_metadata(prompt_text="__scheduled__", metadata=metadata)
            if execution.mode == "agent_prompt":
                raise ValueError("scheduled_tasks.create requires prompt.")
        reference_now = self._reference_now(invocation=invocation)
        schedule = normalize_schedule_input(
            self._normalize_schedule_argument(invocation=invocation, arguments=arguments),
            schedule_text=str(arguments.get("schedule_text") or "").strip() or None,
            default_timezone=self.default_timezone,
            now=reference_now,
        )
        name = str(arguments.get("name") or "").strip() or self._default_task_name(prompt=prompt, metadata=metadata)
        record = self.repository.create(
            session_id=invocation.session_id,
            owner_external_user_id=owner_external_user_id,
            name=name,
            prompt_text=prompt,
            schedule=schedule,
            enabled=bool(arguments.get("enabled", True)),
            run_immediately=bool(arguments.get("run_immediately") or False),
            metadata=metadata,
        )
        if record.next_run_at is not None:
            reply_lines = [
                f"好的，已设置提醒「{record.name}」。",
                f"将在 {self._format_datetime_for_reply(record.next_run_at)} 提醒你。",
                f"状态：{self._state_label(record.state)}。",
            ]
        else:
            reply_lines = [
                f"好的，已创建提醒「{record.name}」。",
                f"状态：{self._state_label(record.state)}。",
            ]
        queue_status = self._queue_status_line(next_run_at=record.next_run_at, now=datetime.now(UTC))
        if queue_status:
            reply_lines.append(queue_status)
        reply = "\n".join(reply_lines)
        return DomainToolReply(
            reply=reply,
            action="automation",
            metadata={"scheduled_task_action": "create"},
        )

    def _list(
        self,
        *,
        invocation: "ToolInvocation",
        owner_external_user_id: str | None,
    ) -> DomainToolReply:
        records = self._scope_records(invocation=invocation, owner_external_user_id=owner_external_user_id)
        if not records:
            return DomainToolReply(
                reply="当前没有已设置的提醒。",
                action="automation",
                metadata={"scheduled_task_action": "list"},
            )
        lines = ["当前已设置的提醒："]
        for record in records:
            lines.append(
                f"- {record.name} [{record.id}] 状态={self._state_label(record.state)} "
                f"下次={self._format_datetime_for_reply(record.next_run_at)} "
                f"计划={self._format_schedule_for_reply(dict(record.schedule_json or {}))}"
            )
        return DomainToolReply(
            reply="\n".join(lines),
            action="automation",
            metadata={"scheduled_task_action": "list"},
        )

    def _get(
        self,
        *,
        invocation: "ToolInvocation",
        owner_external_user_id: str | None,
        arguments: dict[str, Any],
    ) -> DomainToolReply:
        record = self._resolve_task(
            invocation=invocation,
            owner_external_user_id=owner_external_user_id,
            arguments=arguments,
        )
        lines = [
            f"提醒名称：{record.name}",
            f"ID：{record.id}",
            f"状态：{self._state_label(record.state)}",
            f"启用：{bool(record.enabled)}",
            f"计划：{self._format_schedule_for_reply(dict(record.schedule_json or {}))}",
            f"下次执行：{self._format_datetime_for_reply(record.next_run_at)}",
            f"上次执行：{self._format_datetime_for_reply(record.last_run_at)}" if record.last_run_at else "上次执行：从未执行",
            f"上次结果：{record.last_status or '无'}",
        ]
        if record.last_error:
            lines.append(f"上次错误：{record.last_error}")
        if record.last_delivery_error:
            lines.append(f"上次投递错误：{record.last_delivery_error}")
        if record.last_reply_preview:
            lines.append(f"上次回复：{record.last_reply_preview}")
        lines.extend(["", "内容：", record.prompt_text])
        return DomainToolReply(
            reply="\n".join(lines),
            action="automation",
            metadata={"scheduled_task_action": "get"},
        )

    def _update(
        self,
        *,
        invocation: "ToolInvocation",
        owner_external_user_id: str | None,
        arguments: dict[str, Any],
    ) -> DomainToolReply:
        record = self._resolve_task(
            invocation=invocation,
            owner_external_user_id=owner_external_user_id,
            arguments=arguments,
        )
        schedule_payload = None
        if isinstance(arguments.get("schedule"), dict) or str(arguments.get("schedule_text") or "").strip():
            reference_now = self._reference_now(invocation=invocation)
            schedule_payload = normalize_schedule_input(
                self._normalize_schedule_argument(invocation=invocation, arguments=arguments),
                schedule_text=str(arguments.get("schedule_text") or "").strip() or None,
                default_timezone=self.default_timezone,
                now=reference_now,
            )
        prompt_value = str(arguments.get("prompt") or "").strip() if "prompt" in arguments else record.prompt_text
        metadata_payload = self._merged_metadata(
            prompt_text=prompt_value,
            arguments=arguments,
            existing_metadata=dict(record.metadata_json or {}),
        )
        updated = self.repository.update(
            task_id=record.id,
            name=str(arguments.get("name") or "").strip() or None,
            prompt_text=str(arguments.get("prompt") or "").strip() or None,
            schedule=schedule_payload,
            enabled=arguments.get("enabled") if "enabled" in arguments else None,
            run_immediately=bool(arguments.get("run_immediately") or False) if "run_immediately" in arguments else None,
            metadata=metadata_payload,
        )
        reply_lines = [
            f"好的，已更新提醒「{updated.name}」。",
            f"下次执行时间：{self._format_datetime_for_reply(updated.next_run_at)}。",
            f"状态：{self._state_label(updated.state)}。",
        ]
        queue_status = self._queue_status_line(next_run_at=updated.next_run_at, now=datetime.now(UTC))
        if queue_status:
            reply_lines.append(queue_status)
        reply = "\n".join(reply_lines)
        return DomainToolReply(
            reply=reply,
            action="automation",
            metadata={"scheduled_task_action": "update"},
        )

    def _set_enabled(
        self,
        *,
        invocation: "ToolInvocation",
        owner_external_user_id: str | None,
        arguments: dict[str, Any],
        enabled: bool,
    ) -> DomainToolReply:
        record = self._resolve_task(
            invocation=invocation,
            owner_external_user_id=owner_external_user_id,
            arguments=arguments,
        )
        updated = self.repository.resume(task_id=record.id, run_immediately=bool(arguments.get("run_immediately") or False)) if enabled else self.repository.pause(task_id=record.id)
        return DomainToolReply(
            reply=f"{'已恢复' if enabled else '已暂停'}提醒「{updated.name}」，当前状态：{self._state_label(updated.state)}。",
            action="automation",
            metadata={"scheduled_task_action": "resume" if enabled else "pause"},
        )

    def _delete(
        self,
        *,
        invocation: "ToolInvocation",
        owner_external_user_id: str | None,
        arguments: dict[str, Any],
    ) -> DomainToolReply:
        record = self._resolve_task(
            invocation=invocation,
            owner_external_user_id=owner_external_user_id,
            arguments=arguments,
        )
        self.repository.delete(task_id=record.id)
        return DomainToolReply(
            reply=f"已删除提醒「{record.name}」。",
            action="automation",
            metadata={"scheduled_task_action": "delete"},
        )

    def _run_now(
        self,
        *,
        invocation: "ToolInvocation",
        owner_external_user_id: str | None,
        arguments: dict[str, Any],
    ) -> DomainToolReply:
        record = self._resolve_task(
            invocation=invocation,
            owner_external_user_id=owner_external_user_id,
            arguments=arguments,
        )
        updated = self.repository.run_now(task_id=record.id)
        return DomainToolReply(
            reply=f"已将提醒「{updated.name}」加入下一次 worker tick 的执行队列。",
            action="automation",
            metadata={"scheduled_task_action": "run_now"},
        )

    @staticmethod
    def _normalize_schedule_argument(
        *,
        invocation: "ToolInvocation",
        arguments: dict[str, Any],
    ) -> dict[str, Any] | None:
        schedule = arguments.get("schedule")
        if not isinstance(schedule, dict):
            return None
        normalized = dict(schedule)
        if ScheduledTaskToolHandler._should_force_one_shot_delay(
            user_text=invocation.text,
            schedule=normalized,
        ):
            normalized["kind"] = "once"
        return normalized

    @staticmethod
    def _should_force_one_shot_delay(*, user_text: str | None, schedule: dict[str, Any]) -> bool:
        kind = str(schedule.get("kind") or "").strip().lower()
        if kind not in {"once", "interval"}:
            return False
        if str(schedule.get("at") or schedule.get("run_at") or "").strip():
            return False
        text = str(user_text or "").strip().lower()
        if not text:
            return False
        if any(marker in text for marker in ("每天", "每日", "每周", "每月", "每隔", "every ", "daily", "weekly")):
            return False
        if re.search(r"(分钟|小时|天|秒)后", text) or "后提醒" in text or "之后提醒" in text:
            return True
        if text.startswith("in ") or text.startswith("after "):
            return True
        return False


@dataclass(slots=True)
class TerminalToolHandler:
    store: TerminalToolStore

    @classmethod
    def from_root(cls, root: Path) -> "TerminalToolHandler":
        return cls(store=TerminalToolStore(root))

    def execute(self, invocation: "ToolInvocation") -> DomainToolReply:
        try:
            result = self.store.run_command(
                command=str(invocation.plan.arguments.get("command") or ""),
                cwd=str(invocation.plan.arguments.get("cwd") or "."),
                timeout_seconds=int(invocation.plan.arguments.get("timeout_seconds") or 20),
            )
        except ValueError as exc:
            return DomainToolReply(reply=str(exc), action="execute", status="failed")
        return DomainToolReply(
            reply=result.render(),
            action="execute",
            status=result.status,
            metadata=result.metadata(),
        )


@dataclass(slots=True)
class SessionSearchToolHandler:
    store: SessionSearchToolStore

    @classmethod
    def from_repositories(
        cls,
        *,
        message_repository: Any,
        summary_repository: Any,
        session_map_repository: Any | None = None,
        channel_name: str = "wechat",
    ) -> "SessionSearchToolHandler":
        return cls(
            store=SessionSearchToolStore(
                message_repository=message_repository,
                summary_repository=summary_repository,
                session_map_repository=session_map_repository,
                channel_name=channel_name,
            )
        )

    def search(self, invocation: "ToolInvocation") -> DomainToolReply:
        try:
            result = self.store.search(
                session_id=invocation.session_id,
                query=str(invocation.plan.arguments.get("query") or ""),
                limit=int(invocation.plan.arguments.get("limit") or 5),
            )
        except ValueError as exc:
            return DomainToolReply(reply=str(exc), action="retrieve", status="failed")
        return DomainToolReply(
            reply=result.render(),
            action="retrieve",
            metadata=result.metadata(),
        )


@dataclass(slots=True)
class WebToolHandler:
    store: WebToolStore

    @classmethod
    def from_client(
        cls,
        http_client: httpx.Client | None = None,
        *,
        tavily_api_key: str | None = None,
        tavily_base_url: str | None = None,
    ) -> "WebToolHandler":
        return cls(
            store=WebToolStore(
                http_client=http_client,
                tavily_api_key=tavily_api_key,
                tavily_base_url=tavily_base_url or "https://api.tavily.com",
            )
        )

    def search(self, invocation: "ToolInvocation") -> DomainToolReply:
        try:
            result = self.store.search(
                query=str(invocation.plan.arguments.get("query") or ""),
                max_results=int(invocation.plan.arguments.get("max_results") or 5),
            )
        except ValueError as exc:
            return DomainToolReply(reply=str(exc), action="research", status="failed")
        except Exception as exc:
            return DomainToolReply(reply=f"web_search failed: {exc}", action="research", status="failed")
        return DomainToolReply(
            reply=result.render(),
            action="research",
            metadata=result.metadata(),
        )

    def fetch(self, invocation: "ToolInvocation") -> DomainToolReply:
        try:
            result = self.store.fetch(
                url=str(invocation.plan.arguments.get("url") or ""),
                max_chars=int(invocation.plan.arguments.get("max_chars") or 4000),
            )
        except ValueError as exc:
            return DomainToolReply(reply=str(exc), action="research", status="failed")
        except Exception as exc:
            return DomainToolReply(reply=f"web_fetch failed: {exc}", action="research", status="failed")
        return DomainToolReply(
            reply=result.render(),
            action="research",
            metadata=result.metadata(),
        )
