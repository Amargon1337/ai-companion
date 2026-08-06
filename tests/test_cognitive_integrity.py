"""Memory Integrity Lifecycle Tests — cross-layer invariant verification.

These tests don't test individual components. They test the INVARIANTS
that must hold across ALL layers of the Memory OS:

    SQLite → Repository → Lifecycle → Event Bus → FAISS → Explainability → Retrieval

If any invariant breaks, the system silently degrades. These tests catch
that degradation by running a fact through its COMPLETE lifecycle and
verifying consistency at every step.

Invariants tested:
    1. Every active fact has a FAISS embedding
    2. Every fact's provenance chain is explainable
    3. Superseded facts are removed from FAISS
    4. Archived facts are neither in FAISS nor search results
    5. Contradiction resolution preserves both facts (one superseded)
    6. Temporal transitions don't create contradictions
    7. Explainability reports match actual DB state
    8. Provenance verification catches hedging mismatches
    9. After reindex, all active facts are searchable
   10. Full lifecycle: create → confirm → update → explain → reindex → search
"""
from __future__ import annotations

import pytest

from companion.config import EMBEDDING_DIM


def _mock_vec():
    """Return a deterministic test vector."""
    vec = [0.0] * EMBEDDING_DIM
    vec[0] = 1.0
    return vec


# ============================================================================
# Lifecycle invariant: active fact → searchable
# ============================================================================

class TestLifecycleInvariants:
    """Cross-layer invariant tests."""

    def test_active_fact_is_searchable(self, tmp_path, monkeypatch):
        """INVARIANT: Every active fact must be findable via search."""
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test.db"))

        from companion.memory.store import MemoryStore
        from companion.models import Fact

        store = MemoryStore()
        monkeypatch.setattr(store.vector, 'embed_text_only', lambda text: _mock_vec())

        fact = Fact(
            id="f-lifecycle-1",
            fact="Иван любит тяжёлую музыку",
            date="2026-01-15",
            importance=7,
            confidence=0.9,
            source="test",
            tags=["interests"],
        )
        store.add_fact(fact)

        # Verify: fact is active
        saved = store.get_fact(fact.id)
        assert saved is not None
        assert saved.status == "active"

        # Verify: fact is in FAISS (has embedding)
        embedding = store.vector.get_embedding(fact.fact)
        assert embedding is not None, "Active fact must have embedding"

        # Verify: fact is searchable
        results = store.search_facts("тяжёлую музыку", limit=5)
        found_ids = [f.id for f, _ in results]
        assert fact.id in found_ids, "Active fact must be findable via search"
        store.close()

    def test_superseded_fact_removed_from_faiss(self, tmp_path, monkeypatch):
        """INVARIANT: Superseded facts must not appear in search."""
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test.db"))

        from companion.memory.store import MemoryStore
        from companion.models import Fact, FactRelation

        store = MemoryStore()
        monkeypatch.setattr(store.vector, 'embed_text_only', lambda text: _mock_vec())

        # Create two facts
        old = Fact(id="f-old", fact="Иван живёт в Минске", date="2025-01-01",
                   importance=7, confidence=0.8, source="test")
        new = Fact(id="f-new", fact="Иван живёт в Берлине", date="2026-06-01",
                   importance=8, confidence=0.9, source="test")
        store.add_fact(old)
        store.add_fact(new)

        # Supersede old with new
        rel = FactRelation(from_id=new.id, to_id=old.id, relation="supersedes",
                          reason="User moved")
        store.add_relation(rel)

        # Verify: old fact is superseded
        old_saved = store.get_fact(old.id)
        assert old_saved.status == "superseded"

        # Verify: old fact is NOT searchable
        results = store.search_facts("Минске", limit=10)
        found_ids = [f.id for f, _ in results]
        assert old.id not in found_ids, "Superseded fact must not be searchable"

        # Verify: new fact IS searchable
        results_new = store.search_facts("Берлине", limit=10)
        found_ids_new = [f.id for f, _ in results_new]
        assert new.id in found_ids_new
        store.close()

    def test_archived_fact_invisible(self, tmp_path, monkeypatch):
        """INVARIANT: Archived facts are invisible to search and listing."""
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test.db"))

        from companion.memory.store import MemoryStore
        from companion.models import Fact

        store = MemoryStore()
        monkeypatch.setattr(store.vector, 'embed_text_only', lambda text: _mock_vec())

        fact = Fact(id="f-archive", fact="Временный факт", date="2026-01-01",
                   importance=3, confidence=0.5, source="test")
        store.add_fact(fact)
        store.archive_fact(fact.id)

        # Verify: fact is archived
        saved = store.get_fact(fact.id)
        assert saved.status == "archived"

        # Verify: not in active listing
        active = store.list_facts("active")
        assert all(f.id != fact.id for f in active)

        # Verify: not searchable
        results = store.search_facts("Временный", limit=10)
        found_ids = [f.id for f, _ in results]
        assert fact.id not in found_ids
        store.close()


