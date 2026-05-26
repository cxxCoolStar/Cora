from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SCHEMA_VERSION = "1.0"

IntentName = Literal[
    "save",
    "search",
    "read",
    "deliver",
    "delete",
    "overview",
    "list_topics",
]

Disposition = Literal["respond", "clarify"]
Status = Literal["completed", "failed"]


@dataclass(slots=True)
class ArchiveRecord:
    id: str
    type: str
    topic: str
    path: str
    filename: str
    summary: str = ""
    description: str = ""
    source: str = "unknown"
    user_note: str = ""
    created_at: str = ""
    deleted: bool = False

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ArchiveRecord":
        return cls(
            id=str(payload.get("id") or "").strip(),
            type=str(payload.get("type") or "asset").strip(),
            topic=str(payload.get("topic") or "").strip(),
            path=str(payload.get("path") or "").strip(),
            filename=str(payload.get("filename") or "").strip(),
            summary=str(payload.get("summary") or "").strip(),
            description=str(payload.get("description") or "").strip(),
            source=str(payload.get("source") or "unknown").strip() or "unknown",
            user_note=str(payload.get("user_note") or "").strip(),
            created_at=str(payload.get("created_at") or "").strip(),
            deleted=bool(payload.get("deleted")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "topic": self.topic,
            "path": self.path,
            "filename": self.filename,
            "summary": self.summary,
            "description": self.description,
            "source": self.source,
            "user_note": self.user_note,
            "created_at": self.created_at,
            "deleted": self.deleted,
        }


@dataclass(slots=True)
class ScoredRecord:
    record: ArchiveRecord
    score: int
    resolved_path: str = ""
    file_exists: bool = False

    def to_summary(self) -> dict[str, Any]:
        payload = {
            "id": self.record.id,
            "type": self.record.type,
            "topic": self.record.topic,
            "path": self.record.path,
            "filename": self.record.filename,
            "summary": self.record.summary,
            "description": self.record.description,
            "source": self.record.source,
            "created_at": self.record.created_at,
            "score": self.score,
            "file_exists": self.file_exists,
            "resolved_path": self.resolved_path,
        }
        return payload


@dataclass(slots=True)
class ArchiveAction:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "payload": dict(self.payload)}


@dataclass(slots=True)
class ArchiveArtifact:
    kind: str
    ref: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "ref": self.ref, "payload": dict(self.payload)}


@dataclass(slots=True)
class ArchiveRequest:
    intent: IntentName
    arguments: dict[str, Any] = field(default_factory=dict)
    session: dict[str, Any] = field(default_factory=dict)
    upload: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ArchiveRequest":
        intent = str(payload.get("intent") or "").strip().lower()
        if intent not in {
            "save",
            "search",
            "read",
            "deliver",
            "delete",
            "overview",
            "list_topics",
        }:
            raise ValueError(f"unsupported intent: {intent}")
        return cls(
            intent=intent,  # type: ignore[arg-type]
            arguments=dict(payload.get("arguments") or {}),
            session=dict(payload.get("session") or {}),
            upload=dict(payload.get("upload") or {}),
            context=dict(payload.get("context") or {}),
            schema_version=str(payload.get("schema_version") or SCHEMA_VERSION),
        )


@dataclass(slots=True)
class ArchiveResult:
    message: str
    status: Status = "completed"
    disposition: Disposition = "respond"
    artifacts: list[ArchiveArtifact] = field(default_factory=list)
    actions: list[ArchiveAction] = field(default_factory=list)
    pending: dict[str, Any] | None = None
    schema_version: str = SCHEMA_VERSION
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "disposition": self.disposition,
            "message": self.message,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "actions": [action.to_dict() for action in self.actions],
            "pending": dict(self.pending) if self.pending else None,
            "raw": dict(self.raw),
        }
