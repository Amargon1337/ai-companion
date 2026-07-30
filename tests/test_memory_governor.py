"""Tests for Stage 4: Memory Governor (with Policy engine & Persistence layer)."""
import os
import tempfile
import json

from companion.memory.governor import (
    ArchiveRecommendation,
    BoostRecommendation,
    DecayRecommendation,
    MemoryGovernor,
    MergeRecommendation,
)
from companion.models import Fact
from companion.storage.sqlite_db import MemoryDatabase


def test_memory_governor_basic_actions() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name
    try:
        db = MemoryDatabase(db_path)
        gov = MemoryGovernor(db)

        f1 = Fact(
            id="fact_gov_1",
            fact="Иван любит яблоки",
            date="2026-07-28",
            importance=5,
            confidence=0.8,
            source="msg",
        )
        f_immune = Fact(
            id="fact_gov_immune",
            fact="Моя семья живет в Москве",
            date="2026-07-28",
            importance=6,
            confidence=0.9,
            source="msg",
            meta={"category": "family"},
        )
        db.batch_insert_facts([f1.to_dict(), f_immune.to_dict()])

        # Boost normal fact -> increases retrieval_bias without changing permanent importance
        boost_rec = BoostRecommendation(
            fact_id="fact_gov_1",
            amount=2,
            reason="used_frequently",
            source="test",
        )
        assert gov.propose(boost_rec) is True
        fact1_meta = db.get_fact("fact_gov_1")["meta"]
        if isinstance(fact1_meta, str):
            fact1_meta = json.loads(fact1_meta)
        assert fact1_meta["retrieval_bias"] == 0.2
        assert db.get_fact("fact_gov_1")["importance"] == 5

        # Decay normal fact -> decreases retrieval_bias without changing importance
        decay_rec = DecayRecommendation(
            fact_id="fact_gov_1",
            amount=1,
            reason="unused",
            source="test",
        )
        assert gov.propose(decay_rec) is True
        fact1_meta = db.get_fact("fact_gov_1")["meta"]
        if isinstance(fact1_meta, str):
            fact1_meta = json.loads(fact1_meta)
        assert fact1_meta["retrieval_bias"] == 0.1
        assert db.get_fact("fact_gov_1")["importance"] == 5

        # Attempt to decay immune fact -> must be blocked
        immune_decay = DecayRecommendation(
            fact_id="fact_gov_immune",
            amount=2,
            reason="unused",
            source="test",
        )
        assert gov.propose(immune_decay) is False
        assert db.get_fact("fact_gov_immune")["importance"] == 6

        # Archive normal fact
        arch_rec = ArchiveRecommendation(
            fact_id="fact_gov_1",
            reason="old",
            source="test",
        )
        assert gov.propose(arch_rec) is True
        assert db.get_fact("fact_gov_1")["status"] == "archived"
        assert db.get_fact("fact_gov_1")["archived"] == 1

        # Check Mutation Log recorded all 3 approved mutations
        muts = db.list_mutations(entity_id="fact_gov_1")
        assert len(muts) == 3

        # Check initiator was recorded
        for m in muts:
            assert m["initiator"] == "test"

        # Test process_recommendations batch
        stats = gov.process_recommendations([immune_decay])
        assert stats == {"submitted": 1, "approved": 0, "rejected": 1}
    finally:
        if os.path.exists(db_path):
            try:
                os.unlink(db_path)
            except OSError:
                pass
