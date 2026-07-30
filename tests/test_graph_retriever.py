"""Tests for Graph Retriever Service (Phase 2.2)."""
from __future__ import annotations

import tempfile
from companion.memory.graph_retriever import GraphRetrieverService
from companion.memory.store import MemoryStore


def test_multi_hop_subgraph_retrieval() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    store = MemoryStore()
    store.db.path = db_path
    try:
        # Create 3 chained entities: Memory OS -> Phase 2 -> Prediction Engine
        store.db.upsert_world_entity(
            {"entity_id": "ent_root", "name": "Memory OS", "type": "project", "importance": 0.95}
        )
        store.db.upsert_world_entity(
            {"entity_id": "ent_hop1", "name": "Phase 2", "type": "milestone", "importance": 0.9}
        )
        store.db.upsert_world_entity(
            {"entity_id": "ent_hop2", "name": "Prediction Engine", "type": "component", "importance": 0.85}
        )

        # Connect relations
        store.db.upsert_entity_relation(
            {"from_entity_id": "ent_root", "to_entity_id": "ent_hop1", "relation_type": "includes", "trust": 0.9}
        )
        store.db.upsert_entity_relation(
            {"from_entity_id": "ent_hop1", "to_entity_id": "ent_hop2", "relation_type": "enables", "trust": 0.85}
        )

        # Add mentions to facts
        store.db.add_entity_mention(
            {"entity_id": "ent_root", "fact_id": "f-root-1", "context_snippet": "работаем над Memory OS"}
        )
        store.db.add_entity_mention(
            {"entity_id": "ent_hop1", "fact_id": "f-hop1-1", "context_snippet": "Phase 2 важный этап"}
        )
        store.db.add_entity_mention(
            {"entity_id": "ent_hop2", "fact_id": "f-hop2-1", "context_snippet": "Prediction Engine будет предсказывать будущее"}
        )

        # Add dummy facts to DB so get_fact succeeds
        from companion.models import Fact
        for fid, text in [
            ("f-root-1", "работаем над Memory OS"),
            ("f-hop1-1", "Phase 2 важный этап"),
            ("f-hop2-1", "Prediction Engine будет предсказывать будущее"),
        ]:
            store.add_fact(
                Fact(
                    id=fid,
                    fact=text,
                    date="2026-07-29",
                    confidence=0.9,
                    source="user",
                    importance=0.8,
                )
            )

        retriever = GraphRetrieverService(store.db, vector=store.vector)

        # 1. Depth=1 retrieval should only reach Phase 2
        subgraph_d1 = retriever.retrieve_subgraph("Memory OS", depth=1, min_trust=0.5)
        assert len(subgraph_d1["root_entities"]) >= 1
        d1_names = {e["name"] for e in subgraph_d1["expanded_entities"]}
        assert "Phase 2" in d1_names
        assert "Prediction Engine" not in d1_names

        # 2. Depth=2 multi-hop retrieval should reach Prediction Engine
        subgraph_d2 = retriever.retrieve_subgraph("Memory OS", depth=2, min_trust=0.5)
        d2_names = {e["name"] for e in subgraph_d2["expanded_entities"]}
        assert "Phase 2" in d2_names
        assert "Prediction Engine" in d2_names
        fact_ids = {f["id"] for f in subgraph_d2["related_facts"]}
        assert "f-root-1" in fact_ids
        assert "f-hop1-1" in fact_ids
        assert "f-hop2-1" in fact_ids
    finally:
        store.db.close()
