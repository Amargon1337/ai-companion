"""Tests for World Model Unified Graph API & Phase 2.2 Intelligence features."""
from __future__ import annotations

import tempfile
from companion.models import Fact
from companion.memory.store import MemoryStore


def test_world_model_unified_api() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    store = MemoryStore()
    store.db.path = db_path
    store.world._ensure_user_entity()
    try:
        # 1. Create entities
        store.db.upsert_world_entity(
            {
                "entity_id": "ent_morzik",
                "name": "Морзик",
                "type": "pet",
                "importance": 0.5,
                "aliases": ["мой пёс"],
            }
        )

        # 2. Add relation and mention
        store.db.upsert_entity_relation(
            {
                "from_entity_id": "ent_user",
                "to_entity_id": "ent_morzik",
                "relation_type": "owns",
                "trust": 0.95,
            }
        )
        store.add_fact(
            Fact(
                id="f-api-1",
                fact="Морзик любит гулять в парке",
                date="2026-07-29",
                confidence=0.9,
                source="user",
                importance=0.8,
            )
        )
        store.db.add_entity_mention(
            {
                "entity_id": "ent_morzik",
                "fact_id": "f-api-1",
                "context_snippet": "Морзик любит гулять в парке",
            }
        )

        # 3. Test resolve
        resolved = store.world.resolve("мой пёс")
        assert resolved is not None
        assert resolved.entity_id == "ent_morzik"

        # 4. Test compute_importance
        new_imp = store.world.compute_importance("ent_morzik")
        assert 0.1 <= new_imp <= 1.0

        # 5. Test summary
        summary_text = store.world.summary("ent_morzik")
        assert "Морзик" in summary_text
        assert "Mentions count:" in summary_text

        # 6. Test timeline
        timeline_items = store.world.timeline("ent_morzik")
        assert len(timeline_items) >= 1
        assert timeline_items[0]["fact_id"] == "f-api-1"
        assert "гулять" in timeline_items[0]["fact_text"]

        # 7. Test check_consistency
        report = store.world.check_consistency(repair=True)
        assert report["dangling_relations"] == 0
        assert report["dangling_mentions"] == 0
        assert report["repaired"] is True
    finally:
        store.db.close()
