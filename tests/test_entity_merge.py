"""Tests for Entity Merge Service (Phase 2.2)."""
from __future__ import annotations

from companion.memory.entity_merge import EntityMergeService


def test_entity_merge_service(memory_store) -> None:
    store = memory_store
    # Create primary and secondary entities
    store.db.upsert_world_entity(
        {
            "entity_id": "ent_mem_1",
            "name": "Memory OS",
            "type": "project",
            "importance": 0.9,
            "aliases": ["проект"],
        }
    )
    store.db.upsert_world_entity(
        {
            "entity_id": "ent_mem_2",
            "name": "MemoryOS",
            "type": "project",
            "importance": 0.8,
            "aliases": ["мой бот"],
        }
    )

    # Attach relation to secondary
    store.db.upsert_entity_relation(
        {
            "from_entity_id": "ent_user",
            "to_entity_id": "ent_mem_2",
            "relation_type": "builds",
            "trust": 0.95,
        }
    )
    # Attach mention to secondary
    store.db.add_entity_mention(
        {
            "entity_id": "ent_mem_2",
            "fact_id": "f-merge-test",
            "context_snippet": "работа над MemoryOS",
        }
    )

    merge_svc = EntityMergeService(store.db, vector=store.vector)
    candidates = merge_svc.find_merge_candidates(min_similarity=0.8)
    assert len(candidates) >= 1
    c_names = {(c[0].name, c[1].name) for c in candidates}
    assert ("Memory OS", "MemoryOS") in c_names or ("MemoryOS", "Memory OS") in c_names

    # Execute merge
    merged = merge_svc.merge_entities("ent_mem_1", "ent_mem_2")
    assert merged.entity_id == "ent_mem_1"
    assert "MemoryOS" in merged.aliases
    assert "мой бот" in merged.aliases

    # Verify secondary is deleted
    assert store.db.get_world_entity("ent_mem_2") is None

    # Verify relations transferred to primary
    rels = store.db.list_entity_relations(to_entity_id="ent_mem_1")
    assert any(r["from_entity_id"] == "ent_user" and r["relation_type"] == "builds" for r in rels)

    # Verify audit mutation logged
    mutations = store.db.list_mutations(entity_id="ent_mem_1")
    assert any(m["action"] == "MERGE" for m in mutations)
