"""持久化告警诊断任务。"""

from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from src.database import DatabaseConfig

DEFAULT_ALERT_JOB_DB = "data/alert_jobs.sqlite"


class AlertJobStore:
    """告警任务存储，支持 SQLite / PostgreSQL。"""

    def __init__(self, db_path: str | None = None):
        self.db = (
            DatabaseConfig.sqlite(db_path)
            if db_path is not None
            else DatabaseConfig.resolve(
                DEFAULT_ALERT_JOB_DB,
                "ALERT_JOB_DB",
            )
        )
        self._lock = threading.Lock()
        self._initialize()

    def _initialize(self) -> None:
        with self._lock, self._connection() as conn:
            ddl = """CREATE TABLE IF NOT EXISTS alert_jobs (
                job_id TEXT PRIMARY KEY,
                alert_id TEXT NOT NULL DEFAULT '',
                trace_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                alert_json TEXT NOT NULL,
                steps_json TEXT NOT NULL DEFAULT '[]',
                error TEXT NOT NULL DEFAULT '',
                created_at {timestamp_type} NOT NULL,
                started_at {nullable_timestamp_type},
                finished_at {nullable_timestamp_type}
            )"""
            if self.db.backend == "postgres":
                conn.execute(
                    ddl.format(
                        timestamp_type="TIMESTAMPTZ",
                        nullable_timestamp_type="TIMESTAMPTZ",
                    )
                )
            else:
                conn.execute(
                    ddl.format(
                        timestamp_type="TEXT",
                        nullable_timestamp_type="TEXT",
                    )
                )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_alert_jobs_status "
                "ON alert_jobs(status, created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_alert_jobs_alert "
                "ON alert_jobs(alert_id, created_at)"
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

    def enqueue(
        self,
        alert: dict[str, Any],
        *,
        trace_id: str = "",
        job_id: str = "",
    ) -> dict[str, Any]:
        record = {
            "job_id": job_id or uuid4().hex,
            "alert_id": str(alert.get("alert_id", "")),
            "trace_id": trace_id,
            "status": "queued",
            "alert": alert,
            "steps": [],
            "error": "",
            "created_at": self._now(),
            "started_at": None,
            "finished_at": None,
        }
        self._execute(
            """
            INSERT INTO alert_jobs (
                job_id, alert_id, trace_id, status, alert_json,
                steps_json, error, created_at, started_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["job_id"],
                record["alert_id"],
                record["trace_id"],
                record["status"],
                json.dumps(alert, ensure_ascii=False, sort_keys=True),
                "[]",
                "",
                record["created_at"],
                None,
                None,
            ),
        )
        return record

    def mark_started(self, job_id: str) -> bool:
        return self._execute(
            """
            UPDATE alert_jobs
            SET status = 'running', started_at = ?
            WHERE job_id = ? AND status = 'queued'
            """,
            (self._now(), job_id),
        ) == 1

    def complete(
        self,
        job_id: str,
        *,
        steps: list[str],
    ) -> bool:
        return self._execute(
            """
            UPDATE alert_jobs
            SET status = 'completed', steps_json = ?, finished_at = ?
            WHERE job_id = ?
            """,
            (
                json.dumps(steps, ensure_ascii=False),
                self._now(),
                job_id,
            ),
        ) == 1

    def fail(self, job_id: str, error: str) -> bool:
        return self._execute(
            """
            UPDATE alert_jobs
            SET status = 'failed', error = ?, finished_at = ?
            WHERE job_id = ?
            """,
            (error, self._now(), job_id),
        ) == 1

    def get(self, job_id: str) -> dict[str, Any] | None:
        row = self._fetchone("SELECT * FROM alert_jobs WHERE job_id = ?", (job_id,))
        return self._from_row(row) if row is not None else None

    def list(
        self,
        *,
        status: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        normalized_limit = max(1, min(int(limit), 500))
        sql = "SELECT * FROM alert_jobs"
        params: tuple[Any, ...] = ()
        if status:
            sql += " WHERE status = ?"
            params = (status,)
        sql += " ORDER BY created_at DESC, job_id LIMIT ?"
        params += (normalized_limit,)
        return [self._from_row(row) for row in self._fetchall(sql, params)]

    @staticmethod
    def _from_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["alert"] = json.loads(result.pop("alert_json"))
        result["steps"] = json.loads(result.pop("steps_json"))
        return result


_default_store: AlertJobStore | None = None
_default_lock = threading.Lock()


def get_alert_jobs() -> AlertJobStore:
    global _default_store
    if _default_store is None:
        with _default_lock:
            if _default_store is None:
                _default_store = AlertJobStore(
                    os.getenv("ALERT_JOB_DB", DEFAULT_ALERT_JOB_DB)
                )
    return _default_store
