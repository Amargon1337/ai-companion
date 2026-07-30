"""Tests for Stage 5: Mutation Log."""
import os
import tempfile

from companion.storage.sqlite_db import MemoryDatabase


def test_memory_mutation_log() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name
    try:
        db = MemoryDatabase(db_path)

        mut_id = db.log_mutation(
            entity_id="fact_123",
            action="DECAY",
            reason="retrieved_but_unused",
            state_before={"importance": 5},
            state_after={"importance": 4},
            initiator="feedback_loop",
        )
        assert mut_id.startswith("mut_")

        mutations = db.list_mutations(entity_id="fact_123")
        assert len(mutations) == 1
        assert mutations[0]["id"] == mut_id
        assert mutations[0]["action"] == "DECAY"
        assert mutations[0]["reason"] == "retrieved_but_unused"
        assert mutations[0]["state_before"] == {"importance": 5}
        assert mutations[0]["state_after"] == {"importance": 4}
        assert mutations[0]["initiator"] == "feedback_loop"

        # Query without entity_id
        all_mutations = db.list_mutations()
        assert len(all_mutations) == 1
    finally:
        if os.path.exists(db_path):
            try:
                os.unlink(db_path)
            except OSError:
                pass
