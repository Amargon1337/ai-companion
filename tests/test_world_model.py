"""Unit tests for Phase 2 World Model & Relationship Layer."""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import patch

from companion.memory.store import MemoryStore
from companion.models import Fact


def test_entity_creation_and_matching() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    store = MemoryStore()
    store.db.path = db_path
    try:
        # Fact 1 mentions Морзик
        f1 = Fact(
            id="f-world-1",
            fact="У Ивана есть собака Морзик",
            date="2026-07-10",
            importance=8,
            confidence=0.9,
            source="test",
            status="active",
        )
        store.add_fact(f1)

        matches = store.db.search_world_entities_by_name("Морзик")
        assert len(matches) == 1
        morzik_ent = matches[0]
        assert morzik_ent["name"] == "Морзик"
        assert morzik_ent["type"] == "pet"
        assert len(f1.entity_ids) == 1
        assert f1.entity_ids[0] == morzik_ent["entity_id"]

        # Fact 2 mentions Морзик again -> should match existing entity ID
        f2 = Fact(
            id="f-world-2",
            fact="Морзик любит играть с мячом",
            date="2026-07-15",
            importance=7,
            confidence=0.9,
            source="test",
            status="active",
        )
        store.add_fact(f2)

        matches_after = store.db.search_world_entities_by_name("Морзик")
        assert len(matches_after) == 1
        assert matches_after[0]["entity_id"] == morzik_ent["entity_id"]
        assert matches_after[0]["last_mentioned_at"] == "2026-07-15"
        assert f2.entity_ids[0] == morzik_ent["entity_id"]

        # Check mentions table
        mentions = store.db.get_mentions_for_entity(morzik_ent["entity_id"])
        assert len(mentions) == 2
        fact_ids_mentioned = {m["fact_id"] for m in mentions}
        assert "f-world-1" in fact_ids_mentioned
        assert "f-world-2" in fact_ids_mentioned
    finally:
        store.db.close()


def test_relationship_layer() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    store = MemoryStore()
    store.db.path = db_path
    try:
        f = Fact(
            id="f-world-rel-1",
            fact="Женя мой хороший друг",
            date="2026-07-12",
            importance=8,
            confidence=0.9,
            source="test",
            status="active",
        )
        store.add_fact(f)

        rels = store.world_model.query_relationships(from_entity_id="ent_user", min_trust=0.8)
        assert len(rels) >= 1
        zhenya_rels = [r for r in rels if r.relation_type == "friend_of"]
        assert len(zhenya_rels) == 1
        assert zhenya_rels[0].trust == 0.82
    finally:
        store.db.close()


def test_cognitive_queries_neglected_entities() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    store = MemoryStore()
    store.db.path = db_path
    try:
        now_dt = datetime.now()
        old_iso = (now_dt - timedelta(days=40)).isoformat()
        recent_iso = (now_dt - timedelta(days=5)).isoformat()

        # Insert old neglected entity with high importance
        store.db.upsert_world_entity(
            {
                "entity_id": "ent_neglected",
                "name": "Старый Проект",
                "type": "project",
                "importance": 0.95,
                "created_at": old_iso,
                "last_mentioned_at": old_iso,
            }
        )

        # Insert recent entity
        store.db.upsert_world_entity(
            {
                "entity_id": "ent_recent",
                "name": "Новый Проект",
                "type": "project",
                "importance": 0.95,
                "created_at": recent_iso,
                "last_mentioned_at": recent_iso,
            }
        )

        neglected = store.world_model.get_neglected_entities(older_than_days=30)
        neglected_ids = {e.entity_id for e in neglected}
        assert "ent_neglected" in neglected_ids
        assert "ent_recent" not in neglected_ids
    finally:
        store.db.close()


def test_get_entity_graph() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    store = MemoryStore()
    store.db.path = db_path
    try:
        f = Fact(
            id="f-world-graph-1",
            fact="У Ивана есть собака Морзик",
            date="2026-07-20",
            importance=8,
            confidence=0.9,
            source="test",
            status="active",
        )
        store.add_fact(f)

        matches = store.db.search_world_entities_by_name("Морзик")
        assert len(matches) == 1
        morzik_id = matches[0]["entity_id"]

        graph = store.world_model.get_entity_graph(morzik_id)
        assert graph["entity"]["name"] == "Морзик"
        assert len(graph["attributes"]) >= 1
        assert graph["attributes"][0]["attribute_key"] == "role"
        assert len(graph["mentions"]) == 1
        assert graph["mentions"][0]["fact_id"] == "f-world-graph-1"
    finally:
        store.db.close()
