"""Tests for Phase 4: Learning Engine — 10 stages of self-learning and optimization."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime

import pytest

from companion.memory.store import MemoryStore
from companion.learning_engine import (
    MemoryOutcomeTracker,
    RetrievalQualityAnalyzer,
    BeliefEvolutionService,
    PredictionCalibrationService,
    AdaptiveRetrievalService,
    ConversationReplayService,
    MemoryGarbageCollector,
    LongTermConsolidationService,
    SelfEvaluationService,
    LearningEngineService,
)


@pytest.fixture
def temp_store(tmp_path, monkeypatch):
    """MemoryStore with proper SQLITE_PATH set before construction.

    Replaces the old pattern of reassigning store.db.path after MemoryStore()
    creation, which left VectorIndex pointing at the wrong SQLite file.
    """
    import companion.config as cfg
    monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
    db_path = str(tmp_path / "test_learning.db")
    monkeypatch.setattr(cfg, "SQLITE_PATH", db_path)
    store = MemoryStore()
    yield store


def test_memory_outcome_tracking():
    tracker = MemoryOutcomeTracker()
    m = tracker.get_metrics("f101", base_importance=0.8)
    assert m.utility_importance == 0.8

    new_imp = tracker.record_outcome("f101", retrieved=True, used=True, confirmed=True)
    assert new_imp > 0.8
    assert m.retrieved_count == 1
    assert m.confirmed_count == 1

    ref_imp = tracker.record_outcome("f101", retrieved=True, refuted=True)
    assert ref_imp < new_imp


def test_retrieval_quality_analyzer():
    tracker = MemoryOutcomeTracker()
    # Retrieve f1 six times and never use it
    for _ in range(6):
        tracker.record_outcome("f_ignore", retrieved=True, used=False)

    report = RetrievalQualityAnalyzer.analyze(
        retrieved_ids=["f_ignore", "f_used"],
        used_ids=["f_used"],
        tracker=tracker,
    )
    assert report.retrieved_count == 2
    assert report.used_count == 1
    assert report.ignored_count == 1
    assert report.efficiency_ratio == 0.5
    assert "f_ignore" in report.penalized_items


def test_belief_evolution():
    res1 = BeliefEvolutionService.evolve_belief("b101", current_confidence=0.82, supported=True)
    assert res1.new_confidence > 0.82
    assert res1.status == "active"

    res2 = BeliefEvolutionService.evolve_belief("b102", current_confidence=0.35, contradicted=True)
    assert res2.new_confidence < 0.30
    assert res2.status == "archived"


def test_prediction_calibration():
    preds = [
        {"domain": "relationship", "outcome": "correct"},
        {"domain": "relationship", "outcome": "correct"},
        {"domain": "goal_tracking", "outcome": "wrong"},
        {"domain": "goal_tracking", "outcome": "wrong"},
    ]
    prof = PredictionCalibrationService.calibrate(predictions=preds)
    assert prof.total_evaluated == 4
    assert prof.domain_accuracy["relationship"] == 1.0
    assert prof.domain_accuracy["goal_tracking"] == 0.0
    assert prof.weakest_domain == "goal_tracking"


def test_adaptive_retrieval():
    svc = AdaptiveRetrievalService()
    w_default = svc.get_weights("relationship")
    assert w_default.entities_weight == 0.30

    w_pet = svc.get_weights("relationship", entity_names=["Морзик"])
    assert w_pet.episodes_weight == 0.50  # Dynamically boosted episodes for pet/relationship queries


def test_conversation_replay():
    res1 = ConversationReplayService.replay(
        query="Где Иван?",
        historical_response="В офисе",
        current_response="В новом офисе",
        historical_score=0.80,
        current_score=0.85,
    )
    assert res1.is_regression is False

    res2 = ConversationReplayService.replay(
        query="Где Иван?",
        historical_response="В офисе",
        current_response="Не знаю",
        historical_score=0.85,
        current_score=0.60,
    )
    assert res2.is_regression is True


def test_memory_garbage_collector():
    cands = [
        {"id": "f_stale", "last_retrieved_at": "2024-01-01T10:00:00", "confidence": 0.8},
        {"id": "f_active", "last_retrieved_at": "2026-07-28T10:00:00", "confidence": 0.8},
        {"id": "f_low", "confidence": 0.20, "references_count": 0},
    ]
    report = MemoryGarbageCollector.collect(candidates=cands)
    assert report.scanned_count == 3
    assert report.archived_count >= 2
    assert "f_stale" in report.archived_ids
    assert "f_low" in report.archived_ids
    assert "f_active" not in report.archived_ids


def test_long_term_consolidation():
    facts = [
        {"id": "f1", "fact": "Иван работает над проектом А"},
        {"id": "f2", "fact": "Иван обсуждает проект А с командой"},
        {"id": "f3", "fact": "Проект А движется вперед"},
    ]
    report = LongTermConsolidationService.consolidate(facts=facts)
    assert report.facts_processed == 3
    assert report.episode_candidates >= 1
    assert report.belief_candidates >= 1
    assert "work" in report.summary_text


def test_gc_report_reflects_actual_db_writes(memory_store):
    """The GC report must not claim archivals it never performed.

    Previously the writer was guarded by `hasattr(db, "update_fact")` — a
    method MemoryDatabase does not have — so the guard was always False:
    facts were counted as archived while staying active in the DB.
    """
    from datetime import datetime, timedelta

    from companion.models import Fact

    stale_date = (datetime.now() - timedelta(days=60)).isoformat()
    fact = Fact(id="gc-target", fact="давно забытый факт", date=stale_date[:10],
                importance=3, confidence=0.2, source="t")
    memory_store.db._insert_fact(fact.to_dict())

    report = MemoryGarbageCollector.collect(
        candidates=[{"id": "gc-target", "confidence": 0.2,
                     "references_count": 0, "last_retrieved_at": stale_date}],
        db=memory_store.db,
    )

    assert report.archived_count == 1
    assert memory_store.db.get_fact("gc-target")["status"] == "archived", (
        "report claimed an archival that never reached the database"
    )


def test_gc_without_db_still_reports_candidates():
    """Dry-run mode (no db) must keep working for callers that only scan."""
    report = MemoryGarbageCollector.collect(
        candidates=[{"id": "f_low", "confidence": 0.20, "references_count": 0}],
    )
    assert report.archived_count == 1
    assert "f_low" in report.archived_ids


def test_self_evaluation():
    rep = SelfEvaluationService.evaluate_turn(
        retrieved_count=10,
        used_count=8,
        confidence_level="High",
        clarification_asked=False,
    )
    assert rep.memory_sufficient is True
    assert rep.confident is True
    assert rep.excess_retrieval_ratio == 0.2
    assert rep.score > 0.8


def test_offline_learning_and_service(temp_store):
    svc = temp_store.learning
    assert isinstance(svc, LearningEngineService)

    turn_res = svc.process_turn_learning(
        intent="relationship",
        retrieved_ids=["f1", "f2"],
        used_ids=["f1"],
        confidence_level="High",
    )
    assert "retrieval_quality" in turn_res
    assert "self_evaluation" in turn_res
    assert "adaptive_weights" in turn_res

    # Insert stale candidate facts into the store so GC can archive them.
    from datetime import datetime
    stale_date = (datetime.now().replace(year=2024, month=1, day=1)).isoformat()
    for fid, text in [("f_old", "старая записка"), ("f_work1", "Новый проект запущен"), ("f_work2", "Работа над проектом идет в графике")]:
        temp_store.db._insert_fact({
            "id": fid, "fact": text, "date": "2026-06-01",
            "importance": 5, "confidence": 0.8, "source": "test",
            "status": "active", "metadata": {}, "meta": "{}",
        })

    daily = svc.run_daily_offline_learning(
        facts_candidates=[
            {"id": "f_old", "last_retrieved_at": "2024-01-01T10:00:00"},
            {"id": "f_work1", "fact": "Новый проект запущен"},
            {"id": "f_work2", "fact": "Работа над проектом идет в графике"},
        ]
    )
    assert daily.date is not None
    assert daily.gc_report.archived_count >= 1
    assert daily.consolidation_report.episodes_created >= 1
    assert "Updated strategy" in daily.strategy_update
