"""FactRepository — all SQL operations for facts and fact_relations.

Extracted from MemoryDatabase to reduce its size while preserving the
public API. MemoryDatabase.facts returns a FactRepository instance, and
all existing MemoryDatabase.fact_* methods delegate to it.

Domain:
  - facts table (core memory units)
  - fact_relations table (supersedes, contradicts, confirms, etc.)
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from typing import Any, TYPE_CHECKING

from companion.storage.repositories.base import BaseRepository, _json, _loads

if TYPE_CHECKING:
    from companion.storage.sqlite_db import MemoryDatabase

logger = logging.getLogger(__name__)


class FactRepository(BaseRepository):
    """CRUD and queries for facts and their relations."""

    # ── Insert ──────────────────────────────────────────────────────────

    def insert_fact(self, row: dict[str, Any]) -> None:
        """Insert or upsert a fact row.

        Uses ON CONFLICT(id) DO UPDATE to handle re-imports and migrations
        without silent data loss.
        """
        with self._db._conn() as conn:
            conn.execute(
                """
                INSERT INTO facts (
                  id, fact, date, created_at, memory_kind, importance, confidence,
                  source, source_type, tags, status, valid_from, valid_until,
                  schema_version, evidence, facts_sent_count, facts_used_count, embedding,
                  category, anchor_flag, manual_lock, archived, updated_at,
                  last_accessed, access_count, decay_exempt, domain, meta,
                  last_retrieved_at, last_used_at, superseded_by
                ) VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                  fact=excluded.fact,
                  date=excluded.date,
                  memory_kind=excluded.memory_kind,
                  importance=excluded.importance,
                  confidence=excluded.confidence,
                  source=excluded.source,
                  source_type=excluded.source_type,
                  tags=excluded.tags,
                  status=excluded.status,
                  valid_from=excluded.valid_from,
                  valid_until=excluded.valid_until,
                  evidence=excluded.evidence,
                  facts_sent_count=excluded.facts_sent_count,
                  facts_used_count=excluded.facts_used_count,
                  embedding=COALESCE(excluded.embedding, facts.embedding),
                  category=COALESCE(excluded.category, facts.category),
                  anchor_flag=excluded.anchor_flag,
                  manual_lock=excluded.manual_lock,
                  archived=excluded.archived,
                  updated_at=excluded.updated_at,
                  last_accessed=COALESCE(excluded.last_accessed, facts.last_accessed),
                  access_count=excluded.access_count,
                  decay_exempt=excluded.decay_exempt,
                  domain=excluded.domain,
                  meta=excluded.meta,
                  last_retrieved_at=COALESCE(excluded.last_retrieved_at, facts.last_retrieved_at),
                  last_used_at=COALESCE(excluded.last_used_at, facts.last_used_at),
                  superseded_by=excluded.superseded_by
                """,
                (
                    row["id"], row["fact"], row.get("date"), row.get("created_at"),
                    row.get("memory_kind", "event"), row.get("importance", 5),
                    row.get("confidence", 0.8), row.get("source"), row.get("source_type"),
                    json.dumps(row.get("tags", []), ensure_ascii=False),
                    row.get("status", "active"), row.get("valid_from"),
                    row.get("valid_until"), row.get("schema_version", 1),
                    json.dumps(row.get("evidence", []), ensure_ascii=False),
                    row.get("facts_sent_count", 0), row.get("facts_used_count", 0),
                    row.get("embedding"),
                    row.get("category", "life"),
                    row.get("anchor_flag", 1 if any(
                        str(t).lower() in {"anchor", "core_identity", "pinned"}
                        for t in row.get("tags", [])
                    ) or row.get("memory_kind") == "permanent" else 0),
                    row.get("manual_lock", 0),
                    row.get("archived", 1 if row.get("status") == "archived" else 0),
                    row.get("updated_at") or row.get("created_at"),
                    row.get("last_accessed"), row.get("access_count", 0),
                    row.get("decay_exempt", 0),
                    row.get("domain") or "user",
                    json.dumps(row.get("meta") or {}, ensure_ascii=False),
                    row.get("last_retrieved_at"), row.get("last_used_at"),
                    row.get("superseded_by") or "",
                ),
            )

    def insert_relation(self, row: dict[str, Any]) -> None:
        """Insert a fact relation (IGNORE on duplicate id)."""
        with self._db._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO fact_relations VALUES (?,?,?,?,?,?,?)",
                (
                    row["id"], row["from_id"], row["to_id"], row["relation"],
                    row.get("created_at"), row.get("reason", ""),
                    row.get("confidence", 0.8),
                ),
            )

    # ── Read ────────────────────────────────────────────────────────────

    def list_facts(self, status: str | None = "active") -> list[dict[str, Any]]:
        """List facts, optionally filtered by status."""
        with self._db._conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM facts WHERE status=? ORDER BY date DESC, created_at DESC",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM facts ORDER BY date DESC, created_at DESC"
                ).fetchall()
        return [self._row_fact(r) for r in rows]

    def get_fact(self, fact_id: str) -> dict[str, Any] | None:
        """Get a single fact by id."""
        with self._db._conn() as conn:
            row = conn.execute("SELECT * FROM facts WHERE id=?", (fact_id,)).fetchone()
        return self._row_fact(row) if row else None

    def count_facts(self, status: str | None = None) -> int:
        """Count facts, optionally filtered by status."""
        with self._db._conn() as conn:
            if status:
                return conn.execute(
                    "SELECT COUNT(*) FROM facts WHERE status=?", (status,)
                ).fetchone()[0]
            return conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]

    def get_fact_relations(self, fact_id: str) -> list[dict[str, Any]]:
        """Get all relations where this fact is source or target."""
        with self._db._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM fact_relations WHERE from_id=? OR to_id=? ORDER BY created_at ASC",
                (fact_id, fact_id),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Update ──────────────────────────────────────────────────────────

    def update_fact_status(
        self, fact_id: str, status: str, expected_version: int | None = None
    ) -> None:
        """Update a fact's status with optional OCC version check."""
        self.update_fact_fields(fact_id, {"status": status}, expected_version=expected_version)

    def update_fact_fields(
        self,
        fact_id: str,
        fields: dict[str, Any],
        expected_version: int | None = None,
    ) -> None:
        """Update a subset of fact columns atomically.

        Enforces lifecycle transitions via validate_transition() when
        status is being changed. Supports OCC via expected_version.
        """
        fields = dict(fields)
        if "retrieved_count" in fields and "facts_sent_count" not in fields:
            fields["facts_sent_count"] = fields.pop("retrieved_count")
        if "used_count" in fields and "facts_used_count" not in fields:
            fields["facts_used_count"] = fields.pop("used_count")
        allowed = {
            "fact", "date", "importance", "confidence", "tags", "status",
            "valid_from", "valid_until", "evidence", "version", "superseded_by",
            "memory_kind", "source", "source_type", "anchor_flag", "manual_lock",
            "domain", "meta", "archived", "facts_sent_count", "facts_used_count",
            "epistemic_class", "support_count", "contradiction_count",
        }
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return
        if "tags" in sets and not isinstance(sets["tags"], str):
            sets["tags"] = json.dumps(sets["tags"], ensure_ascii=False)
        if "evidence" in sets and not isinstance(sets["evidence"], str):
            sets["evidence"] = json.dumps(sets["evidence"], ensure_ascii=False)
        if "meta" in sets and not isinstance(sets["meta"], str):
            sets["meta"] = json.dumps(sets["meta"] or {}, ensure_ascii=False)
        sets["updated_at"] = fields.get("updated_at") or datetime.now().isoformat()
        if "version" not in sets:
            assignments = ", ".join(f"{k}=?" for k in sets) + ", version=version+1"
        else:
            assignments = ", ".join(f"{k}=?" for k in sets)
        params = list(sets.values()) + [fact_id]
        with self._db._conn() as conn:
            if "status" in sets:
                row = conn.execute(
                    "SELECT status FROM facts WHERE id=?", (fact_id,)
                ).fetchone()
                if row is not None:
                    from companion.memory.lifecycle import validate_transition
                    validate_transition(str(row["status"]), str(sets["status"]))
            if expected_version is not None:
                params.append(expected_version)
                cursor = conn.execute(
                    f"UPDATE facts SET {assignments} WHERE id=? AND version=?", params
                )
                if cursor.rowcount == 0:
                    row = conn.execute(
                        "SELECT version FROM facts WHERE id=?", (fact_id,)
                    ).fetchone()
                    actual_ver = row[0] if row else None
                    from companion.exceptions import ConcurrentModificationError
                    raise ConcurrentModificationError(
                        f"Concurrent modification on fact {fact_id}: "
                        f"expected version {expected_version}, actual {actual_ver}",
                        record_id=fact_id,
                        expected_version=expected_version,
                        actual_version=actual_ver,
                    )
            else:
                conn.execute(f"UPDATE facts SET {assignments} WHERE id=?", params)

    def delete_fact(self, fact_id: str) -> bool:
        """Hard-delete a fact and its relations."""
        with self._db._conn() as conn:
            conn.execute("DELETE FROM memory_genome WHERE memory_id=?", (fact_id,))
            cur = conn.execute("DELETE FROM facts WHERE id=?", (fact_id,))
            conn.execute(
                "DELETE FROM fact_relations WHERE from_id=? OR to_id=?",
                (fact_id, fact_id),
            )
            return cur.rowcount > 0

    # ── Batch ───────────────────────────────────────────────────────────

    def batch_insert(self, rows: list[dict[str, Any]]) -> None:
        """Bulk-insert facts (INSERT OR IGNORE)."""
        if not rows:
            return
        tuples = [
            (
                row["id"], row["fact"], row.get("date"), row.get("created_at"),
                row.get("memory_kind", "event"), row.get("importance", 5),
                row.get("confidence", 0.8), row.get("source"), row.get("source_type"),
                json.dumps(row.get("tags", []), ensure_ascii=False),
                row.get("status", "active"), row.get("valid_from"),
                row.get("valid_until"), row.get("schema_version", 1),
                json.dumps(row.get("evidence", []), ensure_ascii=False),
                row.get("facts_sent_count", 0), row.get("facts_used_count", 0),
                row.get("embedding"),
                row.get("domain") or "user",
                json.dumps(row.get("meta") or {}, ensure_ascii=False),
                row.get("last_retrieved_at"), row.get("last_used_at"),
            )
            for row in rows
        ]
        with self._db._conn() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO facts (
                  id, fact, date, created_at, memory_kind, importance, confidence,
                  source, source_type, tags, status, valid_from, valid_until,
                  schema_version, evidence, facts_sent_count, facts_used_count, embedding,
                  domain, meta, last_retrieved_at, last_used_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                tuples,
            )

    def batch_insert_relations(self, rows: list[dict[str, Any]]) -> None:
        """Bulk-insert fact relations."""
        if not rows:
            return
        tuples = [
            (
                row["id"], row["from_id"], row["to_id"], row["relation"],
                row.get("created_at"), row.get("reason", ""),
                row.get("confidence", 0.8),
            )
            for row in rows
        ]
        with self._db._conn() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO fact_relations VALUES (?,?,?,?,?,?,?)",
                tuples,
            )

    # ── Usage tracking ──────────────────────────────────────────────────

    def increment_usage(self, fact_id: str, used: bool = False) -> None:
        """Increment sent/used counters for a fact."""
        now_iso = datetime.now().isoformat()
        with self._db._conn() as conn:
            if used:
                conn.execute(
                    "UPDATE facts SET facts_sent_count = facts_sent_count + 1, "
                    "facts_used_count = facts_used_count + 1, "
                    "last_retrieved_at = ?, last_used_at = ? WHERE id=?",
                    (now_iso, now_iso, fact_id),
                )
            else:
                conn.execute(
                    "UPDATE facts SET facts_sent_count = facts_sent_count + 1, "
                    "last_retrieved_at = ? WHERE id=?",
                    (now_iso, fact_id),
                )

    def increment_usage_batch(
        self, sent_ids: list[str], used_ids: list[str]
    ) -> None:
        """Batch increment sent/used counters."""
        if not sent_ids and not used_ids:
            return
        now_iso = datetime.now().isoformat()
        with self._db._conn() as conn:
            if sent_ids:
                sent_only = [i for i in sent_ids if i not in used_ids]
                if sent_only:
                    conn.executemany(
                        "UPDATE facts SET facts_sent_count = facts_sent_count + 1, "
                        "last_retrieved_at = ? WHERE id=?",
                        [(now_iso, i) for i in sent_only],
                    )
            if used_ids:
                conn.executemany(
                    "UPDATE facts SET facts_sent_count = facts_sent_count + 1, "
                    "facts_used_count = facts_used_count + 1, "
                    "last_retrieved_at = ?, last_used_at = ? WHERE id=?",
                    [(now_iso, now_iso, i) for i in used_ids],
                )

    # ── Row mapping ─────────────────────────────────────────────────────

    @staticmethod
    def _row_fact(row: sqlite3.Row) -> dict[str, Any]:
        """Convert a sqlite3.Row to a fact dict with deserialized JSON fields."""
        d = dict(row)
        d["tags"] = json.loads(d.get("tags") or "[]")
        d["evidence"] = json.loads(d.get("evidence") or "[]")
        d["meta"] = _loads(d.get("meta"), default={})
        if not isinstance(d["meta"], dict):
            d["meta"] = {}
        if not d.get("domain"):
            d["domain"] = "user"
        return d
