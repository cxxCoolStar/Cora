from __future__ import annotations

from pathlib import Path

from core.agent.plan_execution_state import StoredPlanExecution
from core.agent.plan_store import StoredValidatedPlan
from core.schemas.plan import plan_spec_from_dict
from core.storage.db import DatabaseManager
from core.storage.repositories import SessionRepository, SqlPlanStore


def _sample_plan() -> dict:
    return {
        "plan_id": "plan-sql-1",
        "session_id": "session-1",
        "goal": "Find files",
        "tasks": [
            {
                "task_id": "task-1",
                "title": "Search",
                "tool_names": ["search_files"],
                "instruction": "Search src.",
            }
        ],
    }


def _db_url(tmp_path: Path) -> str:
    return f"sqlite:///{(tmp_path / 'plans.db').as_posix()}"


def _dispose(*databases: DatabaseManager) -> None:
    for database in databases:
        database.engine.dispose()


def test_sql_plan_store_persists_validated_plan_across_connections(tmp_path: Path) -> None:
    url = _db_url(tmp_path)
    db1 = DatabaseManager(url)
    db1.create_all()
    sessions = SessionRepository(db1)
    session = sessions.create()
    store1 = SqlPlanStore(db1)
    store1.save(
        stored=StoredValidatedPlan(
            session_id=session.id,
            plan=plan_spec_from_dict(_sample_plan()),
            planner_run_id="run-planner-1",
        )
    )
    _dispose(db1)
    db2 = DatabaseManager(url)
    store2 = SqlPlanStore(db2)
    loaded = store2.get_latest(session_id=session.id)
    assert loaded is not None
    assert loaded.plan.plan_id == "plan-sql-1"
    assert loaded.planner_run_id == "run-planner-1"
    _dispose(db2)


def test_sql_plan_store_persists_execution_pause(tmp_path: Path) -> None:
    url = _db_url(tmp_path)
    db = DatabaseManager(url)
    db.create_all()
    session = SessionRepository(db).create()
    store = SqlPlanStore(db)
    plan = plan_spec_from_dict(_sample_plan())
    store.save(
        stored=StoredValidatedPlan(
            session_id=session.id,
            plan=plan,
            planner_run_id="run-planner-1",
        )
    )
    store.save_execution(
        execution=StoredPlanExecution(
            session_id=session.id,
            plan=plan,
            planner_run_id="run-planner-1",
            source_message_id="msg-1",
            task_index=0,
            pending_hitl_id="hitl-99",
        )
    )
    _dispose(db)
    reloaded = SqlPlanStore(DatabaseManager(url))
    execution = reloaded.get_execution(session_id=session.id)
    assert execution is not None
    assert execution.pending_hitl_id == "hitl-99"
    reloaded.clear_execution(session_id=session.id)
    assert reloaded.get_execution(session_id=session.id) is None
    assert reloaded.get_latest(session_id=session.id) is not None
    _dispose(reloaded.database)


def test_sql_plan_store_save_clears_stale_execution(tmp_path: Path) -> None:
    url = _db_url(tmp_path)
    db = DatabaseManager(url)
    db.create_all()
    session = SessionRepository(db).create()
    store = SqlPlanStore(db)
    plan = plan_spec_from_dict(_sample_plan())
    store.save_execution(
        execution=StoredPlanExecution(
            session_id=session.id,
            plan=plan,
            planner_run_id="run-planner-1",
            source_message_id="msg-1",
            task_index=0,
            pending_hitl_id="hitl-old",
        )
    )
    new_plan = plan_spec_from_dict({**_sample_plan(), "plan_id": "plan-sql-2"})
    store.save(
        stored=StoredValidatedPlan(
            session_id=session.id,
            plan=new_plan,
            planner_run_id="run-planner-2",
        )
    )
    assert store.get_execution(session_id=session.id) is None
    latest = store.get_latest(session_id=session.id)
    assert latest is not None
    assert latest.plan.plan_id == "plan-sql-2"
    _dispose(db)
