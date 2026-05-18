from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import time
from typing import Any


DEFAULT_TIMEOUT_SECONDS = 20
MAX_TIMEOUT_SECONDS = 120
MAX_COMMAND_CHARS = 4000
MAX_OUTPUT_CHARS = 8000


@dataclass(slots=True)
class TerminalCommandResult:
    command: str
    cwd: str
    status: str
    duration_seconds: float
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False

    def render(self) -> str:
        if self.timed_out:
            lines = [
                f"Command timed out after {self.duration_seconds:.2f}s.",
                f"Working directory: `{self.cwd}`",
                f"Command: `{self.command}`",
            ]
        else:
            lines = [
                f"Command exited with code {self.exit_code} in {self.duration_seconds:.2f}s.",
                f"Working directory: `{self.cwd}`",
                f"Command: `{self.command}`",
            ]
        if self.stdout:
            lines.extend(["", "stdout:", self.stdout])
            if self.stdout_truncated:
                lines.append("[stdout truncated]")
        if self.stderr:
            lines.extend(["", "stderr:", self.stderr])
            if self.stderr_truncated:
                lines.append("[stderr truncated]")
        if not self.stdout and not self.stderr:
            lines.extend(["", "No output."])
        return "\n".join(lines)

    def metadata(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "cwd": self.cwd,
            "exit_code": self.exit_code,
            "duration_seconds": round(self.duration_seconds, 3),
            "timed_out": self.timed_out,
            "stdout_truncated": self.stdout_truncated,
            "stderr_truncated": self.stderr_truncated,
        }


@dataclass(slots=True)
class TerminalToolStore:
    root: Path

    def run_command(
        self,
        *,
        command: str,
        cwd: str = ".",
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> TerminalCommandResult:
        cleaned_command = str(command or "").strip()
        if not cleaned_command:
            raise ValueError("command cannot be empty")
        if len(cleaned_command) > MAX_COMMAND_CHARS:
            raise ValueError(f"command is too long; limit is {MAX_COMMAND_CHARS} characters")

        timeout = int(timeout_seconds or DEFAULT_TIMEOUT_SECONDS)
        if timeout < 1 or timeout > MAX_TIMEOUT_SECONDS:
            raise ValueError(f"timeout_seconds must be between 1 and {MAX_TIMEOUT_SECONDS}")

        target_cwd = self._resolve_cwd(cwd)
        rendered_cwd = target_cwd.relative_to(self._resolved_root()).as_posix() or "."
        started = time.monotonic()
        try:
            completed = subprocess.run(
                cleaned_command,
                cwd=target_cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                shell=True,
            )
        except subprocess.TimeoutExpired as exc:
            duration_seconds = time.monotonic() - started
            stdout, stdout_truncated = self._truncate_output(self._coerce_output(exc.stdout))
            stderr, stderr_truncated = self._truncate_output(self._coerce_output(exc.stderr))
            return TerminalCommandResult(
                command=cleaned_command,
                cwd=rendered_cwd,
                status="failed",
                duration_seconds=duration_seconds,
                stdout=stdout,
                stderr=stderr,
                timed_out=True,
                stdout_truncated=stdout_truncated,
                stderr_truncated=stderr_truncated,
            )

        duration_seconds = time.monotonic() - started
        stdout, stdout_truncated = self._truncate_output(completed.stdout or "")
        stderr, stderr_truncated = self._truncate_output(completed.stderr or "")
        return TerminalCommandResult(
            command=cleaned_command,
            cwd=rendered_cwd,
            status="completed" if completed.returncode == 0 else "failed",
            duration_seconds=duration_seconds,
            exit_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )

    def _resolve_cwd(self, cwd: str) -> Path:
        cleaned = str(cwd or ".").strip() or "."
        root = self._resolved_root()
        candidate = Path(cleaned)
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = candidate.resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("cwd escapes the allowed workspace root") from exc
        if not candidate.exists():
            raise ValueError(f"cwd does not exist: {cleaned}")
        if not candidate.is_dir():
            raise ValueError(f"cwd is not a directory: {cleaned}")
        return candidate

    def _resolved_root(self) -> Path:
        return self.root.resolve()

    @staticmethod
    def _coerce_output(value: str | bytes | None) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value

    @staticmethod
    def _truncate_output(text: str) -> tuple[str, bool]:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if len(normalized) <= MAX_OUTPUT_CHARS:
            return normalized, False
        return normalized[: MAX_OUTPUT_CHARS - 3] + "...", True
