"""共享数据库连接配置：SQLite / PostgreSQL。"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal


DatabaseBackend = Literal["sqlite", "postgres"]


@dataclass(frozen=True)
class DatabaseConfig:
    backend: DatabaseBackend
    path: str = ""
    url: str = ""

    @classmethod
    def sqlite(cls, path: str) -> "DatabaseConfig":
        return cls(backend="sqlite", path=path)

    @classmethod
    def resolve(cls, default_sqlite_path: str, path_env: str = "") -> "DatabaseConfig":
        backend = os.getenv("OPS_DB_BACKEND", "sqlite").strip().lower()
        if backend == "postgres":
            url = os.getenv("OPS_DB_URL", "").strip()
            if not url:
                raise RuntimeError(
                    "OPS_DB_BACKEND=postgres 时必须配置 OPS_DB_URL"
                )
            return cls(backend="postgres", url=url)
        if backend != "sqlite":
            raise RuntimeError(f"不支持的数据库后端: {backend}")

        url = os.getenv("OPS_DB_URL", "").strip()
        if url.startswith("sqlite:///"):
            path = url[len("sqlite:///") :]
            return cls(backend="sqlite", path=path)
        return cls(backend="sqlite", path=os.getenv(path_env) or default_sqlite_path)

    @property
    def placeholder(self) -> str:
        return "%s" if self.backend == "postgres" else "?"

    def parameterize(self, sql: str) -> str:
        if self.backend != "postgres":
            return sql
        return sql.replace("?", "%s")

    def open(self) -> object:
        if self.backend == "postgres":
            import psycopg
            from psycopg.rows import dict_row

            return psycopg.connect(self.url, row_factory=dict_row)

        path = Path(self.path).expanduser()
        if self.path != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(path),
            timeout=5,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @contextmanager
    def connect(self) -> Iterator[object]:
        conn = self.open()
        try:
            if self.backend == "postgres":
                with conn:
                    yield conn
            else:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=5000")
                with conn:
                    yield conn
        finally:
            conn.close()
