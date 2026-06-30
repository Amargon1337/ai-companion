"""Tests for run_compress_pipeline with mocked LLM."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from companion.llm.pipeline import run_compress_pipeline


_LLM_ONESHOT_RETURN = '[{"fact": "test fact", "importance": 5, "confidence": 0.8, "tags": [], "memory_kind": "event", "evidence_messages": ["msg_1"]}]'
_LLM_PARSE_RETURN = [{"fact": "test fact", "importance": 5, "confidence": 0.8, "tags": [], "memory_kind": "event", "evidence_messages": ["msg_1"]}]
_LLM_PARSE_OBJ_RETURN = {
    "interests_delta": {"QA и автоматизация": 1},
    "values_to_add": ["Морзик"],
    "values_to_remove": [],
    "fears_to_add": [],
    "fears_to_remove": [],
    "habits_delta": {}
}


@patch("companion.llm.client.parse_json_object", return_value=_LLM_PARSE_OBJ_RETURN)
@patch("companion.llm.client.parse_json_array", return_value=_LLM_PARSE_RETURN)
@patch("companion.llm.client.oneshot", return_value=_LLM_ONESHOT_RETURN)
def test_run_compress_pipeline_returns_summary(mock_oneshot, mock_parse, mock_parse_obj, mock_chat, memory_store):
    with patch("companion.storage.legacy.LegacyStorage.save_summary") as mock_save, \
         patch("companion.storage.legacy.LegacyStorage.load_master_summary", return_value=""), \
         patch("companion.storage.legacy.LegacyStorage.save_master_summary"):
        result = run_compress_pipeline(memory_store, mock_chat, 12345)

    assert result == "Test summary response."
    mock_chat.send_message.assert_called_once()
    mock_save.assert_called_once_with("Test summary response.")


@patch("companion.llm.client.parse_json_object", return_value=_LLM_PARSE_OBJ_RETURN)
@patch("companion.llm.client.parse_json_array", return_value=_LLM_PARSE_RETURN)
@patch("companion.llm.client.oneshot", return_value=_LLM_ONESHOT_RETURN)
def test_run_compress_pipeline_empty_response(mock_oneshot, mock_parse, mock_parse_obj, memory_store):
    chat = MagicMock()
    response = MagicMock()
    response.text = ""
    chat.send_message.return_value = response

    result = run_compress_pipeline(memory_store, chat, 12345)
    assert result is None


@patch("companion.llm.client.parse_json_object", return_value=_LLM_PARSE_OBJ_RETURN)
@patch("companion.llm.client.parse_json_array", return_value=_LLM_PARSE_RETURN)
@patch("companion.llm.client.oneshot", return_value=_LLM_ONESHOT_RETURN)
def test_run_compress_pipeline_increments_count(mock_oneshot, mock_parse, mock_parse_obj, mock_chat, memory_store):
    with patch("companion.storage.legacy.LegacyStorage.save_summary"), \
         patch("companion.storage.legacy.LegacyStorage.load_master_summary", return_value=""), \
         patch("companion.storage.legacy.LegacyStorage.save_master_summary"):
        c0 = memory_store.get_compress_count()
        run_compress_pipeline(memory_store, mock_chat, 12345)
        c1 = memory_store.get_compress_count()
    assert c1 == c0 + 1


@patch("companion.llm.client.parse_json_object", return_value=_LLM_PARSE_OBJ_RETURN)
@patch("companion.llm.client.parse_json_array", return_value=_LLM_PARSE_RETURN)
@patch("companion.llm.client.oneshot", return_value=_LLM_ONESHOT_RETURN)
def test_run_compress_pipeline_error_handling(mock_oneshot, mock_parse, mock_parse_obj, memory_store):
    chat = MagicMock()
    chat.send_message.side_effect = RuntimeError("API failure")

    result = run_compress_pipeline(memory_store, chat, 12345)
    assert result is None


def test_merge_personality_differential_and_decay():
    from companion.llm.pipeline import _merge_personality

    old_profile = {
        "interests": {
            "QA и автоматизация": 8.0,
            "Python": 5.0,
            "История": 2.1
        },
        "values": ["Морзик", "Качественный код"],
        "fears": ["Выгорание"],
        "habits": {
            "работать по ночам": "стабильно"
        }
    }

    # Delta updates from LLM
    delta = {
        "interests_delta": {
            "QA и автоматизация": 1.0,
            "Python": -2.0,
            "Философия": 3.0
        },
        "values_to_add": ["Свобода"],
        "values_to_remove": ["Качественный код"],
        "fears_to_add": ["Одиночество"],
        "fears_to_remove": ["Выгорание"],
        "habits_delta": {
            "работать по ночам": "усилилась",
            "курение": "появилась"
        }
    }

    merged = _merge_personality(old_profile, delta)

    # QA: 8.0 + 1.0 = 9.0
    assert merged["interests"]["QA и автоматизация"] == 9.0
    # Python: 5.0 - 2.0 = 3.0
    assert merged["interests"]["Python"] == 3.0
    # История: not in delta, decay -0.2 applied -> 2.1 - 0.2 = 1.9 (< 2.0, so pruned)
    assert "История" not in merged["interests"]
    # Философия: new interest -> 3.0
    assert merged["interests"]["Философия"] == 3.0

    # Values: "Качественный код" removed, "Свобода" added
    assert "Морзик" in merged["values"]
    assert "Свобода" in merged["values"]
    assert "Качественный код" not in merged["values"]

    # Fears: "Выгорание" removed, "Одиночество" added
    assert "Одиночество" in merged["fears"]
    assert "Выгорание" not in merged["fears"]

    # Habits: updated
    assert merged["habits"]["работать по ночам"] == "усилилась"
    assert merged["habits"]["курение"] == "появилась"
