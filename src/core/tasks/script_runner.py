from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from core.agent.skill_protocol import SkillExecutionResult


class ScheduledTaskScriptRunner:
    def __init__(
        self,
        *,
        script_root: Path,
        python_executable: str | None = None,
    ) -> None:
        self.script_root = script_root.resolve()
        self.python_executable = python_executable or sys.executable

    def run(self, *, script_path: str, input_payload: dict[str, Any]) -> SkillExecutionResult:
        path = self._resolve_script(script_path)
        payload = json.dumps(input_payload, ensure_ascii=False)
        completed = subprocess.run(
            [self.python_executable, str(path)],
            input=payload,
            capture_output=True,
            text=True,
            cwd=str(path.parent),
            check=False,
        )
        if completed.returncode != 0:
            error = (completed.stderr or completed.stdout or "").strip() or f"Scheduled task script failed: {path.name}"
            raise ValueError(error)
        stdout = (completed.stdout or "").strip()
        if not stdout:
            return SkillExecutionResult(
                message="",
                action="execute",
                status="completed",
                disposition="respond",
            )
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            return SkillExecutionResult(
                message=stdout,
                action="execute",
                status="completed",
                disposition="respond",
                raw_payload={"message": stdout},
            )
        if not isinstance(parsed, dict):
            raise ValueError("Scheduled task script must print a JSON object or plain text.")
        if parsed.get("wakeAgent") is False and not parsed.get("message"):
            parsed = dict(parsed)
            parsed["message"] = ""
            parsed.setdefault("status", "completed")
            parsed.setdefault("action", "execute")
        return SkillExecutionResult.from_payload(parsed)

    def _resolve_script(self, script_path: str) -> Path:
        raw_path = Path(str(script_path or "").strip())
        if not str(raw_path):
            raise ValueError("Scheduled task script_path is required.")
        candidate = raw_path if raw_path.is_absolute() else (self.script_root / raw_path)
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self.script_root)
        except ValueError as exc:
            raise ValueError("Scheduled task script_path must stay within the workspace root.") from exc
        if not resolved.exists() or not resolved.is_file():
            raise ValueError(f"Scheduled task script not found: {script_path}")
        if resolved.suffix.lower() != ".py":
            raise ValueError("Scheduled task scripts currently support Python files only.")
        return resolved

