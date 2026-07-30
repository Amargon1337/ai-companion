"""Tests for Stage 6: Memory Hygiene Service (GC and audit)."""
import os
import tempfile

from companion.memory.governor import MemoryGovernor
from companion.memory.hygiene import MemoryHygieneService
from companion.models import Fact
from companion.storage.sqlite_db import MemoryDatabase


def test_memory_hygiene_service() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name
    try:
        db = MemoryDatabase(db_path)
        gov = MemoryGovernor(db)
        service = MemoryHygieneService(
            db=db,
            governor=gov,
            stale_days=90,
            low_activation_threshold=0.20,
            similarity_threshold=0.80,
        )

        f_stale = Fact(
            id="fact_hyg_stale",
            fact="Старый забытый факт о погоде",
            date="2024-01-01",  # > 90 days ago
            importance=3,
            confidence=0.8,
            source="msg",
        )
        f_dup_older = Fact(
            id="fact_hyg_dup_1",
            fact="Иван любит пить чай",
            date="2026-07-01",
            importance=5,
            confidence=0.8,
            source="msg",
        )
        f_dup_newer = Fact(
            id="fact_hyg_dup_2",
            fact="Иван любит пить чай утром",
            date="2026-07-28",
            importance=5,
            confidence=0.9,
            source="msg",
        )
        f_immune = Fact(
            id="fact_hyg_imm",
            fact="Моя семья живет в Москве",
            date="2020-01-01",  # > 90 days ago, but immune structurally
            importance=5,
            confidence=0.9,
            source="msg",
            meta={"category": "family"},
        )
        db.batch_insert_facts([
            f_stale.to_dict(),
            f_dup_older.to_dict(),
            f_dup_newer.to_dict(),
            f_immune.to_dict(),
        ])

        report = service.audit()
        assert report.total_facts == 4

        # Stale candidate found (f_stale), but f_immune skipped
        assert "fact_hyg_stale" in report.stale_candidates
        assert "fact_hyg_imm" not in report.stale_candidates

        # Duplicate candidate found (older merged into newer)
        assert len(report.duplicate_candidates) == 1
        assert report.duplicate_candidates[0][0] == "fact_hyg_dup_1"
        assert report.duplicate_candidates[0][1] == "fact_hyg_dup_2"

        # Check all recommendations have source="gc"
        for rec in report.recommendations:
            assert rec.source == "gc"

        # Apply recommendations via Governor
        stats = service.apply_recommendations(report)
        assert stats["submitted"] == len(report.recommendations)
        assert stats["approved"] == len(report.recommendations)

        # Check mutation log for initiator="gc"
        muts = db.list_mutations(entity_id="fact_hyg_stale")
        assert len(muts) > 0
        assert muts[0]["initiator"] == "gc"
    finally:
        if os.path.exists(db_path):
            try:
                os.unlink(db_path)
            except OSError:
                pass
