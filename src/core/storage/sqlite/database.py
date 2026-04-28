from __future__ import annotations

import sqlite3
from pathlib import Path


class SQLiteDatabase:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    agent_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    content TEXT NOT NULL,
                    name TEXT,
                    tool_call_id TEXT,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                );
                CREATE INDEX IF NOT EXISTS idx_messages_session_created
                    ON messages(session_id, created_at);

                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    memory_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                );
                CREATE INDEX IF NOT EXISTS idx_memories_session_type
                    ON memories(session_id, memory_type, updated_at);

                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                );
                CREATE INDEX IF NOT EXISTS idx_events_session_created
                    ON events(session_id, created_at);
                """
            )
