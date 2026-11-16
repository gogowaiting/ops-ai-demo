"""写操作审批审计：记录谁在何时批准或拒绝了什么。"""

from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from src.database import DatabaseConfig

DEFAULT_AUDIT_DB = "data/approvals.sqlite"
VALID_DECISIONS = {"approved", "rejected", "auto_approved", "blocked"}


class ApprovalAuditStore:
    """审批审计存储，支持 SQLite / PostgreSQL。"""

    def __init__(self, db_path: str | None = None):
        self.db = (
            DatabaseConfig.sqlite(db_path)
            if db_path is not None
            else DatabaseConfig.resolve(
                DEFAULT_AUDIT_DB,
                "APPROVAL_AUDIT_DB",
            )
        )
        self._lock = threading.Lock()
        self._initialize()

    @contextmanager
    def _connection(self):
        with self.db.connect() as conn:
            yield conn

    def _initialize(self) -> None:
        with self._lock, self._connection() as conn:
            ddl = """CREATE TABLE IF NOT EXISTS approval_events (
                id {id_type},
                event_id TEXT NOT NULL UNIQUE,
                thread_id TEXT NOT NULL DEFAULT '',
                alert_id TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL,
                args_json TEXT NOT NULL,
                decision TEXT NOT NULL,
                approved {approved_type} NOT NULL,
                actor TEXT NOT NULL,
                actor_source TEXT NOT NULL,
                source TEXT NOT NULL,
                trace_id TEXT NOT NULL DEFAULT '',
                skills_json TEXT NOT NULL DEFAULT '[]',
                decided_at {timestamp_type} NOT NULL
            )"""
            if self.db.backend == "postgres":
                id_type = "BIGSERIAL PRIMARY KEY"
                approved_type = "BOOLEAN"
                timestamp_type = "TIMESTAMPTZ"
            else:
                id_type = "INTEGER PRIMARY KEY AUTOINCREMENT"
                approved_type = "INTEGER"
                timestamp_type = "TEXT"
            conn.execute(ddl.format(id_type=id_type, approved_type=approved_type, timestamp_type=timestamp_type))

            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_approval_events_thread "
                "ON approval_events(thread_id, id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_approval_events_alert "
                "ON approval_events(alert_id, id)"
            )

            if self.db.backend == "sqlite":
                columns = {
                    row["name"]
                    for row in conn.execute(
                        "PRAGMA table_info(approval_events)"
                    ).fetchall()
                }
                if "trace_id" not in columns:
                    conn.execute(
                        "ALTER TABLE approval_events "
                        "ADD COLUMN trace_id TEXT NOT NULL DEFAULT ''"
                    )
                if "skills_json" not in columns:
                    conn.execute(
                        "ALTER TABLE approval_events "
                        "ADD COLUMN skills_json TEXT NOT NULL DEFAULT '[]'"
                    )

    def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        with self.db.connect() as conn:
            cursor = conn.execute(self.db.parameterize(sql), params)
            return cursor.rowcount

    def _fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(self.db.parameterize(sql), params).fetchall()
        return [dict(row) for row in rows]

    def record(
        self,
        *,
        action: str,
        args: dict[str, Any],
        decision: str,
        approved: bool,
        actor: str,
        actor_source: str,
        source: str,
        trace_id: str = "",
        thread_id: str = "",
        alert_id: str = "",
        skills: list[dict[str, Any]] | None = None,
        event_id: str = "",
    ) -> dict[str, Any]:
        if decision not in VALID_DECISIONS:
            raise ValueError(f"未知审批结果: {decision}")

        normalized_actor = actor.strip()
        if not normalized_actor:
            raise ValueError("审批人不能为空")
        normalized_action = action.strip()
        if not normalized_action:
            raise ValueError("审批动作不能为空")

        record = {
            "event_id": event_id or uuid4().hex,
            "thread_id": thread_id.strip(),
            "alert_id": alert_id.strip(),
            "action": normalized_action,
            "args": args,
            "decision": decision,
            "approved": bool(approved),
            "actor": normalized_actor,
            "actor_source": actor_source.strip() or "unknown",
            "source": source.strip() or "unknown",
            "trace_id": trace_id.strip(),
            "skills": skills or [],
            "decided_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        args_json = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
        skills_json = json.dumps(
            record["skills"], ensure_ascii=False, sort_keys=True, default=str
        )

        with self._lock:
            self._execute(
                """
                INSERT INTO approval_events (
                    event_id, thread_id, alert_id, action, args_json, decision,
                    approved, actor, actor_source, source, trace_id,
                    skills_json, decided_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["event_id"],
                    record["thread_id"],
                    record["alert_id"],
                    record["action"],
                    args_json,
                    record["decision"],
                    record["approved"],
                    record["actor"],
                    record["actor_source"],
                    record["source"],
                    record["trace_id"],
                    skills_json,
                    record["decided_at"],
                ),
            )
        return record

    def list(self, limit: int = 50, thread_id: str = "") -> list[dict[str, Any]]:
        normalized_limit = max(1, min(int(limit), 500))
        sql = "SELECT * FROM approval_events"
        params: tuple[Any, ...] = ()
        if thread_id:
            sql += " WHERE thread_id = ?"
            params = (thread_id,)
        sql += " ORDER BY id DESC LIMIT ?"
        params += (normalized_limit,)

        results = []
        for item in self._fetchall(sql, params):
            item["args"] = json.loads(item.pop("args_json"))
            item["skills"] = json.loads(item.pop("skills_json", "[]"))
            item["approved"] = bool(item["approved"])
            if hasattr(item["decided_at"], "isoformat"):
                item["decided_at"] = item["decided_at"].isoformat()
            results.append(item)
        return results


_default_store: ApprovalAuditStore | None = None
_default_lock = threading.Lock()


def get_approval_audit() -> ApprovalAuditStore:
    global _default_store
    if _default_store is None:
        with _default_lock:
            if _default_store is None:
                _default_store = ApprovalAuditStore(
                    os.getenv("APPROVAL_AUDIT_DB", DEFAULT_AUDIT_DB)
                )
    return _default_store
