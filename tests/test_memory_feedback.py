"""Tests for Stage 3: Memory Feedback Loop (retrieval_bias adjustments)."""
import os
import tempfile
import json

from companion.memory.feedback import MemoryFeedbackLoop
from companion.memory.governor import BoostRecommendation, DecayRecommendation, MemoryGovernor
from companion.models import Fact
from companion.storage.sqlite_db import MemoryDatabase


def test_memory_feedback_loop() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name
    try:
        db = MemoryDatabase(db_path)
        gov = MemoryGovernor(db)
        loop = MemoryFeedbackLoop(
            db=db,
            governor=gov,
            min_retrieved=10,
            low_precision_threshold=0.15,
            high_usage_threshold=5,
        )

        f_low_prec = Fact(
            id="fact_fb_low",
            fact="Редко используемый факт про погоду",
            date="2026-07-28",
            importance=5,
            confidence=0.8,
            source="msg",
            facts_sent_count=20,
            facts_used_count=1,  # precision = 0.05 (< 0.15)
        )
        f_high_use = Fact(
            id="fact_fb_high",
            fact="Часто используемый факт про проект",
            date="2026-07-28",
            importance=6,
            confidence=0.9,
            source="msg",
            facts_sent_count=10,
            facts_used_count=7,  # used >= 5
        )
        f_immune = Fact(
            id="fact_fb_imm",
            fact="Моя семья живет в Москве",
            date="2026-07-28",
            importance=6,
            confidence=0.9,
            source="msg",
            facts_sent_count=20,
            facts_used_count=0,  # low precision, but immune structurally
            meta={"category": "family"},
        )
        db.batch_insert_facts([f_low_prec.to_dict(), f_high_use.to_dict(), f_immune.to_dict()])

        recs = loop.analyze()
        rec_map = {r.fact_id: r for r in recs}

        assert "fact_fb_low" in rec_map
        assert isinstance(rec_map["fact_fb_low"], DecayRecommendation)
        assert rec_map["fact_fb_low"].source == "feedback_loop"

        assert "fact_fb_high" in rec_map
        assert isinstance(rec_map["fact_fb_high"], BoostRecommendation)
        assert rec_map["fact_fb_high"].source == "feedback_loop"

        # Immune fact must be ignored by feedback loop
        assert "fact_fb_imm" not in rec_map

        # Run cycle through Governor
        stats = loop.run_cycle()
        assert stats["submitted"] == 2
        assert stats["approved"] == 2

        # Check retrieval_bias changed, NOT permanent importance
        low_meta = db.get_fact("fact_fb_low")["meta"]
        if isinstance(low_meta, str):
            low_meta = json.loads(low_meta)
        assert low_meta["retrieval_bias"] == -0.1
        assert db.get_fact("fact_fb_low")["importance"] == 5

        high_meta = db.get_fact("fact_fb_high")["meta"]
        if isinstance(high_meta, str):
            high_meta = json.loads(high_meta)
        assert high_meta["retrieval_bias"] == 0.1
        assert db.get_fact("fact_fb_high")["importance"] == 6

        # Check initiator in Mutation Log
        muts = db.list_mutations(entity_id="fact_fb_high")
        assert len(muts) > 0
        assert muts[0]["initiator"] == "feedback_loop"
    finally:
        if os.path.exists(db_path):
            try:
                os.unlink(db_path)
            except OSError:
                pass