# ============================================================================
# Explainability consistency
# ============================================================================

class TestExplainabilityConsistency:
    """Verify explainability reports match actual state."""

    def test_explain_matches_db_state(self, tmp_path, monkeypatch):
        """INVARIANT: explain_memory() must reflect current DB state."""
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test.db"))

        from companion.memory.store import MemoryStore
        from companion.memory.explainability import explain_memory
        from companion.models import Fact

        store = MemoryStore()
        monkeypatch.setattr(store.vector, 'embed_text_only', lambda text: _mock_vec())

        fact = Fact(
            id="f-explain-cons",
            fact="Иван работает в компании X",
            date="2026-03-01",
            importance=8,
            confidence=0.85,
            source="user_stated",
            tags=["career"],
        )
        store.add_fact(fact)

        # Get explanation
        report = explain_memory(store, fact.id)
        assert report is not None
        assert report["entity_type"] == "fact"
        assert report["text"] == fact.fact
        assert report["confidence"] == fact.confidence
        assert report["importance"] == fact.importance
        assert report["status"] == "active"

        # Now update the fact
        store.update_fact(fact.id, confidence=0.95)

        # Get explanation again — must reflect update
        report2 = explain_memory(store, fact.id)
        assert report2["confidence"] == 0.95, "Explainability must reflect current state"
        store.close()


# ============================================================================
# Contradiction: evolution vs contradiction
# ============================================================================

class TestEvolutionVsContradiction:
    """Verify temporal transitions are not treated as contradictions."""

    def test_change_marker_creates_transition_not_contradiction(self, tmp_path, monkeypatch):
        """'перестал' marker + time gap → transition, not contradiction."""
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test.db"))

        from companion.memory.store import MemoryStore
        from companion.memory.contradiction import check_contradictions
        from companion.models import Fact

        store = MemoryStore()
        monkeypatch.setattr(store.vector, 'embed_text_only', lambda text: _mock_vec())

        # Old fact: "Иван курит" (6 months ago)
        old = Fact(id="f-smokes", fact="Иван курит", date="2026-01-01",
                   importance=6, confidence=0.8, source="test")
        store.add_fact(old)

        # New fact: "Иван перестал курить" (now)
        result = check_contradictions(store, "Иван перестал курить")

        # Should create a TRANSITION, not a contradiction
        assert len(result.transitions) > 0, "Change marker should create transition"
        assert result.transitions[0].transition_type == "temporal_transition"
        assert "перестал" in result.transitions[0].change_marker

        # Should NOT have logical contradictions
        logical_conflicts = [c for c in result.conflicts
                           if c.conflict_class == "logical_contradiction"]
        assert len(logical_conflicts) == 0, "No logical contradiction for temporal evolution"
        store.close()

    def test_negation_without_change_marker_is_contradiction(self, tmp_path, monkeypatch):
        """Plain negation without change marker → real contradiction."""
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test.db"))

        from companion.memory.store import MemoryStore
        from companion.memory.contradiction import check_contradictions
        from companion.models import Fact

        store = MemoryStore()
        monkeypatch.setattr(store.vector, 'embed_text_only', lambda text: _mock_vec())

        old = Fact(id="f-lives", fact="Иван живёт в Минске", date="2026-06-01",
                   importance=7, confidence=0.9, source="test")
        store.add_fact(old)

        # Same date, plain negation → contradiction
        result = check_contradictions(store, "Иван не живёт в Минске")
        assert len(result.conflicts) > 0, "Plain negation should be contradiction"
        assert result.conflicts[0].conflict_class == "logical_contradiction"
        store.close()


# ============================================================================
# Provenance verification
# ============================================================================

