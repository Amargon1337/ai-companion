"""Tests for OCC and audit logging in World Model & Relationship Layer."""
from __future__ import annotations

import pytest
from companion.exceptions import ConcurrentModificationError


def test_world_entity_occ(memory_store) -> None:
    store = memory_store
    # Create entity
    store.db.upsert_world_entity(
        {
            "entity_id": "ent_occ_1",
            "name": "Морзик",
            "type": "pet",
            "importance": 0.8,
        }
    )
    ent1 = store.db.get_world_entity("ent_occ_1")
    assert ent1 is not None
    assert ent1["version"] == 1

    # Update without version -> should increment version
    store.db.upsert_world_entity(
        {
            "entity_id": "ent_occ_1",
            "name": "Морзик",
            "type": "pet",
            "importance": 0.9,
        }
    )
    ent2 = store.db.get_world_entity("ent_occ_1")
    assert ent2["version"] == 2
    assert ent2["importance"] == 0.9

    # Update with stale version -> should raise ConcurrentModificationError
    with pytest.raises(ConcurrentModificationError):
        store.db.upsert_world_entity(
            {
                "entity_id": "ent_occ_1",
                "name": "Морзик",
                "type": "pet",
                "importance": 0.95,
                "version": 1,
            }
        )

    # Confirm importance remained unchanged after failed OCC update
    ent3 = store.db.get_world_entity("ent_occ_1")
    assert ent3["version"] == 2
    assert ent3["importance"] == 0.9


def test_world_model_audit_logging(memory_store) -> None:
    store = memory_store
    store.db.upsert_world_entity(
        {
            "entity_id": "ent_audit_1",
            "name": "Аня",
            "type": "person",
            "importance": 0.9,
        }
    )
    store.db.add_entity_attribute(
        {
            "entity_id": "ent_audit_1",
            "attribute_key": "role",
            "attribute_value": "partner",
            "confidence": 0.95,
        }
    )
    store.db.upsert_entity_relation(
        {
            "from_entity_id": "ent_user",
            "to_entity_id": "ent_audit_1",
            "relation_type": "partner",
            "trust": 0.9,
        }
    )
    store.db.add_entity_mention(
        {
            "entity_id": "ent_audit_1",
            "fact_id": "f-audit-1",
            "context_snippet": "Аня — партнёр",
        }
    )

    with store.db._conn() as conn:
        rows = conn.execute(
            "SELECT table_name, action, record_id FROM audit_log ORDER BY audit_id ASC"
        ).fetchall()
        audit_events = [(r["table_name"], r["action"], r["record_id"]) for r in rows]

        assert ("entities", "INSERT", "ent_audit_1") in audit_events
        assert ("entity_attributes", "INSERT", "ent_audit_1:role") in audit_events
        assert ("entity_relations", "INSERT", "ent_user:ent_audit_1:partner") in audit_events
        assert ("entity_mentions", "INSERT", "f-audit-1:ent_audit_1") in audit_events
