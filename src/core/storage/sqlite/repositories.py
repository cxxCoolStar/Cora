from __future__ import annotations

import json

from core.schemas.common import new_id, utc_now
from core.schemas.event import RuntimeEvent
from core.schemas.memory import MemoryRecord
from core.schemas.message import Message
from core.schemas.session import Session
from core.storage.sqlite.database import SQLiteDatabase


class SessionRepository:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def create(self, *, agent_name: str, metadata: dict) -> Session:
        session = Session(agent_name=agent_name, metadata=metadata)
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO sessions (id, agent_name, status, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.agent_name,
                    session.status,
                    json.dumps(session.metadata),
                    session.created_at.isoformat(),
                    session.updated_at.isoformat(),
                ),
            )
        return session

    def get(self, session_id: str) -> Session:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            raise KeyError(f"Session not found: {session_id}")
        return Session(
            id=row["id"],
            agent_name=row["agent_name"],
            status=row["status"],
            metadata=json.loads(row["metadata_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class MessageRepository:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def append(self, message: Message) -> Message:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO messages (
                    id, session_id, role, channel, content, name, tool_call_id, metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.id,
                    message.session_id,
                    message.role,
                    message.channel,
                    message.content,
                    message.name,
                    message.tool_call_id,
                    json.dumps(message.metadata),
                    message.created_at.isoformat(),
                ),
            )
        return message

    def list_for_session(self, *, session_id: str, limit: int | None) -> list[Message]:
        sql = "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC"
        params: tuple = (session_id,)
        if limit is not None:
            sql = """
                SELECT * FROM (
                    SELECT * FROM messages
                    WHERE session_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                )
                ORDER BY created_at ASC
            """
            params = (session_id, limit)
        with self.database.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [
            Message(
                id=row["id"],
                session_id=row["session_id"],
                role=row["role"],
                channel=row["channel"],
                content=row["content"],
                name=row["name"],
                tool_call_id=row["tool_call_id"],
                metadata=json.loads(row["metadata_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]


class MemoryRepository:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def get_latest_by_type(self, *, session_id: str, memory_type: str) -> MemoryRecord | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM memories
                WHERE session_id = ? AND memory_type = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (session_id, memory_type),
            ).fetchone()
        if row is None:
            return None
        return MemoryRecord(
            id=row["id"],
            session_id=row["session_id"],
            memory_type=row["memory_type"],
            content=row["content"],
            metadata=json.loads(row["metadata_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def upsert_summary(self, *, session_id: str, content: str) -> MemoryRecord:
        existing = self.get_latest_by_type(session_id=session_id, memory_type="summary")
        timestamp = utc_now().isoformat()
        if existing is None:
            record = MemoryRecord(
                id=new_id(),
                session_id=session_id,
                memory_type="summary",
                content=content,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            with self.database.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO memories (
                        id, session_id, memory_type, content, metadata_json, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.id,
                        record.session_id,
                        record.memory_type,
                        record.content,
                        json.dumps(record.metadata),
                        record.created_at.isoformat(),
                        record.updated_at.isoformat(),
                    ),
                )
            return record

        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE memories
                SET content = ?, updated_at = ?
                WHERE id = ?
                """,
                (content, timestamp, existing.id),
            )
        existing.content = content
        return existing


class EventRepository:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def append(self, *, session_id: str, event_type: str, channel: str, payload: dict) -> RuntimeEvent:
        event = RuntimeEvent(
            session_id=session_id,
            event_type=event_type,
            channel=channel,
            payload=payload,
        )
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO events (id, session_id, event_type, channel, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.session_id,
                    event.event_type,
                    event.channel,
                    json.dumps(event.payload),
                    event.created_at.isoformat(),
                ),
            )
        return event
