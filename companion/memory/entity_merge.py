"""Entity Merge Service for Phase 2.2 Entity Intelligence.

Finds similar/duplicate entities and merges them safely, transferring all
relations, mentions, and attributes under OCC version control and audit logging.
"""
from __future__ import annotations

import difflib
import logging
from typing import Any
from companion.models import Entity
from companion.storage.sqlite_db import MemoryDatabase

logger = logging.getLogger(__name__)


class EntityMergeService:
    """Service for discovering duplicate entities and executing safe merges."""

    def __init__(self, db: MemoryDatabase, vector: Any | None = None) -> None:
        self.db = db
        self.vector = vector

    def find_merge_candidates(
        self, min_similarity: float = 0.85
    ) -> list[tuple[Entity, Entity, float]]:
        """Find pairs of entities that have high name/alias similarity."""
        raw_list = self.db.list_world_entities(limit=500)
        entities = [Entity.from_dict(e) for e in raw_list]
        candidates: list[tuple[Entity, Entity, float]] = []

        for i in range(len(entities)):
            for j in range(i + 1, len(entities)):
                e1 = entities[i]
                e2 = entities[j]
                if e1.type != e2.type:
                    continue

                sim = difflib.SequenceMatcher(
                    None, e1.name.lower(), e2.name.lower()
                ).ratio()

                # Check if name is in aliases
                if (
                    e2.name.lower() in [a.lower() for a in e1.aliases]
                    or e1.name.lower() in [a.lower() for a in e2.aliases]
                ):
                    sim = 1.0

                if sim >= min_similarity:
                    candidates.append((e1, e2, round(sim, 3)))

        # Sort by similarity descending
        candidates.sort(key=lambda x: x[2], reverse=True)
        return candidates

    def merge_entities(self, primary_id: str, secondary_id: str) -> Entity:
        """Merge secondary entity into primary entity.

        - Combines aliases (including secondary's primary name).
        - Transfers all relations (from/to), mentions, and attributes to primary_id.
        - Deletes secondary_id.
        - Increments primary version (OCC) and emits audit mutation entry.
        """
        if primary_id == secondary_id:
            raise ValueError("Cannot merge an entity into itself")

        p_raw = self.db.get_world_entity(primary_id)
        s_raw = self.db.get_world_entity(secondary_id)

        if not p_raw:
            raise ValueError(f"Primary entity '{primary_id}' not found")
        if not s_raw:
            raise ValueError(f"Secondary entity '{secondary_id}' not found")

        primary = Entity.from_dict(p_raw)
        secondary = Entity.from_dict(s_raw)

        # 1. Merge aliases
        new_aliases = list(primary.aliases)
        for cand in [secondary.name] + secondary.aliases:
            if cand and cand.lower() not in [a.lower() for a in new_aliases] and cand.lower() != primary.name.lower():
                new_aliases.append(cand)
        primary.aliases = new_aliases
        primary.importance = max(primary.importance, secondary.importance)

        # 2. Re-point relations, mentions, and attributes in SQLite
        with self.db._conn() as conn:
            # Transfer attributes
            conn.execute(
                "UPDATE entity_attributes SET entity_id=? WHERE entity_id=?",
                (primary_id, secondary_id),
            )
            # Transfer mentions
            conn.execute(
                "UPDATE entity_mentions SET entity_id=? WHERE entity_id=?",
                (primary_id, secondary_id),
            )
            # Transfer relations (from_entity_id) - ignore conflicts if relation already exists
            conn.execute(
                """
                UPDATE OR IGNORE entity_relations
                SET from_entity_id=?
                WHERE from_entity_id=?
                """,
                (primary_id, secondary_id),
            )
            conn.execute(
                "DELETE FROM entity_relations WHERE from_entity_id=?",
                (secondary_id,),
            )
            # Transfer relations (to_entity_id) - ignore conflicts if relation already exists
            conn.execute(
                """
                UPDATE OR IGNORE entity_relations
                SET to_entity_id=?
                WHERE to_entity_id=?
                """,
                (primary_id, secondary_id),
            )
            conn.execute(
                "DELETE FROM entity_relations WHERE to_entity_id=?",
                (secondary_id,),
            )
            # Delete secondary entity
            conn.execute("DELETE FROM entities WHERE entity_id=?", (secondary_id,))

        # 3. Save primary entity via upsert_world_entity with OCC check
        self.db.upsert_world_entity(primary.to_dict(), expected_version=primary.version)
        updated_raw = self.db.get_world_entity(primary_id)
        assert updated_raw is not None
        merged_ent = Entity.from_dict(updated_raw)

        # 4. Log mutation
        self.db.log_mutation(
            entity_id=primary_id,
            action="MERGE",
            reason=f"Merged entity '{secondary_id}' ({secondary.name}) into '{primary_id}' ({primary.name})",
            state_before={"primary": p_raw, "secondary": s_raw},
            state_after={"primary": updated_raw, "secondary_deleted": True},
            entity_type="entity",
            initiator="world_model",
        )

        return merged_ent
