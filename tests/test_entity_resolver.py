"""Tests for Entity Resolver Service (Phase 2.2)."""
from __future__ import annotations

import tempfile
from companion.memory.entity_resolver import EntityResolverService
from companion.memory.store import MemoryStore


def test_entity_resolver_exact_and_alias() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    store = MemoryStore()
    store.db.path = db_path
    try:
        store.db.upsert_world_entity(
            {
                "entity_id": "ent_morzik",
                "name": "Морзик",
                "type": "pet",
                "aliases": ["мой пёс", "собака"],
            }
        )

        resolver = EntityResolverService(store.db, vector=store.vector)

        # 1. Exact name match
        res1 = resolver.resolve("Морзик")
        assert res1 is not None
        assert res1.entity_id == "ent_morzik"

        # 2. Alias match
        res2 = resolver.resolve("мой пёс")
        assert res2 is not None
        assert res2.entity_id == "ent_morzik"

        # 3. Singleton category / descriptor match
        res3 = resolver.resolve("собака")
        assert res3 is not None
        assert res3.entity_id == "ent_morzik"

        # 4. Non-existent should return None
        res4 = resolver.resolve("неизвестный кот")
        assert res4 is None
    finally:
        store.db.close()
