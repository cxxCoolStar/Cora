from __future__ import annotations

from core.agent.plan_validator import PlanValidator, validation_options_from_tool_names
from core.schemas.plan import PlanSpec, TaskSpec, plan_spec_from_dict
from core.tools import ToolManager


def _registered_tools() -> frozenset[str]:
    manager = ToolManager()
    return frozenset(spec.name for spec in manager.get_registered_specs(["file", "web", "skills", "session", "automation"]))


def _valid_plan_dict() -> dict:
    return {
        "plan_id": "plan-test-1",
        "session_id": "session-1",
        "goal": "List scheduled reminders",
        "policy_profile": "coding_full",
        "tasks": [
            {
                "task_id": "task-1",
                "title": "List tasks",
                "tool_names": ["scheduled_tasks"],
                "instruction": "List all scheduled tasks for the user.",
            }
        ],
    }


def test_plan_spec_from_dict_round_trip() -> None:
    plan = plan_spec_from_dict(_valid_plan_dict())
    assert plan.plan_id == "plan-test-1"
    assert plan.tasks[0].tool_names == ["scheduled_tasks"]
    assert plan.to_dict()["goal"] == "List scheduled reminders"


def test_plan_validator_accepts_valid_plan() -> None:
    options = validation_options_from_tool_names(registered_tool_names=_registered_tools())
    result = PlanValidator().validate(_valid_plan_dict(), options=options)
    assert result.valid is True
    assert result.issues == []
    assert result.plan is not None
    assert result.plan.tasks[0].task_id == "task-1"


def test_plan_validator_rejects_duplicate_task_id() -> None:
    payload = _valid_plan_dict()
    payload["tasks"] = [
        payload["tasks"][0],
        {**payload["tasks"][0], "title": "Duplicate"},
    ]
    result = PlanValidator().validate(
        payload,
        options=validation_options_from_tool_names(registered_tool_names=_registered_tools()),
    )
    assert result.valid is False
    assert any(issue.code == "duplicate_task_id" for issue in result.issues)


def test_plan_validator_rejects_unknown_tool() -> None:
    payload = _valid_plan_dict()
    payload["tasks"][0]["tool_names"] = ["not_a_real_tool"]
    result = PlanValidator().validate(
        payload,
        options=validation_options_from_tool_names(registered_tool_names=_registered_tools()),
    )
    assert result.valid is False
    assert any(issue.code == "unknown_tool" for issue in result.issues)


def test_plan_validator_rejects_empty_tool_names() -> None:
    plan = PlanSpec(
        plan_id="plan-empty-tools",
        session_id="session-1",
        goal="noop",
        tasks=[
            TaskSpec(
                task_id="task-1",
                title="Empty",
                tool_names=[],
                instruction="Do nothing",
            )
        ],
    )
    result = PlanValidator().validate(
        plan,
        options=validation_options_from_tool_names(registered_tool_names=_registered_tools()),
    )
    assert result.valid is False
    assert any(issue.code == "empty_tool_names" for issue in result.issues)


def test_plan_validator_rejects_shell_exec_on_wechat_platform() -> None:
    payload = _valid_plan_dict()
    payload["tasks"][0]["tool_names"] = ["shell_exec"]
    options = validation_options_from_tool_names(
        registered_tool_names=_registered_tools() | {"shell_exec"},
        platform="wechat",
    )
    result = PlanValidator().validate(payload, options=options)
    assert result.valid is False
    assert any(issue.code == "forbidden_tool" for issue in result.issues)


def test_plan_validator_rejects_invalid_policy_profile() -> None:
    payload = _valid_plan_dict()
    payload["policy_profile"] = "not_a_profile"
    result = PlanValidator().validate(
        payload,
        options=validation_options_from_tool_names(registered_tool_names=_registered_tools()),
    )
    assert result.valid is False
    assert any(issue.code == "invalid_policy_profile" for issue in result.issues)


def test_plan_validator_rejects_malformed_payload() -> None:
    result = PlanValidator().validate(
        {"tasks": "not-a-list"},
        options=validation_options_from_tool_names(registered_tool_names=_registered_tools()),
    )
    assert result.valid is False
    assert any(issue.code == "invalid_plan_shape" for issue in result.issues)
