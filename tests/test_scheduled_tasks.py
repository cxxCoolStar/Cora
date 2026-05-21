import asyncio
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.config import CoreSettings  # noqa: E402
from core.clawbot.dependencies import build_clawbot_container  # noqa: E402
from core.agent.skill_protocol import PendingRequest, PendingStateDelta, SkillExecutionResult  # noqa: E402
from core.storage.db import DatabaseManager  # noqa: E402
from core.clawbot.planner import ToolPlan  # noqa: E402
from core.clawbot.tool_domains import ScheduledTaskToolHandler  # noqa: E402
from core.llm.base import ModelClient  # noqa: E402
from core.schemas.message import Message  # noqa: E402
from core.schemas.model import ModelResponse  # noqa: E402
from core.schemas.tool import ToolCall, ToolSpec  # noqa: E402
from core.storage.repositories import MessageRepository, ScheduledTaskRepository, SessionRepository, SourceEventRepository  # noqa: E402
from core.tasks.schedule import compute_next_run_at, normalize_schedule_input  # noqa: E402
from core.tasks.worker import ScheduledTaskWorker  # noqa: E402
from core.tools.registry import ToolInvocation  # noqa: E402


def _make_database(tmp_path: Path) -> DatabaseManager:
    database = DatabaseManager(f"sqlite:///{(tmp_path / 'scheduled-tasks.db').as_posix()}")
    database.create_all()
    return database


def _temp_dir() -> Path:
    root = ROOT / ".tmp-test-files"
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(dir=root))


