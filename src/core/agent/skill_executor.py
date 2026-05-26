from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.agent.skill_protocol import SkillExecutionResult
from core.agent.skill_loader import SkillLoader


@dataclass(slots=True)
class SkillScriptRequest:
    skill_name: str
    script_path: str
    input_payload: dict[str, Any] = field(default_factory=dict)


class SkillScriptExecutor:
    def __init__(
        self,
        *,
        skill_loader: SkillLoader | None = None,
        python_executable: str | None = None,
    ) -> None:
        self.skill_loader = skill_loader or SkillLoader()
        self.python_executable = python_executable or sys.executable

    def run(self, request: SkillScriptRequest) -> SkillExecutionResult:
        viewed = self.skill_loader.view_skill(name=request.skill_name, file_path=request.script_path)
        if viewed is None:
            raise ValueError(f"Unknown skill: {request.skill_name}")
        if viewed.absolute_path.suffix.lower() != ".py":
            raise ValueError("skill_run only supports Python helper scripts.")
        if self._should_run_archive_dispatch_in_process(request=request, viewed=viewed):
            from core.skills.runner import run_archive_dispatch

            parsed = run_archive_dispatch(request.input_payload)
            return SkillExecutionResult.from_payload(parsed)
        payload = json.dumps(request.input_payload, ensure_ascii=False)
        completed = subprocess.run(
            [self.python_executable, str(viewed.absolute_path)],
            input=payload,
            capture_output=True,
            text=True,
            cwd=str(viewed.absolute_path.parent),
            check=False,
        )
        if completed.returncode != 0:
            error = (completed.stderr or completed.stdout or "").strip() or f"Skill script failed: {viewed.file_path}"
            raise ValueError(error)
        stdout = (completed.stdout or "").strip()
        if not stdout:
            raise ValueError("Skill script returned no JSON output.")
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Skill script returned invalid JSON: {viewed.file_path}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Skill script must return one JSON object.")
        return SkillExecutionResult.from_payload(parsed)

    @staticmethod
    def _should_run_archive_dispatch_in_process(*, request: SkillScriptRequest, viewed: Any) -> bool:
        if request.skill_name != "archive-core":
            return False
        normalized = str(request.script_path or "").replace("\\", "/")
        if not normalized.endswith("archive_dispatch.py"):
            return False
        return viewed.absolute_path.name == "archive_dispatch.py"
