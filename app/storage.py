from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from .models import Project, now_iso


class ProjectStore:
    """Small durable SQLite store; each operation uses a short-lived connection."""

    def __init__(self, path: str | Path = "data/automation.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

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
        project.updated_at = now_iso()
        payload = json.dumps(project.model_dump(mode="json"), ensure_ascii=False)
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO projects(id, updated_at, state_json) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at, state_json=excluded.state_json",
                (project.id, project.updated_at, payload),
            )
        return project

    def get(self, project_id: str) -> Project | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT state_json FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        return Project.model_validate(json.loads(row["state_json"])) if row else None

    def list(self) -> list[Project]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT state_json FROM projects ORDER BY updated_at DESC"
            ).fetchall()
        return [Project.model_validate(json.loads(row["state_json"])) for row in rows]
