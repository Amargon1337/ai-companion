"""Tests for Self-Maintaining Cognitive Architecture:
  - Epistemic layer separation (evidence / inference / narrative)
  - Memory Health Score computation
  - Review Queue generation
  - Cascade invalidation
"""
from __future__ import annotations

import pytest
from companion.config import EMBEDDING_DIM


def _mock_vec():
    vec = [0.0] * EMBEDDING_DIM
    vec[0] = 1.0
    return vec


# ============================================================================
# Epistemic Layer Classification
# ============================================================================

class TestEpistemicLayers:
    """Verify facts are correctly classified into evidence/inference layers."""

    def test_direct_fact_is_evidence(self):
        """A DIRECT_FACT should be classified as evidence."""
        from companion.memory.epistemic_layers import classify_fact, EpistemicLayer
        from companion.models import Fact

        fact = Fact(
            fact="Иван сказал что ему 25 лет",
            date="2026-01-01",
            importance=7,
            confidence=0.9,
            source="test",
            meta={"epistemic_class": "DIRECT_FACT"},
        )
        assert classify_fact(fact) == EpistemicLayer.EVIDENCE

    def test_llm_inference_is_inference(self):
        """An LLM_INFERENCE should be classified as inference."""
        from companion.memory.epistemic_layers import classify_fact, EpistemicLayer
        from companion.models import Fact

        fact = Fact(
            fact="Иван использует сигареты чтобы справляться со стрессом",
            date="2026-01-01",
            importance=6,
            confidence=0.7,
            source="compress",
            epistemic_class="LLM_INFERENCE",
            meta={"epistemic_class": "LLM_INFERENCE"},  # stored in meta for DB roundtrip
        )
        assert classify_fact(fact) == EpistemicLayer.INFERENCE

    def test_hedging_text_detected_as_inference(self):
        """Text with hedging markers should be classified as inference."""
        from companion.memory.epistemic_layers import _looks_like_inference

        assert _looks_like_inference("Иван всегда опаздывает")
        assert _looks_like_inference("Он обычно избегает конфликтов")
        assert _looks_like_inference("Кажется, ему нравится одиночество")
        assert not _looks_like_inference("Иван купил хлеб")
        assert not _looks_like_inference("Морзик — золотистый ретривер")

    def test_layer_audit_detects_orphaned_inferences(self, tmp_path, monkeypatch):
        """Audit should detect inferences with all evidence invalidated."""
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test.db"))

        from companion.memory.store import MemoryStore
        from companion.memory.epistemic_layers import audit_epistemic_layers
        from companion.models import Fact

        store = MemoryStore()
        monkeypatch.setattr(store.vector, 'embed_text_only', lambda text: _mock_vec())

        # Create evidence fact
        evidence = Fact(
            id="f-ev-1", fact="Иван курит", date="2026-01-01",
            importance=6, confidence=0.8, source="test",
            epistemic_class="DIRECT_FACT",
            meta={"epistemic_class": "DIRECT_FACT"},
        )
        store.add_fact(evidence)

        # Create inference that depends on evidence
        inference = Fact(
            id="f-inf-1",
            fact="Иван курит чтобы справляться со стрессом",
            date="2026-01-01", importance=5, confidence=0.7,
            source="compress", epistemic_class="LLM_INFERENCE",
            evidence=["f-ev-1"],
            meta={"epistemic_class": "LLM_INFERENCE"},
        )
        store.add_fact(inference)

        # Archive the evidence
        store.archive_fact("f-ev-1")

        # Audit should find orphaned inference
        audit = audit_epistemic_layers(store)
        assert "f-inf-1" in audit.orphaned_inferences, \
            "Inference with archived evidence should be orphaned"
        store.close()


# ============================================================================
# Cascade Invalidation
# ============================================================================

