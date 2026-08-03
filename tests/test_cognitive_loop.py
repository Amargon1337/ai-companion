"""Tests for Phase 2.3: Cognitive Loop — 10 processes of active memory update."""
from __future__ import annotations

import os
import tempfile

import pytest

from companion.memory.store import MemoryStore
from companion.memory.cognitive_loop import (
    RetrievalPlanner,
    RetrievalBudgetAllocator,
    ContextCompressor,
    ConflictResolver,
    MemoryContext,
    MemoryFusionService,
    PredictionFeedbackService,
    GoalFeedbackService,
    MemoryWritePlanner,
    MemoryWriteExecutor,
    ImportanceFeedbackService,
    PipelineTelemetry,
)
from companion.models import Fact


@pytest.fixture
def temp_store(tmp_path, monkeypatch):
    """MemoryStore with proper SQLITE_PATH set before construction."""
    import companion.config as cfg
    monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
    db_path = str(tmp_path / "test_cognitive.db")
    monkeypatch.setattr(cfg, "SQLITE_PATH", db_path)
    store = MemoryStore()
    yield store


def test_retrieval_planner():
    # Targeted entity query
    plan1 = RetrievalPlanner.plan_retrieval("Как там Женя?")
    assert plan1.fetch_entities is True
    assert plan1.fetch_episodes is True
    assert plan1.fetch_facts is True
    assert plan1.fetch_goals is False
    assert plan1.fetch_predictions is False

    # Goal query
    plan2 = RetrievalPlanner.plan_retrieval("Какие у нас цели на неделю и прогресс?")
    assert plan2.fetch_goals is True
    assert plan2.fetch_facts is True

    # Prediction query
    plan3 = RetrievalPlanner.plan_retrieval("Каков прогноз по проекту?")
    assert plan3.fetch_predictions is True


def test_retrieval_budget_and_compression():
    plan = RetrievalPlanner.plan_retrieval("Как там Женя?")
    budget = RetrievalBudgetAllocator.allocate(plan, total_tokens=6000)
    assert budget.total_tokens == 6000
    assert budget.facts_budget > 0
    assert budget.episodes_budget > 0

    # Compression test
    facts = [
        Fact(id=f"f{i}", fact="A very long detailed fact about the project and system status num " + str(i), importance=8, date="2026-07-29", confidence=0.8, source="test")
        for i in range(15)
    ]
    # Set a tiny budget to force compression
    compressed = ContextCompressor.compress_facts(facts, max_tokens=100)
    assert len(compressed) < len(facts)
    assert any(isinstance(x, str) and "[Сжатое резюме" in x for x in compressed)


def test_conflict_resolver():
    f = Fact(id="f1", fact="Иван любит пить кофе по утрам", importance=8, date="2026-07-29", confidence=0.8, source="test")
    conflicts = ConflictResolver.detect_conflicts("Иван не любит пить кофе по утрам", [f], [], [])
    assert len(conflicts) == 1
    assert conflicts[0]["old"] == "Иван любит пить кофе по утрам"
    assert conflicts[0]["confidence"] == 0.8


def test_memory_fusion_and_context():
    f = Fact(id="f1", fact="Иван работает над Memory OS", importance=9, date="2026-07-29", confidence=0.8, source="test")
    ctx = MemoryContext(
        facts=[f],
        timeline=[{"date": "2026-07-29", "event": "Завершение Phase 2.2"}],
        conflicts=[{"old": "Old info", "new": "New info", "confidence": 0.8}],
    )
    block = ctx.to_prompt_block()
    assert "[MEMORY CONTEXT (FUSED)]" in block
    assert "<facts>" in block
    assert "Иван работает над Memory OS" in block
    assert "<timeline>" in block
    assert "<conflicts>" in block
    assert "Old info" in block


def test_prediction_and_goal_feedback(temp_store):
    db = temp_store.db
    # Create prediction
    db.upsert_prediction({
        "prediction_id": "pred1",
        "hypothesis": "Ivan will finish Phase 2 today",
        "confidence": 0.5,
        "outcome": "pending",
    })
    # Evaluate prediction
    updates_pred = PredictionFeedbackService.evaluate_predictions(
        "Да, я завершил и сделал это сегодня", "Поздравляю!", db
    )
    assert len(updates_pred) == 1
    assert updates_pred[0]["outcome"] == "correct"
    assert updates_pred[0]["confidence"] == 0.6

    # Create goal
    db.upsert_goal({
        "goal_id": "goal1",
        "title": "Дописать когнитивный цикл",
        "description": "Phase 2.3",
        "status": "active",
    })
    # Evaluate goal progress
    updates_goal = GoalFeedbackService.update_goals_from_interaction(
        "Я закончил и сделал цель готово", "Отлично!", db
    )
    assert len(updates_goal) == 1
    assert updates_goal[0]["new_status"] == "completed"


def test_write_planner_and_executor(temp_store):
    # Small talk -> nothing
    items_small = MemoryWritePlanner.plan_write("привет", "привет!", importance=2.0)
    assert items_small[0].action == "nothing"

    # Important fact
    items_fact = MemoryWritePlanner.plan_write("Я переехал в новый офис в Москве", "Понял!", importance=8.0)
    assert any(i.action == "save_fact" for i in items_fact)

    # Goal expression
    items_goal = MemoryWritePlanner.plan_write("Хочу сделать релиз к пятнице, цель: релиз", "Отличный план!", importance=7.0)
    assert any(i.action == "save_goal" for i in items_goal)

    # Execute plan
    res = MemoryWriteExecutor.execute_plan(items_goal, temp_store, temp_store.db)
    assert len(res) == 1
    assert res[0]["action"] == "save_goal"


def test_importance_feedback(temp_store):
    db = temp_store.db
    ent = {"entity_id": "ent_test", "name": "Тест", "type": "concept", "importance": 0.5}
    db.upsert_world_entity(ent)

    from companion.models import Entity
    loaded = Entity(**db.get_world_entity("ent_test"))
    ctx = MemoryContext(entities=[loaded])
    count = ImportanceFeedbackService.apply_retrieval_feedback(ctx, db)
    assert count == 1
    updated = db.get_world_entity("ent_test")
    assert float(updated["importance"]) == 0.52


def test_pipeline_telemetry_and_service_integration(temp_store):
    cog = temp_store.cognitive
    assert cog is not None
    ctx = cog.retrieve_and_fuse("Как там проект?", importance=7.0)
    assert ctx is not None
    assert ctx.plan is not None

    fb = cog.process_turn_feedback("Хочу сделать новую задачу", "Отлично!", importance=7.0)
    assert "write_results" in fb
    assert "write_ms" in fb

    logs = PipelineTelemetry.get_recent_telemetry()
    assert len(logs) > 0
    assert logs[-1].planner_ms >= 0.0
