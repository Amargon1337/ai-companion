"""Tests for run_compress_pipeline with mocked LLM."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from companion.llm.pipeline import run_compress_pipeline


_LLM_ONESHOT_RETURN = '[{"fact": "test fact", "importance": 5, "confidence": 0.8, "tags": [], "memory_kind": "event"}]'
_LLM_PARSE_RETURN = [{"fact": "test fact", "importance": 5, "confidence": 0.8, "tags": [], "memory_kind": "event"}]
_LLM_PARSE_OBJ_RETURN = {"interests": {}}


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
