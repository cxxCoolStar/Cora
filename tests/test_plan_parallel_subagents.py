from __future__ import annotations

from core.agent.plan_validator import PlanValidator, validation_options_from_tool_names
from core.schemas.plan import plan_spec_from_dict
from core.tools import ToolManager


def _registered_tools() -> frozenset[str]:
    manager = ToolManager()
    return frozenset(
        spec.name
        for spec in manager.get_registered_specs(["file", "web", "skills", "session_search", "automation", "subagent"])
    )


def test_plan_validator_accepts_parallel_subagents_without_worker_tools() -> None:
    payload = {
        "plan_id": "plan-parallel-1",
        "session_id": "session-1",
        "goal": "Search workspace",
        "policy_profile": "coding_full",
        "tasks": [
            {
                "task_id": "task-search",
                "title": "Parallel search",
                "tool_names": [],
                "instruction": "Search for symbols in parallel.",
                "parallel_subagents": [
                    {
                        "instruction": "Search src for hello_agent.",
                        "tool_names": ["search_files"],
                    },
                    {
                        "instruction": "Search tests for fixtures.",
                        "tool_names": ["search_files"],
                    },
                ],
            }
        ],
    }
    result = PlanValidator().validate(
        payload,
        options=validation_options_from_tool_names(registered_tool_names=_registered_tools()),
    )
    assert result.valid is True
    assert result.plan is not None
    assert result.plan.tasks[0].uses_parallel_subagents() is True


def test_plan_spec_from_dict_parses_parallel_subagents() -> None:
    plan = plan_spec_from_dict(
        {
            "plan_id": "plan-parallel-2",
            "session_id": "session-1",
            "goal": "Search",
            "tasks": [
                {
                    "task_id": "task-1",
                    "title": "Search",
                    "tool_names": [],
                    "instruction": "Run parallel search.",
                    "parallel_subagents": [
                        {
                            "instruction": "Search A",
                            "tool_names": ["search_files"],
                        }
                    ],
                }
            ],
        }
    )
    assert len(plan.tasks[0].parallel_subagents) == 1
    assert plan.tasks[0].parallel_subagents[0].instruction == "Search A"
