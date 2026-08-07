"""EntityRepository — SQL operations for the World Model graph.

Domain:
  - entities table (nodes in the cognitive graph)
  - entity_attributes table (key-value properties)
  - entity_relations table (edges between entities)
  - entity_mentions table (links between entities and facts)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from companion.storage.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class EntityRepository(BaseRepository):
    """CRUD and queries for entities, attributes, relations, and mentions."""

    # ── Entity CRUD ─────────────────────────────────────────────────────

    def upsert_entity(self, entity: dict[str, Any], expected_version: int | None = None) -> str:
        """Create or update an entity. Returns entity_id."""
        entity_id = str(entity.get("entity_id") or "")
        if not entity_id:
            raise ValueError("entity_id is required")
        now_iso = datetime.now().isoformat()
        _js = lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else str(v)
        existing = self.get_entity(entity_id)
        if existing is None:
            version = int(entity.get("version", 1))
            with self._db._conn() as conn:
                conn.execute(
                    """INSERT INTO entities (
                      entity_id, name, type, importance, version, created_at,
                      updated_at, last_mentioned_at, aliases, summary
                    ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        entity_id, str(entity.get("name", "")),
                        str(entity.get("type", "concept")),
                        float(entity.get("importance", 0.5)),
                        version,
                        str(entity.get("created_at") or now_iso),
                        now_iso,
                        str(entity.get("last_mentioned_at") or now_iso),
                        _js(entity.get("aliases", [])),
                        str(entity.get("summary", "")),
                    ),
                )
            self._db.log_mutation(
                entity_id=entity_id, action="CREATE",
                reason="Entity creation in World Model",
                state_before={}, state_after=entity,
                entity_type="entity", initiator="world_model",
            )
            return entity_id
        else:
            ver = expected_version if expected_version is not None else entity.get("version")
            if ver is not None and int(existing["version"]) != int(ver):
                from companion.exceptions import ConcurrentModificationError
                raise ConcurrentModificationError(
                    f"Concurrent modification on entity {entity_id}",
                    record_id=entity_id,
                    expected_version=int(ver),
                    actual_version=int(existing["version"]),
                )
            new_ver = int(existing["version"]) + 1
            with self._db._conn() as conn:
                conn.execute(
                    """UPDATE entities SET name=?, type=?, importance=?, version=?,
                       updated_at=?, last_mentioned_at=?, aliases=?, summary=?
                       WHERE entity_id=?""",
                    (
                        str(entity.get("name", existing["name"])),
                        str(entity.get("type", existing["type"])),
                        float(entity.get("importance", existing["importance"])),
                        new_ver, now_iso,
                        str(entity.get("last_mentioned_at", existing.get("last_mentioned_at") or now_iso)),
                        _js(entity.get("aliases", existing.get("aliases", []))),
                        str(entity.get("summary", existing.get("summary", ""))),
                        entity_id,
                    ),
                )
            return entity_id

    def get_entity(self, entity_id: str) -> dict[str, Any] | None:
        with self._db._conn() as conn:
            row = conn.execute(
                "SELECT * FROM entities WHERE entity_id=?", (entity_id,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        if isinstance(d.get("aliases"), str):
            try:
                d["aliases"] = json.loads(d["aliases"])
            except Exception:
                d["aliases"] = []
        return d

    def list_entities(
        self, entity_type: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM entities"
        params: list[Any] = []
        if entity_type:
            query += " WHERE type=?"
            params.append(entity_type)
        query += " ORDER BY importance DESC, last_mentioned_at DESC LIMIT ?"
        params.append(limit)
        with self._db._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        res = []
        for r in rows:
            d = dict(r)
            if isinstance(d.get("aliases"), str):
                try:
                    d["aliases"] = json.loads(d["aliases"])
                except Exception:
                    d["aliases"] = []
            res.append(d)
        return res

    def search_by_name(self, name_query: str) -> list[dict[str, Any]]:
        with self._db._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM entities WHERE name LIKE ? OR aliases LIKE ? "
                "ORDER BY importance DESC",
                (f"%{name_query}%", f"%{name_query}%"),
            ).fetchall()
        res = []
        for r in rows:
            d = dict(r)
            if isinstance(d.get("aliases"), str):
                try:
                    d["aliases"] = json.loads(d["aliases"])
                except Exception:
                    d["aliases"] = []
            res.append(d)
        return res

    # ── Attributes ──────────────────────────────────────────────────────

    def add_attribute(self, attr: dict[str, Any]) -> int:
        now_iso = str(attr.get("created_at") or datetime.now().isoformat())
        with self._db._conn() as conn:
            cur = conn.execute(
                """INSERT INTO entity_attributes (
                  entity_id, attribute_key, attribute_value, confidence,
                  source_fact_id, created_at
                ) VALUES (?,?,?,?,?,?)""",
                (
                    str(attr.get("entity_id", "")),
                    str(attr.get("attribute_key", "")),
                    str(attr.get("attribute_value", "")),
                    float(attr.get("confidence", 0.8)),
                    str(attr.get("source_fact_id", "")),
                    now_iso,
                ),
            )
            return int(cur.lastrowid or 0)

    def get_attributes(self, entity_id: str) -> list[dict[str, Any]]:
        with self._db._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM entity_attributes WHERE entity_id=? "
                "ORDER BY confidence DESC, id DESC",
                (entity_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Relations ───────────────────────────────────────────────────────

    def upsert_relation(self, rel: dict[str, Any], expected_version: int | None = None) -> str:
        relation_id = str(rel.get("relation_id") or "")
        from_id = str(rel.get("from_entity_id") or "")
        to_id = str(rel.get("to_entity_id") or "")
        rel_type = str(rel.get("relation_type") or "")
        if not (from_id and to_id and rel_type):
            raise ValueError("from_entity_id, to_entity_id, and relation_type required")
        now_iso = datetime.now().isoformat()

        existing = None
        if relation_id:
            existing = self.get_relation(relation_id)
        else:
            with self._db._conn() as conn:
                row = conn.execute(
                    "SELECT * FROM entity_relations WHERE from_entity_id=? "
                    "AND to_entity_id=? AND relation_type=?",
                    (from_id, to_id, rel_type),
                ).fetchone()
                if row:
                    existing = dict(row)
                    relation_id = str(existing["relation_id"])
                else:
                    relation_id = f"erel_{__import__('uuid').uuid4().hex[:12]}"

        if existing is None:
            version = int(rel.get("version", 1))
            with self._db._conn() as conn:
                conn.execute(
                    """INSERT INTO entity_relations (
                      relation_id, from_entity_id, to_entity_id, relation_type,
                      trust, interaction_frequency, sentiment, relationship_strength,
                      version, last_seen_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        relation_id, from_id, to_id, rel_type,
                        float(rel.get("trust", 0.5)),
                        float(rel.get("interaction_frequency", 0.0)),
                        float(rel.get("sentiment", 0.0)),
                        float(rel.get("relationship_strength", 0.5)),
                        version,
                        str(rel.get("last_seen_at") or now_iso),
                    ),
                )
            return relation_id
        else:
            if expected_version is not None and int(existing["version"]) != int(expected_version):
                from companion.exceptions import ConcurrentModificationError
                raise ConcurrentModificationError(
                    f"Concurrent modification on entity_relation {relation_id}",
                    record_id=relation_id,
                    expected_version=int(expected_version),
                    actual_version=existing["version"],
                )
            new_ver = int(existing["version"]) + 1
            with self._db._conn() as conn:
                conn.execute(
                    """UPDATE entity_relations SET trust=?, interaction_frequency=?,
                       sentiment=?, relationship_strength=?, version=?, last_seen_at=?
                       WHERE relation_id=?""",
                    (
                        float(rel.get("trust", existing["trust"])),
                        float(rel.get("interaction_frequency", existing["interaction_frequency"])),
                        float(rel.get("sentiment", existing["sentiment"])),
                        float(rel.get("relationship_strength", existing["relationship_strength"])),
                        new_ver,
                        str(rel.get("last_seen_at", existing.get("last_seen_at") or now_iso)),
                        relation_id,
                    ),
                )
            return relation_id

    def get_relation(self, relation_id: str) -> dict[str, Any] | None:
        with self._db._conn() as conn:
            row = conn.execute(
                "SELECT * FROM entity_relations WHERE relation_id=?", (relation_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_relations(
        self,
        from_entity_id: str | None = None,
        to_entity_id: str | None = None,
        entity_id: str | None = None,
        min_trust: float = 0.0,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM entity_relations WHERE trust >= ?"
        params: list[Any] = [min_trust]
        if entity_id:
            query += " AND (from_entity_id = ? OR to_entity_id = ?)"
            params.extend([entity_id, entity_id])
        if from_entity_id:
            query += " AND from_entity_id = ?"
            params.append(from_entity_id)
        if to_entity_id:
            query += " AND to_entity_id = ?"
            params.append(to_entity_id)
        query += " ORDER BY trust DESC, relationship_strength DESC"
        with self._db._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    # ── Mentions ────────────────────────────────────────────────────────

    def add_mention(self, mention: dict[str, Any]) -> int:
        now_iso = str(mention.get("created_at") or datetime.now().isoformat())
        with self._db._conn() as conn:
            cur = conn.execute(
                "INSERT INTO entity_mentions (entity_id, fact_id, context_snippet, created_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    str(mention.get("entity_id", "")),
                    str(mention.get("fact_id", "")),
                    str(mention.get("context_snippet", "")),
                    now_iso,
                ),
            )
            return int(cur.lastrowid or 0)

    def get_mentions_for_fact(self, fact_id: str) -> list[dict[str, Any]]:
        with self._db._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM entity_mentions WHERE fact_id=?", (fact_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_mentions_for_entity(self, entity_id: str) -> list[dict[str, Any]]:
        with self._db._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM entity_mentions WHERE entity_id=? ORDER BY id DESC",
                (entity_id,),
            ).fetchall()
        return [dict(r) for r in rows]
