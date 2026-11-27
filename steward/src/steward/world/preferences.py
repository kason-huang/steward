"""Operator preferences that can survive a task, scene or process restart."""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path


class OperatorPreferences:
    def __init__(self, home: Path):
        self.path = home / "operator-preferences.sqlite"

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        try:
            db.execute("""CREATE TABLE IF NOT EXISTS preferences (
                revision INTEGER PRIMARY KEY,
                key TEXT NOT NULL,
                value TEXT,
                updated_at REAL NOT NULL
            )""")
            return db
        except sqlite3.Error:
            db.close()
            raise

    def change(self, key: str, value: str | None) -> dict:
        if not isinstance(key, str) or not key.strip() or len(key.strip()) > 80:
            raise ValueError("a preference key must contain 1 to 80 characters")
        if value is not None and (not isinstance(value, str) or not value.strip() or len(value.strip()) > 1000):
            raise ValueError("a preference value must contain 1 to 1000 characters, or null to remove it")
        key = key.strip()
        value = None if value is None else value.strip()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            updated_at = time.time()
            row = db.execute("INSERT INTO preferences (key, value, updated_at) VALUES (?, ?, ?)",
                             (key, value, updated_at))
            return self._entry(row.lastrowid, key, value, updated_at)

    def read(self, *, history: bool = False) -> list[dict]:
        if not self.path.is_file():
            return []
        with closing(self._connect()) as db:
            if history:
                rows = db.execute("SELECT * FROM preferences ORDER BY revision DESC LIMIT 100").fetchall()
            else:
                rows = db.execute("""SELECT * FROM preferences
                    WHERE revision IN (SELECT MAX(revision) FROM preferences GROUP BY key)
                    AND value IS NOT NULL ORDER BY key""").fetchall()
        return [self._entry(*row) for row in rows]

    @staticmethod
    def _entry(revision: int, key: str, value: str | None, updated_at: float) -> dict:
        return {"key": key, "value": value, "revision": revision, "updatedAt": updated_at, "source": "operator"}
