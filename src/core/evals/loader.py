from __future__ import annotations

import json
from pathlib import Path

from core.evals.models import EvalCase, EvalSetup, EvalStep, maybe_str, string_list


def load_cases(cases_dir: Path, *, case_type: str | None = None) -> list[EvalCase]:
    if not cases_dir.exists():
        raise FileNotFoundError(f"Eval cases directory not found: {cases_dir}")
    normalized_type = maybe_str(case_type)
    cases = [
        load_case(path)
        for path in sorted(cases_dir.rglob("*.json"))
        if normalized_type is None or infer_case_type(path=path, cases_dir=cases_dir) == normalized_type
    ]
    if not cases:
        scope = f" for type `{normalized_type}`" if normalized_type else ""
        raise FileNotFoundError(f"No eval case files found in: {cases_dir}{scope}")
    return cases


def load_case(path: Path) -> EvalCase:
    payload = json.loads(path.read_text(encoding="utf-8"))
    case_id = str(payload.get("id") or "").strip()
    if not case_id:
        raise ValueError(f"{path} is missing required field `id`.")
    description = str(payload.get("description") or case_id).strip()
    steps = [EvalStep.from_dict(item) for item in list(payload.get("steps") or [])]
    if not steps:
        raise ValueError(f"{path} must define at least one eval step.")
    path_case_type = infer_case_type(path=path)
    return EvalCase(
        id=case_id,
        case_type=maybe_str(payload.get("type")) or path_case_type,
        description=description,
        tags=string_list(payload.get("tags")),
        setup=EvalSetup.from_dict(payload.get("setup")),
        steps=steps,
        source_path=path,
    )


def infer_case_type(*, path: Path, cases_dir: Path | None = None) -> str:
    if cases_dir is not None:
        try:
            relative = path.relative_to(cases_dir)
            if len(relative.parts) > 1:
                return relative.parts[0]
        except ValueError:
            pass
    parent = path.parent.name.strip()
    return parent or "uncategorized"
