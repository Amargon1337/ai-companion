"""Graph Retriever Service for Phase 2.2 Entity Intelligence.

Implements multi-hop graph expansion (User Query -> Entity Search -> Neighbour
Expansion up to depth 2-3 -> Fact Retrieval) to provide deep context to retrieval.
"""
from __future__ import annotations

import logging
from typing import Any
from companion.models import Entity, EntityRelation, Fact
from companion.storage.sqlite_db import MemoryDatabase
from companion.memory.entity_resolver import EntityResolverService

logger = logging.getLogger(__name__)


class GraphRetrieverService:
    """Service for multi-hop subgraph expansion and knowledge retrieval."""

    def __init__(self, db: MemoryDatabase, vector: Any | None = None) -> None:
        self.db = db
        self.vector = vector
        self.resolver = EntityResolverService(db, vector=vector)

    def retrieve_subgraph(
        self,
        query: str,
        depth: int = 2,
        min_trust: float = 0.5,
    ) -> dict[str, Any]:
        """Perform multi-hop graph retrieval starting from query keywords/entities.

        1. Identifies root matching entities from query.
        2. BFS traversal across entity_relations up to `depth` hops where trust >= min_trust.
        3. Retrieves facts that mention any entity in the expanded subgraph.
        """
        root_entities: list[Entity] = []
        visited_ids: set[str] = set()

        # Step 1: Entity Search
        # Try resolving the entire query
        resolved = self.resolver.resolve(query)
        if resolved and resolved.entity_id:
            root_entities.append(resolved)
            visited_ids.add(resolved.entity_id)

        # Also check keyword matching on words >= 3 chars
        words = [w.strip() for w in query.split() if len(w.strip()) >= 3]
        for w in words:
            matches = self.db.search_world_entities_by_name(w)
            for m in matches:
                ent = Entity.from_dict(m)
                if ent.entity_id not in visited_ids:
                    root_entities.append(ent)
                    visited_ids.add(ent.entity_id)

        if not root_entities:
            return {
                "root_entities": [],
                "expanded_entities": [],
                "relations": [],
                "related_facts": [],
            }

        # Step 2: BFS Multi-hop Expansion
        expanded_entities: list[Entity] = []
        collected_relations: list[EntityRelation] = []
        seen_rel_ids: set[str] = set()

        frontier = set(visited_ids)
        for _ in range(max(1, depth)):
            next_frontier: set[str] = set()
            for eid in frontier:
                rels_raw = self.db.list_entity_relations(entity_id=eid, min_trust=min_trust)
                for r in rels_raw:
                    rel = EntityRelation.from_dict(r)
                    if rel.relation_id not in seen_rel_ids:
                        seen_rel_ids.add(rel.relation_id)
                        collected_relations.append(rel)

                    other_id = rel.to_entity_id if rel.from_entity_id == eid else rel.from_entity_id
                    if other_id not in visited_ids:
                        visited_ids.add(other_id)
                        next_frontier.add(other_id)
                        other_raw = self.db.get_world_entity(other_id)
                        if other_raw:
                            expanded_entities.append(Entity.from_dict(other_raw))
            frontier = next_frontier
            if not frontier:
                break

        # Step 3: Fact Retrieval across all visited_ids
        collected_facts: list[Fact] = []
        seen_fact_ids: set[str] = set()

        with self.db._conn() as conn:
            for eid in visited_ids:
                rows = conn.execute(
                    "SELECT fact_id FROM entity_mentions WHERE entity_id=?", (eid,)
                ).fetchall()
                for r in rows:
                    fid = r["fact_id"]
                    if fid not in seen_fact_ids:
                        seen_fact_ids.add(fid)
                        fact_row = self.db.get_fact(fid)
                        if fact_row:
                            collected_facts.append(Fact.from_dict(fact_row))

        # Sort facts by importance descending
        collected_facts.sort(key=lambda f: f.importance, reverse=True)

        return {
            "root_entities": [e.to_dict() for e in root_entities],
            "expanded_entities": [e.to_dict() for e in expanded_entities],
            "relations": [r.to_dict() for r in collected_relations],
            "related_facts": [f.to_dict() for f in collected_facts],
        }
