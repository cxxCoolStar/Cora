from __future__ import annotations

import json
from pathlib import Path

from archive_core.models import ArchiveRequest
from archive_core.runtime import ArchiveRuntime
from archive_core.store.file_store import FileArchiveStore


def test_save_search_deliver_flow(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    source = tmp_path / "resume.pdf"
    source.write_bytes(b"%PDF-1.4 resume")

    store = FileArchiveStore(archive_root)
    runtime = ArchiveRuntime(store)

    saved = runtime.run(
        ArchiveRequest.from_dict(
            {
                "intent": "save",
                "upload": {"path": str(source)},
                "arguments": {
                    "topic": "documents",
                    "type": "document",
                    "summary": "my resume",
                    "description": "resume pdf for job applications",
                },
            }
        )
    )
    assert saved.status == "completed"
    record_id = saved.artifacts[0].ref

    found = runtime.run(
        ArchiveRequest.from_dict(
            {
                "intent": "search",
                "arguments": {"query": "resume"},
            }
        )
    )
    assert "resume" in found.message.lower() or "resume" in found.message

    deliver = runtime.run(
        ArchiveRequest.from_dict(
            {
                "intent": "deliver",
                "arguments": {"record_id": record_id},
            }
        )
    )
    assert deliver.actions[0].type == "deliver_file"
    assert Path(deliver.actions[0].payload["path"]).is_file()


def test_archive_cli_stdin(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ARCHIVE_ROOT", str(tmp_path / "archive"))
    from archive_core.cli import run_request

    source = tmp_path / "note.txt"
    source.write_text("hello portable archive", encoding="utf-8")
    result = run_request(
        {
            "intent": "save",
            "upload": {"path": str(source)},
            "arguments": {"topic": "inbox", "type": "text", "summary": "hello"},
        }
    )
    assert result.status == "completed"

    listed = run_request({"intent": "list_topics"})
    assert "inbox" in listed.message
