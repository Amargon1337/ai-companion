"""Tests for Zero-Advice Protocol («Тихая гавань») and Aesthetic Fingerprint («Эстетический отпечаток» / «Ночная кухня»)."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from companion.llm.sessions import build_system_instruction
from companion.policy_layer import (
    policy_layer,
    UserState,
    PolicyDecision,
    PolicyConstraints,
    ResponseMode,
)


def test_build_system_instruction_zero_advice_and_aesthetic():
    mock_store = MagicMock()
    mock_store.db.get_meta.return_value = ""
    mock_store.search_facts.return_value = []
    mock_retrieval = MagicMock()

    with patch("companion.user_model.user_model.get_effective_emotional_state") as mock_state:
        mock_state.return_value = ("depressed", {"energy": 0.2, "sadness": 0.6})
        prompt = build_system_instruction(mock_store, mock_retrieval)

        assert "[CRITICAL: ZERO-ADVICE PROTOCOL]" in prompt
        assert "[CRITICAL: ЭСТЕТИЧЕСКИЙ ОТПЕЧАТОК]" in prompt
        assert "Ночной кухни" in prompt
        assert "Шопенгауэр" in prompt
        assert "Дадзая" in prompt
        assert "Reaper" in prompt


def test_policy_layer_depressed_rule_max_questions_zero():
    rules = policy_layer.rules.get(UserState.DEPRESSED)
    assert rules is not None and len(rules) > 0

    decision = rules[0]
    assert decision.constraints.max_questions == 0
    assert decision.constraints.avoid_questions is True

    formatted_prompt = policy_layer.format_prompt_with_policy("BASE PROMPT", decision)
    assert "Max questions: 0" in formatted_prompt
    assert "- [ZERO-ADVICE PROTOCOL] НЕ задавай ни одного вопроса в ответе!" in formatted_prompt


def test_policy_layer_strips_questions_in_zero_advice():
    decision = PolicyDecision(
        response_mode=ResponseMode.EMPATHY,
        constraints=PolicyConstraints(
            avoid_explanation=True,
            avoid_theorizing=True,
            avoid_questions=True,
            max_questions=0,
            tone="empathic",
        ),
        reasoning="zero advice test",
        confidence=0.9,
    )

    input_text = "Я слышу тебя. Как ты себя чувствуешь? Отдохни сегодня. Что планируешь делать?"
    output_text = policy_layer.enforce_constraints(input_text, decision.constraints)

    assert "?" not in output_text
    assert "Я слышу тебя." in output_text
    assert "Отдохни сегодня." in output_text
    assert "Как ты себя чувствуешь" not in output_text
    assert "Что планируешь делать" not in output_text


def test_enforce_sensitivity_guards_extended_toxic_patterns():
    text = "Тяжелый день. Не опускай руки! Всё наладится. Я рядом с тобой."
    cleaned = policy_layer.enforce_sensitivity_guards(text, effective_state="depressed")

    assert "не опускай руки" not in cleaned.lower()
    assert "всё наладится" not in cleaned.lower()
    assert "Тяжелый день." in cleaned
    assert "Я рядом с тобой." in cleaned
