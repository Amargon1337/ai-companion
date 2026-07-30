"""Graph Consistency Checker for Phase 2.2 Entity Intelligence.

Detects and optionally repairs broken links, orphaned entities, and dangling
mentions/relations in the World Model cognitive graph.
"""
from __future__ import annotations

import logging
from typing import Any
from companion.storage.sqlite_db import MemoryDatabase

logger = logging.getLogger(__name__)


class GraphConsistencyChecker:
    """Checks cognitive graph integrity and removes dangling references."""

    def __init__(self, db: MemoryDatabase) -> None:
        self.db = db

    def check_and_repair(self, repair: bool = False) -> dict[str, Any]:
        """Detect broken relations, mentions, attributes, and orphaned entities.

        If repair=True, deletes dangling rows from SQLite.
        """
        report: dict[str, Any] = {
            "dangling_relations": 0,
            "dangling_mentions": 0,
            "dangling_attributes": 0,
            "orphaned_entities": [],
            "repaired": False,
        }

        with self.db._conn() as conn:
            # 1. Check dangling entity_relations (from/to entity does not exist)
            dangling_rels = conn.execute(
                """
                SELECT relation_id FROM entity_relations
                WHERE from_entity_id NOT IN (SELECT entity_id FROM entities)
                   OR to_entity_id NOT IN (SELECT entity_id FROM entities)
                """
            ).fetchall()
            report["dangling_relations"] = len(dangling_rels)

            # 2. Check dangling entity_mentions (entity_id or fact_id does not exist)
            dangling_mentions = conn.execute(
                """
                SELECT id FROM entity_mentions
                WHERE entity_id NOT IN (SELECT entity_id FROM entities)
                   OR fact_id NOT IN (SELECT id FROM facts)
                """
            ).fetchall()
            report["dangling_mentions"] = len(dangling_mentions)

            # 3. Check dangling entity_attributes (entity_id does not exist)
            dangling_attrs = conn.execute(
                """
                SELECT id FROM entity_attributes
                WHERE entity_id NOT IN (SELECT entity_id FROM entities)
                """
            ).fetchall()
            report["dangling_attributes"] = len(dangling_attrs)

            # 4. Find orphaned entities (0 mentions and 0 relations)
            orphans = conn.execute(
                """
                SELECT entity_id FROM entities
                WHERE entity_id NOT IN (SELECT entity_id FROM entity_mentions)
                  AND entity_id NOT IN (SELECT from_entity_id FROM entity_relations)
                  AND entity_id NOT IN (SELECT to_entity_id FROM entity_relations)
                """
            ).fetchall()
            report["orphaned_entities"] = [r["entity_id"] for r in orphans]

            # 5. Execute repair if requested
            if repair:
                if dangling_rels:
                    conn.execute(
                        """
                        DELETE FROM entity_relations
                        WHERE from_entity_id NOT IN (SELECT entity_id FROM entities)
                           OR to_entity_id NOT IN (SELECT entity_id FROM entities)
                        """
                    )
                if dangling_mentions:
                    conn.execute(
                        """
                        DELETE FROM entity_mentions
                        WHERE entity_id NOT IN (SELECT entity_id FROM entities)
                           OR fact_id NOT IN (SELECT id FROM facts)
                        """
                    )
                if dangling_attrs:
                    conn.execute(
                        """
                        DELETE FROM entity_attributes
                        WHERE entity_id NOT IN (SELECT entity_id FROM entities)
                        """
                    )
                report["repaired"] = True

        return report
