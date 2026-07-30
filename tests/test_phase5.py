"""Tests for Phase 5: Executive Architecture & Meta Cognition."""
from __future__ import annotations

import pytest
from companion.executive import ExecutiveController, CognitiveModule, ExecutivePipeline
from companion.memory.attention import MemoryAttentionService, AttentionScore
from companion.explainability import ExplainabilityService
from companion.persona import DynamicPersona, ConversationStateMachine
from companion.simulation import SimulationEngine, SimulationOption
from companion.curiosity import CuriosityPlanner, EpisodicRecallService


class DummyModule(CognitiveModule):
    def process_turn(self, query: str, state: dict[str, Any]) -> dict[str, Any]:
        return {"status": "ok", "query_len": len(query)}


def test_executive_controller():
    mod = DummyModule()
    ctrl = ExecutiveController(modules=[mod])
    
    pipeline = ctrl.execute_turn("hello world")
    assert "DummyModule" in pipeline.state
    assert pipeline.state["DummyModule"]["status"] == "ok"
    assert len(pipeline.metrics) > 0


def test_memory_attention_service():
    item = {
        "fact": "Морзик это собака",
        "importance": 0.9,
    }
    score = MemoryAttentionService.calculate_attention(
        item=item,
        query="где морзик собака",
        semantic_score=0.8,
        conversation_context={"active_entities": ["Морзик"]}
    )
    assert score.semantic == 0.8
    assert score.importance == 0.9
    assert score.relationship == 0.8
    assert score.conversation_relevance > 0.0
    assert score.total_score > 2.5


def test_explainability_service():
    item = {"fact": "Морзик это собака"}
    score = AttentionScore(semantic=0.8, importance=0.9, relationship=0.8)
    trace = ExplainabilityService.explain_retrieval(item, score, rank=1)
    
    assert "[Trace Rank 1]" in trace
    assert "high semantic match" in trace
    assert "high historical importance" in trace
    assert "active entity" in trace


def test_dynamic_persona():
    persona = DynamicPersona()
    assert persona.empathy == 0.7
    
    persona.adapt_to_emotion("stressful day")
    assert persona.energy < 0.7  # Energy dropped
    assert persona.empathy > 0.7 # Empathy raised
    
    guidance = persona.get_prompt_guidance()
    assert "empathetic" in guidance.lower()


def test_conversation_state_machine():
    sm = ConversationStateMachine()
    assert sm.current_state == "greeting"
    
    # Transition to exploration
    state = sm.transition("как дела?", "general")
    assert state == "exploration"
    
    # Transition to problem
    state = sm.transition("у меня проблема с кодом", "problem")
    assert state == "problem"


def test_simulation_engine():
    persona = DynamicPersona()
    options = SimulationEngine.simulate(
        query="я очень устал и все плохо",
        persona=persona,
        conversation_state="problem",
        uncertainty_level="Low"
    )
    assert len(options) == 3
    # With empathy active and 'problem' state, Empathic Support should score high
    empathy_opt = next(o for o in options if o.strategy_name == "Empathic Support")
    assert empathy_opt.utility_score > 0.5


def test_curiosity_planner():
    q = CuriosityPlanner.plan("работаю над новым проектом", context_density=0.2)
    assert q is not None
    assert q.topic == "Work Goals"
    
    q_skip = CuriosityPlanner.plan("работаю над новым проектом", context_density=0.8)
    assert q_skip is None


def test_episodic_recall_service():
    ep = {"event": "В прошлый раз была ошибка с базой данных"}
    recall = EpisodicRecallService.analyze("как починить базу", ep)
    assert "mistake" in recall.learned_lesson.lower()
