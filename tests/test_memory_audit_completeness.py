"""Unit tests for universal memory audit completeness across all entities (Phase 1.6)."""
from __future__ import annotations

import os
import tempfile
import json

from companion.storage.sqlite_db import MemoryDatabase
from companion.models import Fact


def test_memory_audit_completeness_all_entities() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    db = MemoryDatabase(db_path)
    try:
        # 1. Fact
        f = Fact(id="f-aud-1", fact="Python 3.14", date="2026-07-29", importance=5, confidence=0.9, source="msg", status="active")
        db.batch_insert_facts([f.to_dict()])
        db.log_mutation("f-aud-1", action="boost", reason="test", state_before={"importance": 5}, state_after={"importance": 6}, entity_type="fact")

        # 2. Belief
        db.batch_insert_beliefs([{"id": "b-aud-1", "belief": "Simplicity", "importance": 7, "status": "active", "created_at": "2026-07-29"}])
        db.log_mutation("b-aud-1", action="reinforce", reason="repeated", state_before={"importance": 7}, state_after={"importance": 8}, entity_type="belief")

        # 3. Goal
        db.upsert_goal({"goal_id": "g-aud-1", "title": "Ship Phase 1.6", "priority": 5, "status": "active"})
        db.log_mutation("g-aud-1", action="escalate", reason="urgent", state_before={"priority": 5}, state_after={"priority": 9}, entity_type="goal")

        # 4. Reflection
        db.log_mutation("r-aud-1", action="archive", reason="stale", state_before={"status": "active"}, state_after={"status": "archived"}, entity_type="reflection")

        # 5. Episode
        db.log_mutation("e-aud-1", action="consolidate", reason="summarized", state_before={"importance": 7}, state_after={"importance": 8}, entity_type="episode")

        with db._conn() as conn:
            rows = conn.execute("SELECT * FROM memory_mutation_log ORDER BY timestamp ASC").fetchall()

        assert len(rows) == 5
        types = [dict(r)["entity_type"] for r in rows]
        assert types == ["fact", "belief", "goal", "reflection", "episode"]

        # Verify state_before and state_after serialize cleanly
        d0 = dict(rows[0])
        assert json.loads(d0["state_before"]) == {"importance": 5}
        assert json.loads(d0["state_after"]) == {"importance": 6}
        assert d0["initiator"] == "governor"
    finally:
        db.close()
        try:
            os.unlink(db_path)
        except OSError:
            pass
