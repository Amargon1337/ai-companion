"""Tests for oneshot_structured with Pydantic schemas and mocked API responses."""
from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from companion.llm.client import (
    oneshot_structured,
    MessageAnalysis,
    FactExtractionResult,
    ConsolidationResult,
    CausalLinkExtractionResult,
    ReflectionResult,
    PersonalityPipelineResult,
)


def test_oneshot_structured_message_analysis():
    mock_response = MagicMock()
    mock_response.text = (
        '{"intent": "command", "confidence": 0.95, '
        '"user_mood": {"anxiety": 0.1, "anger": 0.0, "sadness": 0.0, "energy": 0.8}, '
        '"user_state": "NORMAL", "estimated_importance": 5, "command": "show_facts"}'
    )
    with patch("companion.llm.client.client.models.generate_content", return_value=mock_response):
        result = oneshot_structured("dummy prompt", MessageAnalysis)
        assert isinstance(result, MessageAnalysis)
        assert result.intent == "command"
        assert result.user_mood.anxiety == 0.1
        assert result.command == "show_facts"


def test_oneshot_structured_fact_extraction():
    mock_response = MagicMock()
    mock_response.text = (
        '{"facts": ['
        '  {"fact": "Иван любит Python", "memory_kind": "permanent", '
        '   "importance": 8, "confidence": 0.9, "tags": ["prog"], '
        '   "evidence_messages": ["msg_1"]}'
        ']}'
    )
    with patch("companion.llm.client.client.models.generate_content", return_value=mock_response):
        result = oneshot_structured("dummy prompt", FactExtractionResult)
        assert isinstance(result, FactExtractionResult)
        assert len(result.facts) == 1
        assert result.facts[0].fact == "Иван любит Python"
        assert result.facts[0].memory_kind == "permanent"


def test_oneshot_structured_consolidation():
    mock_response = MagicMock()
    mock_response.text = (
        '{"relations": ['
        '  {"new_fact_index": 0, "existing_fact_id": "fact_123", '
        '   "relation": "supersedes", "reason": "newer info"}'
        ']}'
    )
    with patch("companion.llm.client.client.models.generate_content", return_value=mock_response):
        result = oneshot_structured("dummy prompt", ConsolidationResult)
        assert isinstance(result, ConsolidationResult)
        assert len(result.relations) == 1
        assert result.relations[0].relation == "supersedes"
        assert result.relations[0].existing_fact_id == "fact_123"


def test_oneshot_structured_causal_links():
    mock_response = MagicMock()
    mock_response.text = (
        '{"links": ['
        '  {"cause": "anxiety", "effect": "insomnia", "confidence": 0.85, '
        '   "evidence": ["msg_2"], "mechanism": "hyperarousal"}'
        ']}'
    )
    with patch("companion.llm.client.client.models.generate_content", return_value=mock_response):
        result = oneshot_structured("dummy prompt", CausalLinkExtractionResult)
        assert isinstance(result, CausalLinkExtractionResult)
        assert len(result.links) == 1
        assert result.links[0].cause == "anxiety"
        assert result.links[0].mechanism == "hyperarousal"


def test_oneshot_structured_reflection():
    mock_response = MagicMock()
    mock_response.text = (
        '{"reflections": ['
        '  {"insight": "Одиночество остаётся центральной темой", "importance": 7, "confidence": 0.8}'
        ']}'
    )
    with patch("companion.llm.client.client.models.generate_content", return_value=mock_response):
        result = oneshot_structured("dummy prompt", ReflectionResult)
        assert isinstance(result, ReflectionResult)
        assert len(result.reflections) == 1
        assert result.reflections[0].insight == "Одиночество остаётся центральной темой"


def test_oneshot_structured_personality_pipeline():
    mock_response = MagicMock()
    mock_response.text = (
        '{"interests_delta": {"Python": 1}, "values_to_add": ["Качественный код"], '
        '"values_to_remove": [], "changes": ["начал писать больше тестов"]}'
    )
    with patch("companion.llm.client.client.models.generate_content", return_value=mock_response):
        result = oneshot_structured("dummy prompt", PersonalityPipelineResult)
        assert isinstance(result, PersonalityPipelineResult)
        assert result.interests_delta["Python"] == 1
        assert "Качественный код" in result.values_to_add
