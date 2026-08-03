"""Tests for Phase 3: Reasoning Engine — 10 stages of decision-making before answering."""
from __future__ import annotations

import os
import tempfile

import pytest

from companion.memory.store import MemoryStore
from companion.memory.cognitive_loop import MemoryContext
from companion.models import Fact, Entity
from companion.reasoning_engine import (
    ReasoningPlanner,
    ReasoningModuleRegistry,
    EvidenceBuilder,
    EvidenceItem,
    HypothesisEngine,
    UncertaintyEngine,
    ClarificationPlanner,
    MultiStepReasoning,
    ReflectionBuffer,
    ToolPlanner,
    FinalAnswerComposer,
    ReasoningEngineService,
)


@pytest.fixture
def temp_store(tmp_path, monkeypatch):
    """MemoryStore with proper SQLITE_PATH set before construction."""
    import companion.config as cfg
    monkeypatch.setattr(cfg, "DATA_DIR", str(tmp_path))
    db_path = str(tmp_path / "test_reasoning.db")
    monkeypatch.setattr(cfg, "SQLITE_PATH", db_path)
    store = MemoryStore()
    yield store


def test_reasoning_planner():
    plan1 = ReasoningPlanner.plan("Как там Женя?")
    assert plan1.intent == "relationship"
    assert "relationship" in plan1.modules_to_run

    plan2 = ReasoningPlanner.plan("Какие у нас цели на неделю и прогресс?")
    assert plan2.intent == "goal_tracking"
    assert "goal" in plan2.modules_to_run

    plan3 = ReasoningPlanner.plan("Что сейчас в интернете по новостям погоды?")
    assert plan3.need_internet is True

    plan4 = ReasoningPlanner.plan("он сказал это вчера")
    assert plan4.need_clarification is True

    plan5 = ReasoningPlanner.plan("привет")
    assert plan5.intent == "small_talk"
    assert plan5.need_memory is False


def test_reasoning_modules():
    plan = ReasoningPlanner.plan("Как там Женя? Когда мы встречались?")
    e = Entity(
        entity_id="ent_zhenya",
        name="Женя",
        type="person",
        importance=0.8,
    )
    ctx = MemoryContext(entities=[e], episodes=[{"date": "2026-07-28", "event": "Встреча с Женей"}])
    res = ReasoningModuleRegistry.execute_modules(plan, "Как там Женя? Когда мы встречались?", ctx)
    assert len(res) >= 1
    assert any(r["module"] == "relationship" for r in res)
    assert any("Женя" in r["summary"] for r in res)


def test_evidence_builder():
    f = Fact(
        id="f101",
        fact="Иван работает в новом офисе",
        importance=8,
        date="2026-07-29",
        confidence=0.9,
        source="test",
    )
    ctx = MemoryContext(facts=[f])
    ev = EvidenceBuilder.build_evidence("Расскажи где работает Иван", ctx)
    assert len(ev) == 1
    assert ev[0].claim == "Иван работает в новом офисе"
    assert ev[0].confidence == 0.9
    assert ev[0].evidence_type == "fact"


def test_hypothesis_engine():
    # Strong evidence -> no hypothesis needed
    ev_strong = [
        EvidenceItem(claim="Fact 1", evidence_type="fact", evidence_id="f1", confidence=0.8),
        EvidenceItem(claim="Fact 2", evidence_type="fact", evidence_id="f2", confidence=0.8),
    ]
    res_strong = HypothesisEngine.generate_hypotheses("query", ev_strong)
    assert res_strong["recommendation"] == "answer_directly"
    assert len(res_strong["hypotheses"]) == 0

    # Sparse evidence -> hypotheses generated
    res_sparse = HypothesisEngine.generate_hypotheses("что с проектом?", [])
    assert len(res_sparse["hypotheses"]) > 0
    assert res_sparse["recommendation"] == "ask_user"


def test_uncertainty_engine():
    # Low confidence
    ev_low = [EvidenceItem(claim="Uncertain claim", evidence_type="fact", evidence_id="f1", confidence=0.3)]
    eval_low = UncertaintyEngine.evaluate(ev_low, {})
    assert eval_low.level == "Low"
    assert eval_low.assertive is False  # Stops asserting dogmatically when low confidence

    # High confidence
    ev_high = [
        EvidenceItem(claim="Confident claim 1", evidence_type="fact", evidence_id="f1", confidence=0.9),
        EvidenceItem(claim="Confident claim 2", evidence_type="fact", evidence_id="f2", confidence=0.9),
    ]
    eval_high = UncertaintyEngine.evaluate(ev_high, {})
    assert eval_high.level == "High"
    assert eval_high.assertive is True


def test_clarification_planner():
    plan = ReasoningPlanner.plan("он сказал это вчера")
    ev = []
    unc = UncertaintyEngine.evaluate(ev, {})
    question = ClarificationPlanner.plan_clarification("он сказал это вчера", plan, ev, unc)
    assert question is not None
    assert "Уточните" in question.question or "о ком" in question.question


def test_multistep_reasoning():
    plan = ReasoningPlanner.plan("Как там Женя?")
    ctx = MemoryContext()
    ev = []
    unc = UncertaintyEngine.evaluate(ev, {})
    steps = MultiStepReasoning.execute_chain("Как там Женя?", plan, ctx, ev, unc, max_steps=5)
    assert 1 <= len(steps) <= 5
    assert steps[0].step_index == 1
    assert "Intent" in steps[0].action


def test_reflection_buffer():
    # High confidence response
    ev_high = ReflectionBuffer.evaluate_response("Как дела?", "Всё отлично, проект движется по графику.", "High")
    assert ev_high.is_good is True
    assert ev_high.write_action == "save_to_memory"

    # Low confidence -> skip writing declarative memory
    ev_low = ReflectionBuffer.evaluate_response("Что там было?", "Я не вполне уверен в деталях.", "Low")
    assert ev_low.write_action == "skip"


def test_tool_planner():
    plan_web = ReasoningPlanner.plan("Какие сегодняшние новости в мире?")
    tools_web = ToolPlanner.plan_tools("Какие сегодняшние новости в мире?", plan_web)
    assert any(t.tool_name == "web_search" for t in tools_web)

    plan_cal = ReasoningPlanner.plan("Покажи мой календарь и расписание")
    tools_cal = ToolPlanner.plan_tools("Покажи мой календарь и расписание", plan_cal)
    assert any(t.tool_name == "calendar" for t in tools_cal)


def test_final_answer_composer_and_service(temp_store):
    service = temp_store.reasoner
    assert isinstance(service, ReasoningEngineService)

    f = Fact(
        id="f101",
        fact="Иван работает над Memory OS",
        importance=9,
        date="2026-07-29",
        confidence=0.9,
        source="test",
    )
    ctx = MemoryContext(facts=[f])
    res = service.reason("Над чем работает Иван?", ctx)
    assert "prompt_block" in res
    block = res["prompt_block"]
    assert "<reasoning_engine_context>" in block
    assert "<execution_plan" in block
    assert "<evidence_summary>" in block
    assert "Иван работает над Memory OS" in block
    assert "<uncertainty_guidance" in block
    assert "assertive=" in block
