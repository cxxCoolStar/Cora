from __future__ import annotations

import sqlite3

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from core.storage.models import Base


class DatabaseManager:
    def __init__(self, url: str) -> None:
        self.engine = create_engine(url, future=True)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, class_=Session)

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)
        self._run_sqlite_migrations()

    def session(self) -> Session:
        return self.session_factory()

    def _run_sqlite_migrations(self) -> None:
        if not self.engine.url.drivername.startswith("sqlite"):
            return
        with self.engine.begin() as connection:
            raw = self._unwrap_sqlite_connection(connection.connection)
            if raw is None:
                return
            self._ensure_column(raw, "clawbot_messages", "metadata_json", "JSON NOT NULL DEFAULT '{}'")
            self._ensure_column(raw, "clawbot_items", "document_key", "VARCHAR(255)")
            self._ensure_column(raw, "clawbot_items", "version", "INTEGER NOT NULL DEFAULT 1")
            self._ensure_column(raw, "clawbot_items", "is_current", "INTEGER NOT NULL DEFAULT 1")
            self._ensure_column(raw, "clawbot_items", "superseded_by_item_id", "VARCHAR")
            self._ensure_column(raw, "clawbot_items", "source_event_id", "VARCHAR")

    @staticmethod
    def _unwrap_sqlite_connection(raw: object) -> sqlite3.Connection | None:
        if isinstance(raw, sqlite3.Connection):
            return raw
        for attr in ("driver_connection", "connection", "_dbapi_connection"):
            candidate = getattr(raw, attr, None)
            if isinstance(candidate, sqlite3.Connection):
                return candidate
        return None

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, column_sql: str) -> None:
        cursor = conn.execute(f"PRAGMA table_info({table_name})")
        columns = {row[1] for row in cursor.fetchall()}
        if column_name in columns:
            return
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")
