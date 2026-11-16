"""诊断轨迹存储，支持 SQLite / PostgreSQL。"""

from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from src.database import DatabaseConfig

DEFAULT_TRACE_DB = "data/traces.sqlite"


class DiagnosticTraceStore:
    def __init__(self, db_path: str | None = None):
        self.db = (
            DatabaseConfig.sqlite(db_path)
            if db_path is not None
            else DatabaseConfig.resolve(
                DEFAULT_TRACE_DB,
                "DIAGNOSTIC_TRACE_DB",
            )
        )
        self._lock = threading.Lock()
        self._initialize()

    def _initialize(self) -> None:
        with self._lock, self._connection() as conn:
            trace_ddl = """CREATE TABLE IF NOT EXISTS diagnostic_traces (
                id {id_type},
                trace_id TEXT NOT NULL,
                thread_id TEXT NOT NULL DEFAULT '',
                alert_id TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL,
                input_text TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at {timestamp_type} NOT NULL,
                finished_at {nullable_timestamp_type}
            )"""
            event_ddl = """CREATE TABLE IF NOT EXISTS diagnostic_events (
                id {id_type},
                event_id TEXT NOT NULL UNIQUE,
                trace_id TEXT NOT NULL,
                node TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{{}}',
                created_at {timestamp_type} NOT NULL
            )"""
            if self.db.backend == "postgres":
                timestamp_type = "TIMESTAMPTZ"
                id_type = "BIGSERIAL PRIMARY KEY"
            else:
                timestamp_type = "TEXT"
                id_type = "INTEGER PRIMARY KEY AUTOINCREMENT"
            conn.execute(
                trace_ddl.format(
                    id_type=id_type,
                    timestamp_type=timestamp_type,
                    nullable_timestamp_type=timestamp_type,
                )
            )
            conn.execute(
                event_ddl.format(
                    id_type=id_type,
                    timestamp_type=timestamp_type,
                )
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_diagnostic_traces_trace "
                "ON diagnostic_traces(trace_id, id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_diagnostic_events_trace "
                "ON diagnostic_events(trace_id, id)"
            )

    @contextmanager
    def _connection(self):
        with self.db.connect() as conn:
            yield conn

    def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        with self.db.connect() as conn:
            cursor = conn.execute(self.db.parameterize(sql), params)
            return cursor.rowcount

    def _fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(self.db.parameterize(sql), params).fetchone()
        return dict(row) if row is not None else None

    def _fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(self.db.parameterize(sql), params).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat().replace("+00:00", "Z")

    def start(
        self,
        *,
        trace_id: str,
        source: str,
        input_text: str,
        thread_id: str = "",
        alert_id: str = "",
    ) -> None:
        self._execute(
            """
            INSERT INTO diagnostic_traces (
                trace_id, thread_id, alert_id, source, input_text,
                status, started_at
            ) VALUES (?, ?, ?, ?, ?, 'running', ?)
            """,
            (
                trace_id,
                thread_id,
                alert_id,
                source,
                input_text,
                self._now(),
            ),
        )

    def record(
        self,
        *,
        trace_id: str,
        node: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        event_id: str = "",
    ) -> None:
        self._execute(
            """
            INSERT INTO diagnostic_events (
                event_id, trace_id, node, content, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event_id or uuid4().hex,
                trace_id,
                node,
                content,
                json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
                self._now(),
            ),
        )

    def finish(self, trace_id: str, status: str = "completed") -> None:
        self._execute(
            """
            UPDATE diagnostic_traces
            SET status = ?, finished_at = ?
            WHERE trace_id = ?
            """,
            (status, self._now(), trace_id),
        )

    def get(self, trace_id: str) -> dict[str, Any] | None:
        trace = self._fetchone(
            """
            SELECT * FROM diagnostic_traces
            WHERE trace_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (trace_id,),
        )
        if trace is None:
            return None
        events = self._fetchall(
            "SELECT * FROM diagnostic_events WHERE trace_id = ? ORDER BY id",
            (trace_id,),
        )
        for event in events:
            event["metadata"] = json.loads(event.pop("metadata_json"))
        trace["events"] = events
        return trace

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        normalized_limit = max(1, min(int(limit), 500))
        return self._fetchall(
            "SELECT * FROM diagnostic_traces ORDER BY id DESC LIMIT ?",
            (normalized_limit,),
        )


_default_store: DiagnosticTraceStore | None = None
_default_lock = threading.Lock()


def get_diagnostic_traces() -> DiagnosticTraceStore:
    global _default_store
    if _default_store is None:
        with _default_lock:
            if _default_store is None:
                _default_store = DiagnosticTraceStore(
                    os.getenv("DIAGNOSTIC_TRACE_DB", DEFAULT_TRACE_DB)
                )
    return _default_store
