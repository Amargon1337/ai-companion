"""Entity Resolver Service for Phase 2.2 Entity Intelligence.

Resolves entity mentions using name, aliases, entity type, context entity IDs,
and vector embedding similarity.
"""
from __future__ import annotations

import logging
from typing import Any
from companion.models import Entity
from companion.storage.sqlite_db import MemoryDatabase

logger = logging.getLogger(__name__)

_PRONOUNS_PEOPLE_PETS = {"он", "она", "оно", "его", "ее", "её", "ему", "ей"}
_PET_DESCRIPTORS = {"собака", "пёс", "пес", "мой пёс", "мой пес", "кошка", "кот", "питомец"}
_PROJECT_DESCRIPTORS = {"бот", "мой бот", "проект", "агент", "система"}


class EntityResolverService:
    """Resolves raw text mentions or pronouns to World Model Entity instances."""

    def __init__(self, db: MemoryDatabase, vector: Any | None = None) -> None:
        self.db = db
        self.vector = vector

    def resolve(
        self,
        text: str,
        context_entity_ids: list[str] | None = None,
        expected_type: str | None = None,
    ) -> Entity | None:
        """Resolve text mention to an Entity.

        Resolution priority:
        1. Exact Name match (case-insensitive)
        2. Alias match (exact or case-insensitive in entity.aliases)
        3. Pronoun / category descriptor match against `context_entity_ids` or DB singletons
        4. Vector similarity match (if vector index is available and similarity >= 0.82)
        """
        if not text or not text.strip():
            return None
        norm = text.strip().lower()

        entities_raw = self.db.list_world_entities()
        all_entities = [Entity.from_dict(e) for e in entities_raw]

        # 1. Exact Name match
        for ent in all_entities:
            if ent.name.lower() == norm:
                if expected_type and ent.type.lower() != expected_type.lower():
                    continue
                return ent

        # 2. Alias match
        for ent in all_entities:
            alias_lower = [a.lower() for a in ent.aliases]
            if norm in alias_lower:
                if expected_type and ent.type.lower() != expected_type.lower():
                    continue
                return ent

        # 3. Pronoun & Category Descriptor Resolution (Context / Singleton check)
        if norm in _PRONOUNS_PEOPLE_PETS or norm in _PET_DESCRIPTORS or norm in _PROJECT_DESCRIPTORS:
            target_types: set[str] = set()
            if norm in _PET_DESCRIPTORS:
                target_types = {"pet", "dog", "cat", "animal"}
            elif norm in _PROJECT_DESCRIPTORS:
                target_types = {"project", "bot", "ai", "system", "app"}
            elif norm in _PRONOUNS_PEOPLE_PETS:
                target_types = {"pet", "person", "friend", "partner", "user"}

            # Check context_entity_ids first if provided
            if context_entity_ids:
                for cid in context_entity_ids:
                    for ent in all_entities:
                        if ent.entity_id == cid:
                            if not target_types or ent.type.lower() in target_types:
                                return ent

            # Singleton category heuristic (e.g. if text is 'мой пёс' and there is exactly 1 pet in DB)
            if target_types:
                matching_by_type = [
                    ent for ent in all_entities if ent.type.lower() in target_types
                ]
                if len(matching_by_type) == 1:
                    return matching_by_type[0]
                # If multiple match, return the highest importance / most recently mentioned
                if matching_by_type:
                    return matching_by_type[0]

        # 4. Vector Embedding Similarity Match (similarity >= 0.82)
        if self.vector is not None:
            try:
                # VectorIndex.search has no `min_score` argument; filter its
                # normalized result explicitly.  Entity vectors are content
                # strings, so resolve the returned content against the entity
                # representation rather than expecting a nonexistent entity_id.
                results = self.vector.search(text, top_k=3, content_type="entity")
                for res in results:
                    if float(res.get("score", 0.0)) < 0.82:
                        continue
                    content = str(res.get("content", ""))
                    for ent in all_entities:
                        if content == f"{ent.name} ({ent.type})":
                            if expected_type and ent.type.lower() != expected_type.lower():
                                continue
                            return ent
            except Exception as e:
                logger.warning("Vector search in EntityResolverService failed: %s", e)

        return None
