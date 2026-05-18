from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from core.clawbot.schemas import TurnResponse
from core.evals.judge import evaluate_step
from core.evals.loader import load_cases
from core.evals.models import (
    EvalCase,
    EvalRunResult,
    EvalStep,
    EvalStepResult,
)
from core.evals.report import write_report
from core.evals.runtime import EvalRuntime


class EvalRunner:
    def __init__(
        self,
        *,
        project_root: Path,
        cases_dir: Path,
        report_path: Path | None = None,
        case_type: str | None = None,
    ) -> None:
        self.project_root = project_root
        self.cases_dir = cases_dir
        self.report_path = report_path
        self.case_type = case_type
        self.runtime = EvalRuntime(project_root=project_root)

    def run(self) -> EvalRunResult:
        cases = self.load_cases()
        case_results = [self.runtime.run_case(case) for case in cases]
        passed_cases = sum(1 for case in case_results if case.ok)
        total_steps = sum(len(case.step_results) for case in case_results)
        passed_steps = sum(1 for case in case_results for step in case.step_results if step.ok)
        run_result = EvalRunResult(
            created_at=datetime.now(UTC).isoformat(),
            project_root=str(self.project_root),
            cases_dir=str(self.cases_dir),
            total_cases=len(case_results),
            passed_cases=passed_cases,
            failed_cases=len(case_results) - passed_cases,
            total_steps=total_steps,
            passed_steps=passed_steps,
            failed_steps=total_steps - passed_steps,
            case_results=case_results,
            report_path=str(self.report_path) if self.report_path is not None else None,
            html_report_path=(
                str(self.report_path.with_suffix(".html"))
                if self.report_path is not None
                else None
            ),
        )
        if self.report_path is not None:
            write_report(self.report_path, run_result)
        return run_result

    def load_cases(self) -> list[EvalCase]:
        return load_cases(self.cases_dir, case_type=self.case_type)

    def evaluate_step(
        self,
        *,
        case: EvalCase,
        step: EvalStep,
        index: int,
        response: TurnResponse,
        observed_state=None,
    ) -> EvalStepResult:
        return evaluate_step(case=case, step=step, index=index, response=response, observed_state=observed_state)
