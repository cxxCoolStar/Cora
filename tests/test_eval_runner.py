from __future__ import annotations

from pathlib import Path

from core.clawbot.schemas import TurnResponse
from core.evals.models import EvalCaseResult, EvalObservedState
from core.evals.judge import tool_names_from_trace
from core.evals.report import html_report_path, run_result_to_html
from core.evals.runner import EvalCase, EvalRunner


def test_eval_runner_loads_cases(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    regression_dir = cases_dir / "regression"
    regression_dir.mkdir(parents=True)
    (regression_dir / "sample.json").write_text(
        """
        {
          "id": "sample_case",
          "type": "regression",
          "description": "sample",
          "setup": {
            "workspace_files": {
              "src/example.py": "print('hello')\\n"
            }
          },
          "steps": [
            {
              "input": {"text": "hello"},
              "expect": {"reply_contains_all": ["hi"]}
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    runner = EvalRunner(project_root=tmp_path, cases_dir=cases_dir)
    cases = runner.load_cases()

    assert len(cases) == 1
    assert cases[0].id == "sample_case"
    assert cases[0].case_type == "regression"
    assert cases[0].setup.workspace_files == {"src/example.py": "print('hello')\n"}
    assert cases[0].steps[0].input.text == "hello"


def test_eval_runner_filters_case_type(tmp_path: Path) -> None:
    regression_dir = tmp_path / "cases" / "regression"
    capability_dir = tmp_path / "cases" / "capability"
    regression_dir.mkdir(parents=True)
    capability_dir.mkdir(parents=True)
    regression_case = """
        {
          "id": "regression_case",
          "type": "regression",
          "steps": [{"input": {"text": "hello"}, "expect": {}}]
        }
    """
    capability_case = """
        {
          "id": "capability_case",
          "type": "capability",
          "steps": [{"input": {"text": "hello"}, "expect": {}}]
        }
    """
    (regression_dir / "one.json").write_text(regression_case, encoding="utf-8")
    (capability_dir / "two.json").write_text(capability_case, encoding="utf-8")

    runner = EvalRunner(project_root=tmp_path, cases_dir=tmp_path / "cases", case_type="capability")

    cases = runner.load_cases()

    assert [case.id for case in cases] == ["capability_case"]


def test_eval_runner_evaluate_step_reports_failures(tmp_path: Path) -> None:
    case = EvalCase.from_path(_write_case(tmp_path))
    runner = EvalRunner(project_root=tmp_path, cases_dir=tmp_path)
    response = TurnResponse(
        reply="我不知道。",
        status="completed",
        disposition="respond",
        action="chat",
        item_id=None,
        needs_clarification=False,
        artifacts=[],
        trace=[],
        decision_source="llm_tool_call",
    )

    result = runner.evaluate_step(case=case, step=case.steps[0], index=1, response=response)

    assert not result.ok
    assert any("reply missing required text" in failure.message for failure in result.failures)
    assert result.failure_category == "assertion_failure"


def test_eval_runner_evaluate_step_checks_state_assertions(tmp_path: Path) -> None:
    path = tmp_path / "sample-state.json"
    path.write_text(
        """
        {
          "id": "sample_case",
          "type": "regression",
          "steps": [
            {
              "input": {"text": "hello"},
              "expect": {
                "state": {
                  "item_count": 1,
                  "deleted_item_count": 0,
                  "pending_exists": false,
                  "user_memory_contains_all": ["coffee"]
                }
              }
            }
          ]
        }
        """,
        encoding="utf-8",
    )
    case = EvalCase.from_path(path)
    runner = EvalRunner(project_root=tmp_path, cases_dir=tmp_path)
    response = TurnResponse(
        reply="ok",
        status="completed",
        disposition="respond",
        action="chat",
        item_id=None,
        needs_clarification=False,
        artifacts=[],
        trace=[],
        decision_source="llm_tool_call",
    )
    observed_state = EvalObservedState(
        item_count=1,
        deleted_item_count=0,
        pending_exists=False,
        pending_kind=None,
        user_memory_text="# User Memory\ncoffee\n",
    )

    result = runner.evaluate_step(
        case=case,
        step=case.steps[0],
        index=1,
        response=response,
        observed_state=observed_state,
    )

    assert result.ok
    assert result.observed_state is observed_state


def test_eval_runner_evaluate_step_checks_workspace_file_assertions(tmp_path: Path) -> None:
    path = tmp_path / "sample-workspace.json"
    path.write_text(
        """
        {
          "id": "sample_case",
          "type": "tooling",
          "steps": [
            {
              "input": {"text": "hello"},
              "expect": {
                "state": {
                  "workspace_files_exist": ["notes/todo.txt"],
                  "workspace_files_not_exist": ["notes/missing.txt"],
                  "workspace_file_contains_all": {
                    "notes/todo.txt": ["ship it", "add tests"]
                  },
                  "workspace_file_not_contains": {
                    "notes/todo.txt": ["rollback"]
                  }
                }
              }
            }
          ]
        }
        """,
        encoding="utf-8",
    )
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "notes").mkdir()
    (workspace_root / "notes" / "todo.txt").write_text("ship it\nadd tests\n", encoding="utf-8")
    case = EvalCase.from_path(path)
    runner = EvalRunner(project_root=tmp_path, cases_dir=tmp_path)
    response = TurnResponse(
        reply="ok",
        status="completed",
        disposition="respond",
        action="edit",
        item_id=None,
        needs_clarification=False,
        artifacts=[],
        trace=[],
        decision_source="llm_tool_call",
    )
    observed_state = EvalObservedState(
        item_count=0,
        deleted_item_count=0,
        pending_exists=False,
        pending_kind=None,
        user_memory_text="# User Memory\n",
        workspace_root=str(workspace_root),
    )

    result = runner.evaluate_step(
        case=case,
        step=case.steps[0],
        index=1,
        response=response,
        observed_state=observed_state,
    )

    assert result.ok
    assert result.observed_state is observed_state


def test_eval_runner_run_continues_after_infrastructure_failure(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases" / "capability"
    cases_dir.mkdir(parents=True)
    case_path = cases_dir / "sample.json"
    case_path.write_text(
        """
        {
          "id": "sample_case",
          "type": "capability",
          "steps": [{"input": {"text": "hello"}, "expect": {}}]
        }
        """,
        encoding="utf-8",
    )

    runner = EvalRunner(project_root=tmp_path, cases_dir=tmp_path / "cases")

    class FailingRuntime:
        def run_case(self, case: EvalCase) -> EvalCaseResult:
            raise AssertionError("runner should use the wrapped runtime result, not raise here")

    class WrappedFailingRuntime:
        def run_case(self, case: EvalCase) -> EvalCaseResult:
            return EvalCaseResult(
                case_id=case.id,
                description=case.description,
                ok=False,
                tags=list(case.tags),
                step_results=[],
                duration_seconds=0.1,
                source_path=str(case.source_path),
                failure_category="infrastructure_failure",
                error_message="ConnectError: boom",
            )

    runner.runtime = WrappedFailingRuntime()

    result = runner.run()

    assert result.failed_cases == 1
    assert result.case_results[0].failure_category == "infrastructure_failure"
    assert result.case_results[0].error_message == "ConnectError: boom"


def test_eval_runner_writes_html_report(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases" / "regression"
    cases_dir.mkdir(parents=True)
    (cases_dir / "sample.json").write_text(
        """
        {
          "id": "sample_case",
          "type": "regression",
          "steps": [{"input": {"text": "hello"}, "expect": {}}]
        }
        """,
        encoding="utf-8",
    )
    report_path = tmp_path / ".cora" / "evals" / "latest.json"

    class WrappedRuntime:
        def run_case(self, case: EvalCase) -> EvalCaseResult:
            return EvalCaseResult(
                case_id=case.id,
                description=case.description,
                ok=True,
                tags=list(case.tags),
                step_results=[],
                duration_seconds=0.1,
                source_path=str(case.source_path),
            )

    runner = EvalRunner(project_root=tmp_path, cases_dir=tmp_path / "cases", report_path=report_path)
    runner.runtime = WrappedRuntime()

    result = runner.run()
    html_path = html_report_path(report_path)
    html_text = html_path.read_text(encoding="utf-8")

    assert result.html_report_path == str(html_path)
    assert html_path.exists()
    assert "Cora Eval Report" in html_text
    assert "sample_case" in html_text


def test_run_result_to_html_includes_failure_details() -> None:
    run_result = EvalRunner(project_root=Path("."), cases_dir=Path(".")).run
    del run_result
    html_text = run_result_to_html(
        type(
            "StubRunResult",
            (),
            {
                "created_at": "2026-05-15T00:00:00+08:00",
                "project_root": "C:/repo",
                "cases_dir": "evals/cases",
                "total_cases": 1,
                "passed_cases": 0,
                "failed_cases": 1,
                "total_steps": 1,
                "passed_steps": 0,
                "failed_steps": 1,
                "report_path": ".cora/evals/latest.json",
                "html_report_path": ".cora/evals/latest.html",
                "case_results": [
                    type(
                        "StubCaseResult",
                        (),
                        {
                            "case_id": "deliver_missing_item_safe",
                            "description": "safe fail",
                            "ok": False,
                            "tags": ["safety"],
                            "duration_seconds": 1.2,
                            "source_path": "evals/cases/safety/deliver_missing_item_safe.json",
                            "failure_category": "assertion_failure",
                            "error_message": None,
                            "step_results": [
                                type(
                                    "StubStepResult",
                                    (),
                                    {
                                        "index": 1,
                                        "label": "deliver missing item",
                                        "ok": False,
                                        "tool_names": ["skill_run"],
                                        "failure_category": "assertion_failure",
                                        "failures": ["reply missing required text"],
                                        "response": {"reply": "foo"},
                                        "observed_state": type(
                                            "StubObservedState",
                                            (),
                                            {
                                                "item_count": 0,
                                                "deleted_item_count": 0,
                                                "pending_exists": False,
                                                "pending_kind": None,
                                                "user_memory_text": "# User Memory\n",
                                            },
                                        )(),
                                    },
                                )()
                            ],
                        },
                    )()
                ],
            },
        )()
    )

    assert "deliver_missing_item_safe" in html_text
    assert "assertion_failure" in html_text
    assert "reply missing required text" in html_text
    assert "deleted_item_count" in html_text


def test_tool_names_from_trace_strips_whitespace() -> None:
    tool_names = tool_names_from_trace(
        [
            {"role": "tool", "name": " skill_run"},
            {"role": "tool", "name": "skill_view "},
            {"role": "assistant", "name": "ignored"},
        ]
    )

    assert tool_names == ["skill_run", "skill_view"]


def _write_case(tmp_path: Path) -> Path:
    path = tmp_path / "sample.json"
    path.write_text(
        """
        {
          "id": "sample_case",
          "type": "regression",
          "description": "sample",
          "steps": [
            {
              "input": {"text": "hello"},
              "expect": {
                "status": "completed",
                "reply_contains_all": ["hi"]
              }
            }
          ]
        }
        """,
        encoding="utf-8",
    )
    return path