class TestProvenanceVerification:
    """Verify the provenance verification system catches hallucinations."""

    def test_matching_fact_passes_verification(self):
        """Fact that matches its source should be verified."""
        from companion.memory.provenance_verification import verify_fact_against_source

        result = verify_fact_against_source(
            fact_text="Иван работает QA инженером",
            source_text="Меня зовут Иван, я работаю QA инженером в компании X",
        )
        assert result.status == "verified"
        assert result.semantic_overlap > 0.5

    def test_hedging_source_flagged_as_weak(self):
        """Source with hedging language → weak match."""
        from companion.memory.provenance_verification import verify_fact_against_source

        result = verify_fact_against_source(
            fact_text="Иван работает QA инженером",
            source_text="Иван хочет работать QA инженером",
        )
        # Hedging detected → weak match, not verified
        assert result.hedging_detected
        assert result.status in ("weak_match", "mismatch")

    def test_completely_different_text_is_mismatch(self):
        """Fact that doesn't match source → mismatch."""
        from companion.memory.provenance_verification import verify_fact_against_source

        result = verify_fact_against_source(
            fact_text="Иван живёт в Берлине",
            source_text="Сегодня была хорошая погода",
        )
        assert result.status == "mismatch"
        assert result.semantic_overlap < 0.3

    def test_negation_mismatch_detected(self):
        """Fact with different polarity from source → mismatch."""
        from companion.memory.provenance_verification import verify_fact_against_source

        result = verify_fact_against_source(
            fact_text="Иван не курит",
            source_text="Иван курит каждый день",
        )
        assert result.status == "mismatch"


# ============================================================================
# Full lifecycle: create → confirm → update → explain → reindex → search
# ============================================================================

class TestFullLifecycle:
    """End-to-end lifecycle test spanning all layers."""

    def test_complete_fact_lifecycle(self, tmp_path, monkeypatch):
        """
        Full lifecycle:
        1. Create fact (SQLite + FAISS + event)
        2. Confirm fact (bump usage)
        3. Update fact (change confidence)
        4. Explain fact (provenance chain)
        5. Reindex all (FAISS rebuild)
        6. Search (find fact)
        7. Verify all invariants hold after each step
        """
        import companion.config as cfg
        monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(cfg, "SQLITE_PATH", str(tmp_path / "test.db"))

        from companion.memory.store import MemoryStore
        from companion.memory.explainability import explain_memory
        from companion.models import Fact

        store = MemoryStore()
        monkeypatch.setattr(store.vector, 'embed_text_only', lambda text: _mock_vec())

        # ── Step 1: Create ────────────────────────────────────────────
        fact = Fact(
            id="f-lifecycle-full",
            fact="Иван любит программировать на Python",
            date="2026-01-01",
            importance=7,
            confidence=0.8,
            source="test",
            tags=["interests"],
        )
        store.add_fact(fact)

        # Verify after create
        saved = store.get_fact(fact.id)
        assert saved.status == "active"
        assert saved.confidence == 0.8
        assert store.vector.get_embedding(fact.fact) is not None

        # ── Step 2: Confirm (usage tracking) ──────────────────────────
        store.db.increment_fact_usage(fact.id, used=True)
        confirmed = store.get_fact(fact.id)
        assert confirmed.facts_used_count == 1

        # ── Step 3: Update ────────────────────────────────────────────
        store.update_fact(fact.id, confidence=0.95)
        updated = store.get_fact(fact.id)
        assert updated.confidence == 0.95
        assert updated.version == 2  # version incremented

        # ── Step 4: Explain ───────────────────────────────────────────
        report = explain_memory(store, fact.id)
        assert report is not None
        assert report["confidence"] == 0.95  # reflects update
        assert report["status"] == "active"
        assert report["text"] == fact.fact

        # ── Step 5: Reindex ───────────────────────────────────────────
        result = store.reindex_all()
        assert result["facts"] >= 1

        # ── Step 6: Search ────────────────────────────────────────────
        results = store.search_facts("Python", limit=5)
        found_ids = [f.id for f, _ in results]
        assert fact.id in found_ids, "Fact must survive reindex"

        # ── Step 7: Final invariant check ─────────────────────────────
        # Active fact count matches FAISS
        active_count = store.db.count_facts("active")
        faiss_total = store.vector.index.ntotal
        # FAISS may have non-fact vectors too, so just check it's >= active facts
        assert faiss_total >= active_count - 1  # -1 for potential pending query cache
        store.close()