def _write_python_task(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _tool_reply_text(content: str) -> str:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return content
    if isinstance(payload, dict):
        reply = payload.get("content")
        if isinstance(reply, str) and reply.strip():
            return reply
        nested_reply = payload.get("reply")
        if isinstance(nested_reply, str) and nested_reply.strip():
            return nested_reply
    return content


def _build_runtime_container(tmp_path: Path, *, toolset_preset: str = "cora-wechat"):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    settings = CoreSettings(
        clawbot_database_path=tmp_path / "clawbot.db",
        files_storage_dir=tmp_path / "files",
        archive_root_dir=tmp_path / "archive",
        file_tool_root=workspace,
        toolset_preset=toolset_preset,
    )
    container = build_clawbot_container(settings=settings)
    container.initialize()
    return container


class _ForbiddenBackgroundToolModel(ModelClient):
    def __init__(self, *, forbidden_tool_name: str) -> None:
        self.forbidden_tool_name = forbidden_tool_name
        self.seen_tool_names: list[list[str]] = []
        self.tool_replies: list[str] = []

    def generate(self, *, messages: list[Message], tools: list[ToolSpec]) -> ModelResponse:
        self.seen_tool_names.append([tool.name for tool in tools])
        latest_tool = messages[-1] if messages and messages[-1].role == "tool" else None
        if latest_tool is not None:
            self.tool_replies.append(_tool_reply_text(latest_tool.content))
            return ModelResponse(assistant_text="[SILENT]")
        return ModelResponse(
            tool_calls=[
                ToolCall(
                    tool_name=self.forbidden_tool_name,
                    arguments={
                        "action": "create",
                        "name": "Nested reminder",
                        "prompt": "This should never be created.",
                        "schedule": {"kind": "once", "at": datetime(2026, 5, 19, 2, 0, tzinfo=UTC).isoformat()},
                    },
                )
            ]
        )


class _ClarifyingSkillModel(ModelClient):
    def __init__(self) -> None:
        self.seen_tool_names: list[list[str]] = []

    def generate(self, *, messages: list[Message], tools: list[ToolSpec]) -> ModelResponse:
        self.seen_tool_names.append([tool.name for tool in tools])
        return ModelResponse(
            tool_calls=[
                ToolCall(
                    tool_name="skill_run",
                    arguments={
                        "name": "archive-core",
                        "script_path": "scripts/archive_dispatch.py",
                        "input": {"query": "check the latest item"},
                    },
                )
            ]
        )


def test_schedule_normalization_and_next_run() -> None:
    now = datetime(2026, 5, 18, 1, 0, tzinfo=UTC)

    interval = normalize_schedule_input(schedule_text="every 5m", now=now)
    assert interval == {"kind": "interval", "interval_seconds": 300}
    assert compute_next_run_at(interval, now=now) == datetime(2026, 5, 18, 1, 5, tzinfo=UTC)

    daily = normalize_schedule_input(schedule_text="daily 09:30 Asia/Shanghai", now=now)
    assert daily["kind"] == "daily"
    assert daily["timezone"] == "Asia/Shanghai"
    assert compute_next_run_at(daily, now=now) == datetime(2026, 5, 18, 1, 30, tzinfo=UTC)

    weekly = normalize_schedule_input(
        schedule={"kind": "weekly", "days_of_week": ["mon", "wed"], "hour": 9, "minute": 30, "timezone": "Asia/Shanghai"},
        now=now,
    )
    assert weekly["days_of_week"] == [0, 2]
    assert compute_next_run_at(weekly, now=now) == datetime(2026, 5, 18, 1, 30, tzinfo=UTC)

    cron = normalize_schedule_input(
        schedule={"kind": "cron", "expr": "0 9 15 * *", "timezone": "Asia/Shanghai"},
        now=now,
    )
    assert cron == {"kind": "cron", "expr": "0 9 15 * *", "timezone": "Asia/Shanghai"}
    assert compute_next_run_at(cron, now=now) == datetime(2026, 6, 15, 1, 0, tzinfo=UTC)

    delayed_once = normalize_schedule_input(
        schedule={"kind": "once", "intervval_seconds": 60},
        now=now,
    )
    assert delayed_once == {"kind": "once", "at": datetime(2026, 5, 18, 1, 1, tzinfo=UTC).isoformat()}


def test_scheduled_task_repository_state_transitions() -> None:
    tmp_path = _temp_dir()
    database = _make_database(tmp_path)
    session_repository = SessionRepository(database)
    task_repository = ScheduledTaskRepository(database)
    session = session_repository.create()

    created = task_repository.create(
        session_id=session.id,
        owner_external_user_id="wx-user-1",
        name="ETF watcher",
        prompt_text="Check ETF 513650 and notify me when needed.",
        schedule={"kind": "interval", "interval_minutes": 5},
        enabled=True,
    )

    listed = task_repository.list_for_scope(session_id=session.id, owner_external_user_id="wx-user-1")
    assert [record.id for record in listed] == [created.id]

    paused = task_repository.pause(task_id=created.id)
    assert paused.state == "paused"
    assert paused.enabled == 0

    resumed = task_repository.resume(task_id=created.id, run_immediately=True)
    assert resumed.state == "scheduled"
    assert resumed.enabled == 1
    assert resumed.next_run_at is not None

    resolved = task_repository.resolve_for_scope(
        task_ref="ETF watcher",
        session_id=session.id,
        owner_external_user_id="wx-user-1",
    )
    assert resolved is not None
    assert resolved.id == created.id


def test_scheduled_task_worker_executes_due_task_and_delivers_reply() -> None:
    tmp_path = _temp_dir()
    database = _make_database(tmp_path)
    session_repository = SessionRepository(database)
    task_repository = ScheduledTaskRepository(database)
    session = session_repository.create()

    created = task_repository.create(
        session_id=session.id,
        owner_external_user_id="wx-user-2",
        name="Morning reminder",
        prompt_text="Remind me to review the market open.",
        schedule={"kind": "once", "at": datetime.now(UTC).isoformat()},
        enabled=True,
    )

    delivered: list[tuple[str, str]] = []
    seen_source_metadata: list[dict] = []
    created_execution_session_ids: list[str] = []

    class _StubClawBotService:
        def create_job_execution_session(
            self,
            *,
            origin_session_id: str,
            scheduled_task_id: str,
            task_name: str,
            execution_mode: str,
            owner_external_user_id: str | None = None,
        ):
            session_id = f"job-session-{len(created_execution_session_ids) + 1}"
            created_execution_session_ids.append(session_id)
            return SimpleNamespace(id=session_id)

        async def reply_outcome(self, *, session_id: str, text: str, source_metadata=None):
            seen_source_metadata.append({"session_id": session_id, **dict(source_metadata or {})})
            return SimpleNamespace(reply="Time to review the market open.", status="completed")

    async def _send_text(user_id: str, text: str):
        delivered.append((user_id, text))
        return {"ret": 0, "errcode": 0}

    worker = ScheduledTaskWorker(
        repository=task_repository,
        clawbot_service=_StubClawBotService(),  # type: ignore[arg-type]
        send_text=_send_text,
        lock_path=tmp_path / "scheduled-tasks" / ".tick.lock",
        poll_interval_seconds=1,
        lease_seconds=60,
    )

    executed = asyncio.run(worker.tick())
    assert executed == 1
    assert delivered == [("wx-user-2", "Time to review the market open.")]
    assert seen_source_metadata[0]["scheduled_task_id"] == created.id
    assert created_execution_session_ids == ["job-session-1"]
    assert seen_source_metadata[0]["session_id"] == "job-session-1"
    assert seen_source_metadata[0]["scheduled_task_origin_session_id"] == session.id
    assert seen_source_metadata[0]["scheduled_task_execution_session_id"] == "job-session-1"

    updated = task_repository.get(task_id=created.id)
    assert updated.state == "completed"
    assert updated.enabled == 0
    assert updated.last_status == "ok"
    assert updated.last_reply_preview == "Time to review the market open."
    assert updated.metadata_json["last_run"]["execution_session_id"] == "job-session-1"


def test_scheduled_task_worker_suppresses_silent_reply() -> None:
    tmp_path = _temp_dir()
    database = _make_database(tmp_path)
    session_repository = SessionRepository(database)
    task_repository = ScheduledTaskRepository(database)
    session = session_repository.create()

    created = task_repository.create(
        session_id=session.id,
        owner_external_user_id="wx-user-3",
        name="Silent monitor",
        prompt_text="Check the condition. Reply [SILENT] when nothing needs attention.",
        schedule={"kind": "once", "at": datetime.now(UTC).isoformat()},
        enabled=True,
    )

    delivered: list[tuple[str, str]] = []

    class _StubClawBotService:
        def create_job_execution_session(
            self,
            *,
            origin_session_id: str,
            scheduled_task_id: str,
            task_name: str,
            execution_mode: str,
            owner_external_user_id: str | None = None,
        ):
            return SimpleNamespace(id="job-session-silent")

        async def reply_outcome(self, *, session_id: str, text: str, source_metadata=None):
            return SimpleNamespace(reply="[SILENT]", status="completed")

    async def _send_text(user_id: str, text: str):
        delivered.append((user_id, text))
        return {"ret": 0, "errcode": 0}

    worker = ScheduledTaskWorker(
        repository=task_repository,
        clawbot_service=_StubClawBotService(),  # type: ignore[arg-type]
        send_text=_send_text,
        lock_path=tmp_path / "scheduled-tasks" / ".tick.lock",
        poll_interval_seconds=1,
        lease_seconds=60,
    )

    executed = asyncio.run(worker.tick())
    assert executed == 1
    assert delivered == []

    updated = task_repository.get(task_id=created.id)
    assert updated.id == created.id
    assert updated.last_status == "ok"
    assert updated.last_reply_preview is None
    assert updated.metadata_json["last_run"]["execution_session_id"] == "job-session-silent"


def test_scheduled_task_worker_dispatches_skill_execution_mode() -> None:
    tmp_path = _temp_dir()
    database = _make_database(tmp_path)
    session_repository = SessionRepository(database)
    task_repository = ScheduledTaskRepository(database)
    session = session_repository.create()

    created = task_repository.create(
        session_id=session.id,
        owner_external_user_id="wx-user-4",
        name="ETF skill runner",
        prompt_text="Check the current ETF condition and summarize it.",
        schedule={"kind": "once", "at": datetime.now(UTC).isoformat()},
        enabled=True,
        metadata={
            "execution": {
                "mode": "skill",
                "skill_name": "etf-monitor",
                "script_path": "scripts/check.py",
                "input": {"symbol": "513650"},
            }
        },
    )

    delivered: list[tuple[str, str]] = []
    seen_calls: list[dict[str, object]] = []

    class _StubClawBotService:
        def create_job_execution_session(
            self,
            *,
            origin_session_id: str,
            scheduled_task_id: str,
            task_name: str,
            execution_mode: str,
            owner_external_user_id: str | None = None,
        ):
            return SimpleNamespace(id="job-session-skill")

        async def execute_tool_plan_outcome(self, *, session_id: str, plan: ToolPlan, text: str | None = None, source_metadata=None):
            seen_calls.append(
                {
                    "session_id": session_id,
                    "tool": plan.tool,
                    "arguments": dict(plan.arguments),
                    "text": text,
                    "source_metadata": dict(source_metadata or {}),
                }
            )
            return SimpleNamespace(reply="ETF 513650 is stable.", status="completed")

    async def _send_text(user_id: str, text: str):
        delivered.append((user_id, text))
        return {"ret": 0, "errcode": 0}

    worker = ScheduledTaskWorker(
        repository=task_repository,
        clawbot_service=_StubClawBotService(),  # type: ignore[arg-type]
        send_text=_send_text,
        lock_path=tmp_path / "scheduled-tasks" / ".tick.lock",
        poll_interval_seconds=1,
        lease_seconds=60,
    )

    executed = asyncio.run(worker.tick())
    assert executed == 1
    assert delivered == [("wx-user-4", "ETF 513650 is stable.")]
    assert seen_calls == [
        {
            "session_id": "job-session-skill",
            "tool": "skill_run",
            "arguments": {
                "name": "etf-monitor",
                "script_path": "scripts/check.py",
                "input": {"symbol": "513650"},
            },
            "text": "Check the current ETF condition and summarize it.",
            "source_metadata": {
                "channel": "scheduled_task",
                "event_type": "scheduled_task",
                "external_user_id": "wx-user-4",
                "scheduled_task_id": created.id,
                "scheduled_task_name": "ETF skill runner",
                "scheduled_task_execution_mode": "skill",
                "scheduled_task_origin_session_id": session.id,
                "scheduled_task_execution_session_id": "job-session-skill",
                "session_kind": "job_execution",
            },
        }
    ]

    updated = task_repository.get(task_id=created.id)
    assert updated.state == "completed"
    assert updated.last_reply_preview == "ETF 513650 is stable."
    assert updated.metadata_json["last_run"]["execution_session_id"] == "job-session-skill"


def test_scheduled_task_worker_runs_workspace_script_mode() -> None:
    tmp_path = _temp_dir()
    database = _make_database(tmp_path)
    session_repository = SessionRepository(database)
    task_repository = ScheduledTaskRepository(database)
    session = session_repository.create()
    script_path = tmp_path / "scripts" / "task-check.py"
    _write_python_task(
        script_path,
        "\n".join(
            [
                "import json, sys",
                "payload = json.loads(sys.stdin.read() or '{}')",
                "symbol = payload.get('arguments', {}).get('symbol', 'unknown')",
                "print(json.dumps({'message': f'Script checked {symbol}.', 'action': 'execute', 'status': 'completed'}))",
            ]
        ),
    )

    created = task_repository.create(
        session_id=session.id,
        owner_external_user_id="wx-user-5",
        name="Workspace script",
        prompt_text="",
        schedule={"kind": "once", "at": datetime.now(UTC).isoformat()},
        enabled=True,
        metadata={
            "execution": {
                "mode": "script",
                "script_path": "scripts/task-check.py",
                "input": {"symbol": "159915"},
            }
        },
    )

    delivered: list[tuple[str, str]] = []

    class _StubClawBotService:
        file_tool_root = tmp_path

        def create_job_execution_session(
            self,
            *,
            origin_session_id: str,
            scheduled_task_id: str,
            task_name: str,
            execution_mode: str,
            owner_external_user_id: str | None = None,
        ):
            return SimpleNamespace(id="job-session-script")

    async def _send_text(user_id: str, text: str):
        delivered.append((user_id, text))
        return {"ret": 0, "errcode": 0}

    worker = ScheduledTaskWorker(
        repository=task_repository,
        clawbot_service=_StubClawBotService(),  # type: ignore[arg-type]
        send_text=_send_text,
        lock_path=tmp_path / "scheduled-tasks" / ".tick.lock",
        poll_interval_seconds=1,
        lease_seconds=60,
    )

    executed = asyncio.run(worker.tick())
    assert executed == 1
    assert delivered == [("wx-user-5", "Script checked 159915.")]

    updated = task_repository.get(task_id=created.id)
    assert updated.state == "completed"
    assert updated.last_reply_preview == "Script checked 159915."
    assert updated.metadata_json["last_run"]["execution_session_id"] == "job-session-script"


def test_scheduled_task_worker_keeps_origin_session_clean_and_records_child_execution_session() -> None:
    tmp_path = _temp_dir()
    database = _make_database(tmp_path)
    session_repository = SessionRepository(database)
    message_repository = MessageRepository(database)
    task_repository = ScheduledTaskRepository(database)
    origin_session = session_repository.create()
    message_repository.add_user_message(
        session_id=origin_session.id,
        content="Please remind me to review the market open.",
    )

    created = task_repository.create(
        session_id=origin_session.id,
        owner_external_user_id="wx-user-6",
        name="Origin-safe reminder",
        prompt_text="Remind me to review the market open.",
        schedule={"kind": "once", "at": datetime.now(UTC).isoformat()},
        enabled=True,
    )

    class _FakeClawBotService:
        def __init__(self) -> None:
            self.execution_session_ids: list[str] = []

        def create_job_execution_session(
            self,
            *,
            origin_session_id: str,
            scheduled_task_id: str,
            task_name: str,
            execution_mode: str,
            owner_external_user_id: str | None = None,
        ):
            record = session_repository.create(
                session_kind="job_execution",
                parent_session_id=origin_session_id,
                metadata={
                    "scheduled_task_id": scheduled_task_id,
                    "scheduled_task_name": task_name,
                    "scheduled_task_execution_mode": execution_mode,
                },
            )
            self.execution_session_ids.append(record.id)
            return record

        async def reply_outcome(self, *, session_id: str, text: str, source_metadata=None):
            message_repository.add_user_message(session_id=session_id, content=text)
            message_repository.add_assistant_message(
                session_id=session_id,
                content="Time to review the market open.",
                metadata={"source_metadata": dict(source_metadata or {})},
            )
            return SimpleNamespace(reply="Time to review the market open.", status="completed")

    delivered: list[tuple[str, str]] = []

    async def _send_text(user_id: str, text: str):
        delivered.append((user_id, text))
        return {"ret": 0, "errcode": 0}

    fake_service = _FakeClawBotService()
    worker = ScheduledTaskWorker(
        repository=task_repository,
        clawbot_service=fake_service,  # type: ignore[arg-type]
        send_text=_send_text,
        lock_path=tmp_path / "scheduled-tasks" / ".tick.lock",
        poll_interval_seconds=1,
        lease_seconds=60,
    )

    executed = asyncio.run(worker.tick())

    assert executed == 1
    assert delivered == [("wx-user-6", "Time to review the market open.")]
    assert len(fake_service.execution_session_ids) == 1

    origin_messages = message_repository.list_by_session(session_id=origin_session.id)
    assert [message.content for message in origin_messages] == [
        "Please remind me to review the market open.",
    ]

    execution_session_id = fake_service.execution_session_ids[0]
    execution_record = session_repository.get(execution_session_id)
    assert execution_record.session_kind == "job_execution"
    assert execution_record.parent_session_id == origin_session.id

    execution_messages = message_repository.list_by_session(session_id=execution_session_id)
    assert [message.role for message in execution_messages] == ["user", "assistant"]
    assert execution_messages[0].content == "Remind me to review the market open."
    assert execution_messages[1].content == "Time to review the market open."

    updated = task_repository.get(task_id=created.id)
    assert updated.metadata_json["last_run"]["origin_session_id"] == origin_session.id
    assert updated.metadata_json["last_run"]["execution_session_id"] == execution_session_id


def test_scheduled_task_worker_blocks_forbidden_tool_calls_end_to_end(tmp_path: Path) -> None:
    container = _build_runtime_container(tmp_path)
    model = _ForbiddenBackgroundToolModel(forbidden_tool_name="scheduled_tasks")
    container.clawbot_service.model_client = model
    origin_session = container.session_repository.create()
    created = container.scheduled_task_repository.create(
        session_id=origin_session.id,
        owner_external_user_id="wx-user-7",
        name="No nested reminders",
        prompt_text="Check status quietly.",
        schedule={"kind": "once", "at": datetime.now(UTC).isoformat()},
        enabled=True,
    )

    delivered: list[tuple[str, str]] = []

    async def _send_text(user_id: str, text: str):
        delivered.append((user_id, text))
        return {"ret": 0, "errcode": 0}

    worker = ScheduledTaskWorker(
        repository=container.scheduled_task_repository,
        clawbot_service=container.clawbot_service,
        send_text=_send_text,
        lock_path=tmp_path / "scheduled-tasks" / ".tick.lock",
        poll_interval_seconds=1,
        lease_seconds=60,
    )

    executed = asyncio.run(worker.tick())

    assert executed == 1
    assert model.seen_tool_names
    assert "skill_run" in model.seen_tool_names[0]
    assert "scheduled_tasks" not in model.seen_tool_names[0]
    assert "user_memory" not in model.seen_tool_names[0]
    assert "shell_exec" not in model.seen_tool_names[0]
    assert model.tool_replies
    assert "unavailable during background scheduled execution" in model.tool_replies[0]
    assert delivered == [("wx-user-7", model.tool_replies[0])]

    scoped_records = container.scheduled_task_repository.list_for_scope(
        session_id=origin_session.id,
        owner_external_user_id="wx-user-7",
    )
    assert [record.id for record in scoped_records] == [created.id]

    updated = container.scheduled_task_repository.get(task_id=created.id)
    assert updated.state == "completed"
    assert updated.last_status == "ok"
    assert updated.last_reply_preview == model.tool_replies[0]

    execution_session_id = updated.metadata_json["last_run"]["execution_session_id"]
    execution_record = container.session_repository.get(execution_session_id)
    assert execution_record.session_kind == "job_execution"
    assert execution_record.parent_session_id == origin_session.id
    assert container.pending_state_repository.get_latest_pending(session_id=execution_session_id) is None

    execution_messages = container.message_repository.list_by_session(session_id=execution_session_id)
    assert [message.role for message in execution_messages] == ["user", "assistant"]
    assert execution_messages[0].content == "Check status quietly."
    assert execution_messages[1].content == model.tool_replies[0]


def test_scheduled_task_worker_suppresses_clarifying_skill_without_pending_state_end_to_end(tmp_path: Path) -> None:
    container = _build_runtime_container(tmp_path)
    model = _ClarifyingSkillModel()
    container.clawbot_service.model_client = model
    container.tool_executor.skill_script_executor.run = lambda request: SkillExecutionResult(
        message="Which workspace should I inspect?",
        action="skill",
        pending_state_delta=PendingStateDelta(
            request=PendingRequest(
                kind="choice",
                question="Which workspace should I inspect?",
                choices=["prod", "staging"],
            )
        ),
    )
    origin_session = container.session_repository.create()
    created = container.scheduled_task_repository.create(
        session_id=origin_session.id,
        owner_external_user_id="wx-user-8",
        name="Clarifying archive check",
        prompt_text="Check the latest item and report back only if needed.",
        schedule={"kind": "once", "at": datetime.now(UTC).isoformat()},
        enabled=True,
    )

    delivered: list[tuple[str, str]] = []

    async def _send_text(user_id: str, text: str):
        delivered.append((user_id, text))
        return {"ret": 0, "errcode": 0}

    worker = ScheduledTaskWorker(
        repository=container.scheduled_task_repository,
        clawbot_service=container.clawbot_service,
        send_text=_send_text,
        lock_path=tmp_path / "scheduled-tasks" / ".tick.lock",
        poll_interval_seconds=1,
        lease_seconds=60,
    )

    executed = asyncio.run(worker.tick())

    assert executed == 1
    assert delivered == []
    assert model.seen_tool_names
    assert "skill_run" in model.seen_tool_names[0]
    assert "scheduled_tasks" not in model.seen_tool_names[0]
    assert "user_memory" not in model.seen_tool_names[0]
    assert "shell_exec" not in model.seen_tool_names[0]

    updated = container.scheduled_task_repository.get(task_id=created.id)
    assert updated.state == "completed"
    assert updated.last_status == "ok"
    assert updated.last_reply_preview is None

    execution_session_id = updated.metadata_json["last_run"]["execution_session_id"]
    assert container.pending_state_repository.get_latest_pending(session_id=execution_session_id) is None

    execution_record = container.session_repository.get(execution_session_id)
    assert execution_record.session_kind == "job_execution"
    assert execution_record.parent_session_id == origin_session.id

    execution_messages = container.message_repository.list_by_session(session_id=execution_session_id)
    assert [message.role for message in execution_messages] == ["user", "assistant"]
    assert execution_messages[0].content == "Check the latest item and report back only if needed."
    assert execution_messages[1].content == "[SILENT]"


def test_execute_tool_plan_outcome_suppresses_clarifying_skill_for_job_execution_direct_runs(tmp_path: Path) -> None:
    container = _build_runtime_container(tmp_path)
    origin_session = container.session_repository.create()
    execution_session = container.clawbot_service.create_job_execution_session(
        origin_session_id=origin_session.id,
        scheduled_task_id="task-direct-skill",
        task_name="Direct clarifying skill",
        execution_mode="skill",
        owner_external_user_id="wx-user-9",
    )
    container.tool_executor.skill_script_executor.run = lambda request: SkillExecutionResult(
        message="Which workspace should I inspect?",
        action="skill",
        pending_state_delta=PendingStateDelta(
            request=PendingRequest(
                kind="choice",
                question="Which workspace should I inspect?",
                choices=["prod", "staging"],
            )
        ),
    )

    outcome = asyncio.run(
        container.clawbot_service.execute_tool_plan_outcome(
            session_id=execution_session.id,
            text="Check the latest item and report back only if needed.",
            plan=ToolPlan(
                tool="skill_run",
                arguments={
                    "name": "archive-core",
                    "script_path": "scripts/archive_dispatch.py",
                    "input": {"query": "check the latest item"},
                },
                reason="test",
                source="scheduled_task",
            ),
        )
    )

    assert outcome.status == "failed"
    assert outcome.disposition == "respond"
    assert outcome.reply == "[SILENT]"
    assert outcome.tool_trace[0]["hints"]["policy_tag"] == "no_clarify"
    assert container.pending_state_repository.get_latest_pending(session_id=execution_session.id) is None

    execution_messages = container.message_repository.list_by_session(session_id=execution_session.id)
    assert [message.role for message in execution_messages] == ["user", "assistant"]
    assert execution_messages[0].content == "Check the latest item and report back only if needed."
    assert execution_messages[1].content == "[SILENT]"


def test_scheduled_task_tool_coerces_one_shot_reminder_from_user_text() -> None:
    tmp_path = _temp_dir()
    database = _make_database(tmp_path)
    session_repository = SessionRepository(database)
    task_repository = ScheduledTaskRepository(database)
    session = session_repository.create()

    handler = ScheduledTaskToolHandler(repository=task_repository)
    invocation = ToolInvocation(
        session_id=session.id,
        source_message_id="msg-1",
        plan=ToolPlan(
            tool="scheduled_tasks",
            arguments={
                "action": "create",
                "name": "喝水提醒",
                "prompt": "提醒用户该喝水了！",
                "schedule": {"kind": "interval", "innterval_seconds": 65},
            },
            reason="test",
            source="llm_tool_call",
        ),
        text="一分钟后提醒我喝水",
        upload=None,
        context={},
    )

    result = handler.execute(invocation, owner_external_user_id="wx-user-1")
    assert result.status == "completed"

    records = task_repository.list_for_scope(session_id=session.id, owner_external_user_id="wx-user-1")
    assert len(records) == 1
    assert records[0].schedule_json["kind"] == "once"


def test_scheduled_task_tool_creates_skill_execution_without_prompt() -> None:
    tmp_path = _temp_dir()
    database = _make_database(tmp_path)
    session_repository = SessionRepository(database)
    task_repository = ScheduledTaskRepository(database)
    session = session_repository.create()

    handler = ScheduledTaskToolHandler(repository=task_repository)
    invocation = ToolInvocation(
        session_id=session.id,
        source_message_id="msg-skill",
        plan=ToolPlan(
            tool="scheduled_tasks",
            arguments={
                "action": "create",
                "name": "ETF skill monitor",
                "schedule": {"kind": "cron", "expr": "0 9 15 * *", "timezone": "Asia/Shanghai"},
                "execution": {
                    "mode": "skill",
                    "skill_name": "etf-monitor",
                    "script_path": "scripts/check.py",
                    "input": {"symbol": "513650"},
                },
            },
            reason="test",
            source="llm_tool_call",
        ),
        text="每月15号上午9点跑一次 ETF 技能任务",
        upload=None,
        context={},
    )

    result = handler.execute(invocation, owner_external_user_id="wx-user-1")
    assert result.status == "completed"

    records = task_repository.list_for_scope(session_id=session.id, owner_external_user_id="wx-user-1")
    assert len(records) == 1
    assert records[0].name == "ETF skill monitor"
    assert records[0].prompt_text == ""
    assert records[0].schedule_json == {"kind": "cron", "expr": "0 9 15 * *", "timezone": "Asia/Shanghai"}
    assert records[0].metadata_json["execution"] == {
        "mode": "skill",
        "skill_name": "etf-monitor",
        "script_path": "scripts/check.py",
        "input": {"symbol": "513650"},
    }


def test_scheduled_task_tool_anchors_relative_delay_to_source_event_time_and_formats_local_next_run() -> None:
    tmp_path = _temp_dir()
    database = _make_database(tmp_path)
    session_repository = SessionRepository(database)
    source_event_repository = SourceEventRepository(database)
    task_repository = ScheduledTaskRepository(database)
    session = session_repository.create()
    source_created_at = datetime(2026, 5, 18, 2, 5, 11, tzinfo=UTC)
    source_event = source_event_repository.create(
        session_id=session.id,
        source_message_id=None,
        channel="wechat",
        external_event_id="evt-1",
        external_user_id="wx-user-1",
        event_type="text",
        raw_text="一分钟后提醒我喝水",
        metadata={
            "source_create_time_ms": int(source_created_at.timestamp() * 1000),
            "source_created_at": source_created_at.isoformat(),
        },
    )

    handler = ScheduledTaskToolHandler(
        repository=task_repository,
        default_timezone="Asia/Shanghai",
        source_event_repository=source_event_repository,
    )
    invocation = ToolInvocation(
        session_id=session.id,
        source_message_id="msg-1",
        plan=ToolPlan(
            tool="scheduled_tasks",
            arguments={
                "action": "create",
                "name": "喝水提醒",
                "prompt": "提醒用户该喝水了。",
                "schedule": {"kind": "interval", "interval_seconds": 60},
            },
            reason="test",
            source="llm_tool_call",
        ),
        text="一分钟后提醒我喝水",
        upload=None,
        context={"current_source_event_id": source_event.id},
    )

    result = handler.execute(invocation, owner_external_user_id="wx-user-1")
    assert result.status == "completed"

    records = task_repository.list_for_scope(session_id=session.id, owner_external_user_id="wx-user-1")
    assert len(records) == 1
    assert records[0].schedule_json["kind"] == "once"
    assert records[0].schedule_json["at"] == datetime(2026, 5, 18, 2, 6, 11, tzinfo=UTC).isoformat()
    assert "2026-05-18 10:06:11 (Asia/Shanghai)" in result.reply


def test_scheduled_task_worker_shortens_sleep_to_nearest_due_task() -> None:
    tmp_path = _temp_dir()
    database = _make_database(tmp_path)
    session_repository = SessionRepository(database)
    task_repository = ScheduledTaskRepository(database)
    session = session_repository.create()
    now = datetime(2026, 5, 18, 1, 0, tzinfo=UTC)
    task_repository.create(
        session_id=session.id,
        owner_external_user_id="wx-user-1",
        name="soon",
        prompt_text="Run soon.",
        schedule={"kind": "once", "at": (now + timedelta(seconds=17)).isoformat()},
        enabled=True,
    )

    worker = ScheduledTaskWorker(
        repository=task_repository,
        clawbot_service=SimpleNamespace(),  # type: ignore[arg-type]
        lock_path=tmp_path / "scheduled-tasks" / ".tick.lock",
        poll_interval_seconds=30,
        lease_seconds=60,
    )

    assert worker.next_sleep_seconds(now=now) == 17


def test_container_defaults_scheduled_task_timezone_to_asia_shanghai() -> None:
    tmp_path = _temp_dir()
    container = build_clawbot_container(
        settings=CoreSettings(
            clawbot_database_path=tmp_path / "clawbot.db",
            files_storage_dir=tmp_path / "files",
            archive_root_dir=tmp_path / "archive",
            scheduler_timezone=None,
            wechat_session_timezone=None,
        )
    )

    assert container.tool_executor.scheduled_task_tools is not None
    assert container.tool_executor.scheduled_task_tools.default_timezone == "Asia/Shanghai"