class TestCascadeInvalidation:
    """When evidence dies, inferences must be re-evaluated."""

    def test_cascade_reduces_confidence(self, tmp_path, monkeypatch):
        """When evidence is archived, dependent inference loses confidence."""
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test.db"))

        from companion.memory.store import MemoryStore
        from companion.memory.epistemic_layers import cascade_invalidation
        from companion.models import Fact

        store = MemoryStore()
        monkeypatch.setattr(store.vector, 'embed_text_only', lambda text: _mock_vec())

        # Evidence
        ev = Fact(id="f-casc-ev", fact="Иван пьёт кофе", date="2026-01-01",
                  importance=5, confidence=0.9, source="test",
                  epistemic_class="DIRECT_FACT",
                  meta={"epistemic_class": "DIRECT_FACT"})
        store.add_fact(ev)

        # Inference depending on evidence
        inf = Fact(id="f-casc-inf",
                   fact="Иван пьёт кофе чтобы не засыпать на работе",
                   date="2026-01-01", importance=5, confidence=0.8,
                   source="compress", epistemic_class="LLM_INFERENCE",
                   evidence=["f-casc-ev"],
                   meta={"epistemic_class": "LLM_INFERENCE"})
        store.add_fact(inf)

        # Archive evidence → cascade
        store.archive_fact("f-casc-ev")
        result = cascade_invalidation(store, "f-casc-ev", reason="fact archived")

        assert len(result.directly_affected) > 0
        assert "f-casc-inf" in result.directly_affected

        # Check inference confidence was reduced
        updated_inf = store.get_fact("f-casc-inf")
        assert updated_inf.confidence < 0.8, \
            f"Inference confidence should drop after evidence loss (got {updated_inf.confidence})"
        store.close()

    def test_cascade_marks_baseless_as_pending_review(self, tmp_path, monkeypatch):
        """When ALL evidence is gone, inference goes to pending_review."""
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test.db"))

        from companion.memory.store import MemoryStore
        from companion.memory.epistemic_layers import cascade_invalidation
        from companion.models import Fact

        store = MemoryStore()
        monkeypatch.setattr(store.vector, 'embed_text_only', lambda text: _mock_vec())

        ev = Fact(id="f-all-ev", fact="Тестовый факт", date="2026-01-01",
                  importance=5, confidence=0.9, source="test",
                  epistemic_class="DIRECT_FACT",
                  meta={"epistemic_class": "DIRECT_FACT"})
        store.add_fact(ev)

        inf = Fact(id="f-all-inf", fact="Вывод на основе тестового факта",
                   date="2026-01-01", importance=5, confidence=0.8,
                   source="compress", epistemic_class="LLM_INFERENCE",
                   evidence=["f-all-ev"],
                   meta={"epistemic_class": "LLM_INFERENCE"})
        store.add_fact(inf)

        # Archive the only evidence
        store.archive_fact("f-all-ev")
        cascade_invalidation(store, "f-all-ev")

        # Inference should be pending_review
        updated = store.get_fact("f-all-inf")
        assert updated.status == "pending_review", \
            f"Inference with no evidence should be pending_review (got {updated.status})"
        store.close()


# ============================================================================
# Memory Health Score
# ============================================================================

class TestHealthScore:
    """Verify composite health score computation."""

    def test_healthy_fact_scores_high(self, tmp_path, monkeypatch):
        """A well-supported, recent, verified fact should score high."""
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test.db"))

        from companion.memory.store import MemoryStore
        from companion.memory.health_score import compute_health
        from companion.models import Fact
        from datetime import datetime

        store = MemoryStore()
        monkeypatch.setattr(store.vector, 'embed_text_only', lambda text: _mock_vec())

        fact = Fact(
            id="f-healthy",
            fact="Иван любит Python",
            date=datetime.now().strftime("%Y-%m-%d"),
            importance=8,
            confidence=0.95,
            source="explicit",
            source_type="explicit",
            epistemic_class="DIRECT_FACT",
            tags=["core_identity"],
            meta={"verification_status": "verified", "epistemic_class": "DIRECT_FACT"},
        )
        store.add_fact(fact)

        health = compute_health(store, fact)
        assert health.overall > 0.7, f"Healthy fact should score >0.7 (got {health.overall})"
        assert health.status == "healthy"
        store.close()

    def test_unverified_inference_scores_low(self, tmp_path, monkeypatch):
        """An inference with no evidence and low verification should score low."""
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test.db"))

        from companion.memory.store import MemoryStore
        from companion.memory.health_score import compute_health
        from companion.models import Fact

        store = MemoryStore()
        monkeypatch.setattr(store.vector, 'embed_text_only', lambda text: _mock_vec())

        fact = Fact(
            id="f-weak-inf",
            fact="Иван ненавидит свою работу",
            date="2024-01-01",  # Old
            importance=4,
            confidence=0.4,     # Low
            source="compress",
            source_type="compress",
            epistemic_class="LLM_INFERENCE",
            evidence=[],         # No evidence
            meta={"verification_status": "mismatch", "epistemic_class": "LLM_INFERENCE"},
        )
        store.add_fact(fact)

        health = compute_health(store, fact)
        assert health.overall < 0.55, f"Weak inference should score <0.55 (got {health.overall})"
        assert len(health.reasons) > 0, "Should have reasons for low score"
        store.close()

    def test_contradicted_fact_scores_lower(self, tmp_path, monkeypatch):
        """A fact with contradictions should score lower."""
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test.db"))

        from companion.memory.store import MemoryStore
        from companion.memory.health_score import compute_health
        from companion.models import Fact

        store = MemoryStore()
        monkeypatch.setattr(store.vector, 'embed_text_only', lambda text: _mock_vec())

        # Fact with many contradictions
        fact = Fact(
            id="f-contra",
            fact="Contested fact",
            date="2026-06-01",
            importance=5,
            confidence=0.7,
            source="test",
            epistemic_class="DIRECT_FACT",
            contradiction_count=5,
            support_count=1,
            meta={"epistemic_class": "DIRECT_FACT"},
        )
        store.add_fact(fact)

        health = compute_health(store, fact)
        assert health.components["contradiction"] <= 0.5, \
            "Contradiction score should be low when contradictions > support"
        store.close()


