from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


SANDBOX_WORKSPACE_DIRNAME = "workspace"
MUTATING_TOOLS_IN_SANDBOX = frozenset({"write_file", "shell_exec"})


@dataclass(frozen=True, slots=True)
class SandboxContext:
    run_id: str
    sandbox_root: Path
    workspace_root: Path

    def to_dict(self) -> dict[str, str]:
        return {
            "run_id": self.run_id,
            "sandbox_root": self.sandbox_root.as_posix(),
            "workspace_root": self.workspace_root.as_posix(),
        }


class SandboxWorkspaceManager:
    def __init__(self, *, base_dir: Path) -> None:
        self.base_dir = base_dir.resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_cora_home(cls, cora_home: Path) -> "SandboxWorkspaceManager":
        return cls(base_dir=cora_home / "sandboxes")

    def ensure(self, *, run_id: str) -> SandboxContext:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            raise ValueError("run_id is required for sandbox workspace")
        sandbox_root = (self.base_dir / normalized_run_id).resolve()
        workspace_root = (sandbox_root / SANDBOX_WORKSPACE_DIRNAME).resolve()
        workspace_root.mkdir(parents=True, exist_ok=True)
        return SandboxContext(
            run_id=normalized_run_id,
            sandbox_root=sandbox_root,
            workspace_root=workspace_root,
        )

    def cleanup(self, *, run_id: str) -> None:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            return
        target = self.base_dir / normalized_run_id
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)


__all__ = [
    "MUTATING_TOOLS_IN_SANDBOX",
    "SANDBOX_WORKSPACE_DIRNAME",
    "SandboxContext",
    "SandboxWorkspaceManager",
]
