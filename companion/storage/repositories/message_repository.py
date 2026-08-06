"""MessageRepository — SQL operations for conversation messages."""
from __future__ import annotations

import json
from typing import Any

from companion.storage.repositories.base import BaseRepository


class MessageRepository(BaseRepository):
    """CRUD and queries for messages."""

    def insert(self, row: dict[str, Any]) -> None:
        """Insert a message (IGNORE on duplicate id)."""
        with self._db._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO messages VALUES (?,?,?,?,?,?,?,?)",
                (
                    row["id"], row.get("ts"), row.get("role"), row.get("text"),
                    row.get("importance", 5), row.get("mode", "default"),
                    json.dumps(row.get("signals", []), ensure_ascii=False),
                    row.get("user_id"),
                ),
            )

    def list_messages(
        self, min_importance: int = 0, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """List messages ordered by timestamp DESC."""
        q = "SELECT * FROM messages WHERE importance>=? ORDER BY ts DESC"
        params: list[Any] = [min_importance]
        if limit is not None:
            q += " LIMIT ?"
            params.append(max(0, int(limit)))
        with self._db._conn() as conn:
            rows = conn.execute(q, tuple(params)).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["signals"] = json.loads(d.get("signals") or "[]")
            result.append(d)
        return result

    def count(self) -> int:
        """Total message count."""
        with self._db._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

    def batch_insert(self, rows: list[dict[str, Any]]) -> None:
        """Bulk-insert messages."""
        if not rows:
            return
        tuples = [
            (
                row["id"], row.get("ts"), row.get("role"), row.get("text"),
                row.get("importance", 5), row.get("mode", "default"),
                json.dumps(row.get("signals", []), ensure_ascii=False),
                row.get("user_id"),
            )
            for row in rows
        ]
        with self._db._conn() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO messages VALUES (?,?,?,?,?,?,?,?)",
                tuples,
            )
