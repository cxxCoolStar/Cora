from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SKILL_ROOT = Path(__file__).resolve().parents[1] / "skills" / "archive-core"
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from archive_core.models import ArchiveRequest  # noqa: E402
from archive_core.runtime import ArchiveRuntime  # noqa: E402
from archive_core.store.file_store import FileArchiveStore  # noqa: E402

from core.archive.portable_bridge import (  # noqa: E402
    archive_result_to_skill_payload,
    run_portable_archive,
)


def test_portable_bridge_maps_deliver_effect(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    source = tmp_path / "doc.txt"
    source.write_text("portable bridge", encoding="utf-8")

    raw = run_portable_archive(
        {
            "intent": "save",
            "upload": {"path": str(source)},
            "arguments": {"topic": "inbox", "type": "text", "summary": "bridge"},
        },
        archive_root=archive_root,
    )
    record_id = raw["artifacts"][0]["ref"]

    deliver = run_portable_archive(
        {"intent": "deliver", "arguments": {"record_id": record_id}},
        archive_root=archive_root,
    )
    skill_payload = archive_result_to_skill_payload(deliver)
    assert any(effect.get("kind") == "deliver_file" for effect in skill_payload["effects"])


def test_runtime_search_multiple_candidates(tmp_path: Path) -> None:
    store = FileArchiveStore(tmp_path / "archive")
    runtime = ArchiveRuntime(store)
    for name, summary in [("a.txt", "alpha config"), ("b.txt", "beta config")]:
        path = tmp_path / name
        path.write_text(summary, encoding="utf-8")
        runtime.run(
            ArchiveRequest.from_dict(
                {
                    "intent": "save",
                    "upload": {"path": str(path)},
                    "arguments": {"topic": "configs", "type": "text", "summary": summary},
                }
            )
        )
    result = runtime.run(
        ArchiveRequest.from_dict(
            {"intent": "search", "arguments": {"query": "config"}}
        )
    )
    assert result.disposition == "clarify"
    assert result.pending is not None
