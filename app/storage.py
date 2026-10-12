from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .models import Project, now_iso


class StorageConflict(ValueError):
    """A stale project snapshot must not overwrite a newer human decision."""


class ProjectStore:
    """Small durable SQLite store; each operation uses a short-lived connection."""

    def __init__(self, path: str | Path = "data/automation.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    updated_at TEXT NOT NULL,
                    state_json TEXT NOT NULL
                )
                """
            )

    def save(self, project: Project) -> Project:
        previous_updated_at = project.updated_at
        updated_at = now_iso()
        state = project.model_dump(mode="json")
        state["updated_at"] = updated_at
        payload = json.dumps(state, ensure_ascii=False)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO projects(id, updated_at, state_json) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at, state_json=excluded.state_json "
                "WHERE projects.updated_at = ?",
                (project.id, updated_at, payload, previous_updated_at),
            )
            if cursor.rowcount != 1:
                raise StorageConflict("Project was updated by another request.")
        project.updated_at = updated_at
        return project

    def check_connection(self) -> None:
        with self._connect() as connection:
            connection.execute("SELECT 1")

    def get(self, project_id: str) -> Project | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT state_json FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        return Project.model_validate(json.loads(row["state_json"])) if row else None

    def get_if_changed(self, project_id: str, updated_at: str) -> Project | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state_json FROM projects WHERE id = ? AND updated_at <> ?",
                (project_id, updated_at),
            ).fetchone()
        return Project.model_validate(json.loads(row["state_json"])) if row else None

    def list(self) -> list[Project]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT state_json FROM projects ORDER BY updated_at DESC"
            ).fetchall()
        return [Project.model_validate(json.loads(row["state_json"])) for row in rows]

    @property
    def backend(self) -> str:
        return "sqlite"


class PostgresProjectStore:
    """Neon/PostgreSQL store for Vercel and multi-instance deployments.

    The application stores the complete validated project snapshot as JSONB.
    This keeps the first migration backward-compatible with the existing
    Pydantic state while adding durable, shared persistence. The table is
    intentionally namespaced so a future normalized schema can be introduced
    without colliding with Neon-managed tables.
    """

    table_name = "automation_projects"

    def __init__(self, database_url: str) -> None:
        if not database_url.strip():
            raise ValueError("DATABASE_URL cannot be empty.")
        self.database_url = database_url
        self._lock = threading.RLock()
        self._initialized = False

    def _connect(self) -> Any:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - exercised in deployment
            raise RuntimeError(
                "PostgreSQL storage requires psycopg[binary]. Install requirements.txt."
            ) from exc
        return psycopg.connect(self.database_url, row_factory=dict_row, connect_timeout=10)

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table_name} (
                    id TEXT PRIMARY KEY,
                    updated_at TEXT NOT NULL,
                    state_json JSONB NOT NULL
                )
                """
            )
            connection.execute(
                f"CREATE INDEX IF NOT EXISTS automation_projects_updated_at_idx "
                f"ON {self.table_name} (updated_at DESC)"
            )
            connection.commit()

    def _ensure_initialized(self) -> None:
        # Importing the ASGI entrypoint during a Vercel build must not connect
        # to the database. Retry initialization if the first connection fails.
        with self._lock:
            if not self._initialized:
                self._initialize()
                self._initialized = True

    def check_connection(self) -> None:
        with self._connect() as connection:
            connection.execute("SELECT 1")

    def save(self, project: Project) -> Project:
        self._ensure_initialized()
        previous_updated_at = project.updated_at
        updated_at = now_iso()
        payload = project.model_dump(mode="json")
        payload["updated_at"] = updated_at
        try:
            from psycopg.types.json import Jsonb
        except ImportError as exc:  # pragma: no cover - guarded by _connect
            raise RuntimeError("PostgreSQL storage requires psycopg[binary].") from exc
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                f"""
                INSERT INTO {self.table_name}(id, updated_at, state_json)
                VALUES (%s, %s, %s)
                ON CONFLICT(id) DO UPDATE SET
                    updated_at = EXCLUDED.updated_at,
                    state_json = EXCLUDED.state_json
                WHERE automation_projects.updated_at = %s
                """,
                (project.id, updated_at, Jsonb(payload), previous_updated_at),
            )
            if cursor.rowcount != 1:
                raise StorageConflict("Project was updated by another request.")
            connection.commit()
        project.updated_at = updated_at
        return project

    def get(self, project_id: str) -> Project | None:
        self._ensure_initialized()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                f"SELECT state_json FROM {self.table_name} WHERE id = %s", (project_id,)
            ).fetchone()
        if not row:
            return None
        state = row["state_json"]
        return Project.model_validate(json.loads(state) if isinstance(state, str) else state)

    def get_if_changed(self, project_id: str, updated_at: str) -> Project | None:
        self._ensure_initialized()
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT state_json FROM {self.table_name} WHERE id = %s AND updated_at <> %s",
                (project_id, updated_at),
            ).fetchone()
        return Project.model_validate(row["state_json"]) if row else None

    def list(self) -> list[Project]:
        self._ensure_initialized()
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"SELECT state_json FROM {self.table_name} ORDER BY updated_at DESC"
            ).fetchall()
        return [
            Project.model_validate(json.loads(row["state_json"]) if isinstance(row["state_json"], str) else row["state_json"])
            for row in rows
        ]

    @property
    def backend(self) -> str:
        return "neon_postgres"


def build_store(sqlite_path: str | Path = "data/automation.db") -> ProjectStore | PostgresProjectStore:
    """Use Neon when configured, otherwise retain a frictionless local mode."""

    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        return PostgresProjectStore(database_url)
    return ProjectStore(sqlite_path)
