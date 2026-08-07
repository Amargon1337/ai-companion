"""WorldModelService - Entity-Relationship Graph & Cognitive Queries (Phase 2)."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any

from companion.models import (
    Entity,
    EntityRelation,
    Fact,
    _new_id,
)
from companion.memory.entity_resolver import EntityResolverService
from companion.memory.entity_merge import EntityMergeService
from companion.memory.graph_retriever import GraphRetrieverService
from companion.memory.graph_consistency import GraphConsistencyChecker

logger = logging.getLogger(__name__)


class WorldModelService:
    """Manages the Entity-Relationship Graph and performs cognitive queries."""

    def __init__(self, db: Any, vector: Any | None = None) -> None:
        self.db = db
        self.vector = vector
        self._resolver = EntityResolverService(db, vector=vector)
        self._merger = EntityMergeService(db, vector=vector)
        self._retriever = GraphRetrieverService(db, vector=vector)
        self._checker = GraphConsistencyChecker(db)
        self._ensure_user_entity()

    def _ensure_user_entity(self) -> None:
        """Ensure the root user entity exists."""
        try:
            if not self.db.get_world_entity("ent_user"):
                self.db.upsert_world_entity(
                    {
                        "entity_id": "ent_user",
                        "name": "Иван",
                        "type": "person",
                        "importance": 1.0,
                    }
                )
        except Exception as e:
            logger.debug(f"Could not ensure user entity: {e}")

    def extract_entities_from_fact(self, fact: Fact) -> list[dict[str, Any]]:
        """Extract entity candidates and relationship hints from a fact."""
        text = fact.fact
        results: list[dict[str, Any]] = []

        # Known domain entities & relationship patterns
        rules: list[tuple[str, str, str, str, float, float]] = [
            ("Морзик", "pet", "owns", "dog", 0.98, 0.95),
            ("Женя", "person", "friend_of", "friend", 0.85, 0.82),
            ("Memory OS", "project", "project", "AI system", 0.99, 0.90),
            ("Amargon", "project", "project", "Telegram bot", 0.90, 0.85),
            ("Аня", "person", "partner", "partner", 0.90, 0.90),
            ("Мама", "person", "family", "mother", 0.85, 0.90),
            ("Папа", "person", "family", "father", 0.85, 0.90),
        ]

        for name, ent_type, rel_type, role_attr, imp, trust in rules:
            if name.lower() in text.lower():
                results.append(
                    {
                        "name": name,
                        "type": ent_type,
                        "importance": imp,
                        "attributes": {"role": role_attr},
                        "relation": {
                            "relation_type": rel_type,
                            "trust": trust,
                            "relationship_strength": imp,
                        },
                    }
                )

        # Fallback: extract capitalized proper nouns >= 3 chars if no rule matched
        if not results:
            words = re.findall(r"\b[A-ZА-Я][a-zа-я]{2,}\b", text)
            for w in words:
                if w not in {"Иван", "Я", "Мне", "Мы", "Они", "Это", "Все", "Для"}:
                    results.append(
                        {
                            "name": w,
                            "type": "concept",
                            "importance": 0.6,
                            "attributes": {},
                            "relation": {
                                "relation_type": "related_to",
                                "trust": 0.5,
                                "relationship_strength": 0.5,
                            },
                        }
                    )

        return results

    def process_fact(self, fact: Fact, *, index_entities: bool = True) -> list[Entity]:
        """Process a fact into the World Model: match/create entities, attributes, relations, and mentions."""
        extracted = self.extract_entities_from_fact(fact)
        processed: list[Entity] = []
        now_iso = fact.date or datetime.now().isoformat()

        for ext in extracted:
            name = str(ext["name"])
            ent_type = str(ext.get("type", "concept"))
            importance = float(ext.get("importance", 0.5))

            # Match existing entity by exact name (case-insensitive)
            matches = self.db.search_world_entities_by_name(name)
            entity_id = ""
            for m in matches:
                if str(m.get("name", "")).lower() == name.lower():
                    entity_id = str(m["entity_id"])
                    break

            if entity_id:
                existing = self.db.get_world_entity(entity_id)
                new_imp = max(
                    importance, float(existing.get("importance", 0.5)) if existing else 0.5
                )
                self.db.upsert_world_entity(
                    {
                        "entity_id": entity_id,
                        "name": name,
                        "type": ent_type,
                        "importance": new_imp,
                        "last_mentioned_at": now_iso,
                    }
                )
            else:
                entity_id = _new_id("ent")
                self.db.upsert_world_entity(
                    {
                        "entity_id": entity_id,
                        "name": name,
                        "type": ent_type,
                        "importance": importance,
                        "created_at": now_iso,
                        "last_mentioned_at": now_iso,
                    }
                )

            # Record mention link
            self.db.add_entity_mention(
                {
                    "entity_id": entity_id,
                    "fact_id": fact.id,
                    "context_snippet": fact.fact[:100],
                    "created_at": now_iso,
                }
            )
            if entity_id not in fact.entity_ids:
                fact.entity_ids.append(entity_id)

            # Add attributes
            for k, v in ext.get("attributes", {}).items():
                self.db.add_entity_attribute(
                    {
                        "entity_id": entity_id,
                        "attribute_key": str(k),
                        "attribute_value": str(v),
                        "confidence": 0.85,
                        "source_fact_id": fact.id,
                        "created_at": now_iso,
                    }
                )

            # Upsert relationship to user
            rel_data = ext.get("relation")
            if rel_data and isinstance(rel_data, dict):
                self.db.upsert_entity_relation(
                    {
                        "from_entity_id": "ent_user",
                        "to_entity_id": entity_id,
                        "relation_type": str(rel_data.get("relation_type", "related_to")),
                        "trust": float(rel_data.get("trust", 0.5)),
                        "relationship_strength": float(
                            rel_data.get("relationship_strength", 0.5)
                        ),
                        "last_seen_at": now_iso,
                    }
                )

            # Vector Indexing
            if index_entities and self.vector and hasattr(self.vector, "compute_and_cache"):
                try:
                    self.vector.compute_and_cache(
                        f"{name} ({ent_type})",
                        content_type="entity",
                        fact_id=entity_id,
                    )
                except Exception as e:
                    logger.debug(f"Vector caching failed for entity {name}: {e}")

            ent_dict = self.db.get_world_entity(entity_id)
            if ent_dict:
                processed.append(Entity.from_dict(ent_dict))

        return processed

    def get_entity_graph(self, entity_id: str, depth: int = 2) -> dict[str, Any]:
        """Retrieve an entity and its connected relationships, attributes, and mentions."""
        ent_dict = self.db.get_world_entity(entity_id)
        if not ent_dict:
            return {}
        attrs = self.db.get_entity_attributes(entity_id)
        rels_from = self.db.list_entity_relations(from_entity_id=entity_id)
        mentions = self.db.get_mentions_for_entity(entity_id)
        return {
            "entity": ent_dict,
            "attributes": attrs,
            "relations": rels_from,
            "mentions": mentions,
        }

    def get_neglected_entities(self, older_than_days: int = 30) -> list[Entity]:
        """Find important entities that have not been mentioned recently (cognitive query)."""
        all_ents = self.db.list_world_entities(limit=500)
        cutoff = datetime.now() - timedelta(days=older_than_days)
        neglected: list[Entity] = []
        for d in all_ents:
            if d.get("entity_id") == "ent_user":
                continue
            last_dt_str = str(d.get("last_mentioned_at") or "")
            try:
                dt = datetime.fromisoformat(last_dt_str)
                if dt < cutoff and float(d.get("importance", 0.0)) >= 0.7:
                    neglected.append(Entity.from_dict(d))
            except (ValueError, TypeError):
                continue
        return neglected

    def query_relationships(
        self, from_entity_id: str = "ent_user", min_trust: float = 0.0
    ) -> list[EntityRelation]:
        """Retrieve relationship profiles filtered by trust threshold."""
        rows = self.db.list_entity_relations(
            from_entity_id=from_entity_id, min_trust=min_trust
        )
        return [EntityRelation.from_dict(r) for r in rows]

    def compute_importance(self, entity_id: str) -> float:
        """Compute dynamic entity importance based on mention frequency, relations, and recency."""
        raw = self.db.get_world_entity(entity_id)
        if not raw:
            return 0.5
        ent = Entity.from_dict(raw)

        mentions = self.db.get_mentions_for_entity(entity_id)
        rels = self.db.list_entity_relations(entity_id=entity_id)

        base = min(1.0, 0.4 + 0.1 * len(mentions) + 0.15 * len(rels))
        days_old = 0.0
        try:
            if ent.last_mentioned_at:
                dt = datetime.fromisoformat(ent.last_mentioned_at)
                days_old = max(0.0, (datetime.now() - dt).total_seconds() / 86400.0)
        except Exception:
            days_old = 0.0

        decay = 0.95 ** (days_old / 30.0)
        final_score = round(min(1.0, max(0.1, base * decay)), 2)

        if abs(final_score - ent.importance) >= 0.05:
            ent.importance = final_score
            self.db.upsert_world_entity(ent.to_dict(), expected_version=ent.version)

        return final_score

    def get_timeline(self, entity_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Return chronological timeline of mentions and facts for an entity."""
        mentions_raw = self.db.get_mentions_for_entity(entity_id)
        timeline_items: list[dict[str, Any]] = []

        for m in mentions_raw:
            fid = m.get("fact_id")
            fact_text = ""
            fact_imp = 0.5
            if fid:
                f_row = self.db.get_fact(str(fid))
                if f_row:
                    fact_text = str(f_row.get("fact", ""))
                    fact_imp = float(f_row.get("importance", 0.5))

            timeline_items.append(
                {
                    "timestamp": str(m.get("created_at") or ""),
                    "fact_id": str(fid or ""),
                    "snippet": str(m.get("context_snippet", "")),
                    "fact_text": fact_text,
                    "importance": fact_imp,
                    "event_type": "mention",
                }
            )

        timeline_items.sort(key=lambda x: x["timestamp"])
        return timeline_items[-limit:]

    def update_summary(self, entity_id: str) -> str:
        """Synthesize and update concise text summary for an entity."""
        raw = self.db.get_world_entity(entity_id)
        if not raw:
            return ""
        ent = Entity.from_dict(raw)
        attrs = self.db.get_entity_attributes(entity_id)
        rels = self.db.list_entity_relations(entity_id=entity_id)
        mentions = self.db.get_mentions_for_entity(entity_id)

        parts = [f"Entity '{ent.name}' ({ent.type}). Importance: {ent.importance}."]
        if attrs:
            attr_strs = [f"{a.get('attribute_key')}={a.get('attribute_value')}" for a in attrs]
            parts.append(f"Attributes: {', '.join(attr_strs)}.")
        if rels:
            rel_strs = [f"{r.get('relation_type')} ({r.get('trust', 0.5)})" for r in rels]
            parts.append(f"Relations: {', '.join(rel_strs)}.")
        parts.append(f"Mentions count: {len(mentions)}.")

        summary_text = " ".join(parts)
        if summary_text != ent.summary:
            ent.summary = summary_text
            self.db.upsert_world_entity(ent.to_dict(), expected_version=ent.version)

        return summary_text

    # --- Phase 2.2 Unified Graph API ---

    def get_entity(self, entity_id: str) -> Entity | None:
        """Retrieve Entity instance by ID."""
        raw = self.db.get_world_entity(entity_id)
        return Entity.from_dict(raw) if raw else None

    def search(self, query: str) -> list[Entity]:
        """Search entities by name or alias query."""
        rows = self.db.search_world_entities_by_name(query)
        return [Entity.from_dict(r) for r in rows]

    def neighbours(self, entity_id: str, depth: int = 1) -> dict[str, Any]:
        """Retrieve multi-hop neighbours of an entity."""
        raw = self.db.get_world_entity(entity_id)
        if not raw:
            return {}
        return self._retriever.retrieve_subgraph(str(raw.get("name", "")), depth=depth)

    def timeline(self, entity_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Retrieve chronological history of an entity."""
        return self.get_timeline(entity_id, limit=limit)

    def summary(self, entity_id: str) -> str:
        """Get or compute entity summary text."""
        return self.update_summary(entity_id)

    def related(self, entity_id: str) -> list[EntityRelation]:
        """Get all relations involving entity_id."""
        rows = self.db.list_entity_relations(entity_id=entity_id)
        return [EntityRelation.from_dict(r) for r in rows]

    def merge(self, primary_id: str, secondary_id: str) -> Entity:
        """Merge secondary entity into primary entity."""
        return self._merger.merge_entities(primary_id, secondary_id)

    def resolve(
        self,
        text: str,
        context_entity_ids: list[str] | None = None,
        expected_type: str | None = None,
    ) -> Entity | None:
        """Resolve natural language text or pronoun to an Entity."""
        return self._resolver.resolve(
            text,
            context_entity_ids=context_entity_ids,
            expected_type=expected_type,
        )

    def check_consistency(self, repair: bool = False) -> dict[str, Any]:
        """Check and optionally repair cognitive graph integrity."""
        return self._checker.check_and_repair(repair=repair)