# ============================================================================
# Review Queue
# ============================================================================

class TestReviewQueue:
    """Verify the automatic review queue works correctly."""

    def test_review_queue_finds_weak_facts(self, tmp_path, monkeypatch):
        """Facts below threshold should appear in review queue."""
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test.db"))

        from companion.memory.store import MemoryStore
        from companion.memory.health_score import scan_review_queue
        from companion.models import Fact

        store = MemoryStore()
        monkeypatch.setattr(store.vector, 'embed_text_only', lambda text: _mock_vec())

        # Weak fact (no evidence, low confidence, compress source)
        weak = Fact(
            id="f-review-weak",
            fact="Weak unverified fact",
            date="2024-01-01",
            importance=3,
            confidence=0.3,
            source="compress",
            source_type="compress",
            epistemic_class="LLM_INFERENCE",
            evidence=[],
            meta={"verification_status": "mismatch", "epistemic_class": "LLM_INFERENCE"},
        )
        store.add_fact(weak)

        queue = scan_review_queue(store, threshold=0.6)  # Higher threshold to catch it
        fact_ids = [item.fact_id for item in queue]
        assert "f-review-weak" in fact_ids, f"Weak fact should be in review queue (queue: {fact_ids})"
        store.close()

    def test_immune_facts_excluded_from_queue(self, tmp_path, monkeypatch):
        """Protected facts should NOT appear in review queue."""
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test.db"))

        from companion.memory.store import MemoryStore
        from companion.memory.health_score import scan_review_queue
        from companion.models import Fact

        store = MemoryStore()
        monkeypatch.setattr(store.vector, 'embed_text_only', lambda text: _mock_vec())

        # Protected fact (importance 9, core_identity tag)
        protected = Fact(
            id="f-review-protected",
            fact="Ивану 25 лет",
            date="2026-01-01",
            importance=9,
            confidence=0.3,  # Low confidence but protected
            source="test",
            tags=["core_identity"],
        )
        store.add_fact(protected)

        queue = scan_review_queue(store, threshold=0.5)
        fact_ids = [item.fact_id for item in queue]
        assert "f-review-protected" not in fact_ids, \
            "Immune fact should NOT be in review queue"
        store.close()

    def test_health_report_structure(self, tmp_path, monkeypatch):
        """memory_health_report should return proper structure."""
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test.db"))

        from companion.memory.store import MemoryStore
        from companion.memory.health_score import memory_health_report
        from companion.models import Fact

        store = MemoryStore()
        monkeypatch.setattr(store.vector, 'embed_text_only', lambda text: _mock_vec())

        fact = Fact(
            id="f-report", fact="Test fact for report",
            date="2026-08-01", importance=5, confidence=0.8,
            source="test",
        )
        store.add_fact(fact)

        report = memory_health_report(store)
        assert "total_active_facts" in report
        assert "average_health" in report
        assert "health_distribution" in report
        assert "epistemic_layers" in report
        assert report["total_active_facts"] >= 1
        store.close()
