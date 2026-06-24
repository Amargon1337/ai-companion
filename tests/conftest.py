"""Shared fixtures and mocks for tests."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

# Must set env vars before any companion import to avoid config parse errors
os.environ.setdefault("API_TOKEN", "test:token")
os.environ.setdefault("GOOGLE_API_KEY", "test_key")
os.environ.setdefault("ADMIN_IDS", "12345")
os.environ.setdefault("LLM_TIMEOUT", "5")
os.environ.setdefault("LLM_RETRIES", "1")

from companion.memory.retrieval import RetrievalBudgetManager
from companion.memory.store import MemoryStore
from companion.models import Fact


@pytest.fixture
def mock_llm_oneshot():
    """Mock companion.llm.client.oneshot to return a JSON array."""
    with patch("companion.llm.client.oneshot") as mock:
        mock.return_value = '[{"fact": "test fact", "importance": 5, "confidence": 0.8, "tags": [], "memory_kind": "event"}]'
        yield mock


@pytest.fixture
def mock_llm_parse():
    """Mock parse_json_array to return predefined data."""
    with patch("companion.llm.client.parse_json_array") as mock:
        mock.return_value = [{"fact": "test fact", "importance": 5, "confidence": 0.8, "tags": [], "memory_kind": "event"}]
        yield mock


@pytest.fixture
def mock_llm_parse_object():
    """Mock parse_json_object."""
    with patch("companion.llm.client.parse_json_object") as mock:
        mock.return_value = {"interests": {}}
        yield mock


@pytest.fixture
def mock_chat():
    """Mock Gemini chat session."""
    chat = MagicMock()
    response = MagicMock()
    response.text = "Test summary response."
    chat.send_message.return_value = response
    return chat


@pytest.fixture
def memory_store(tmp_path):
    """MemoryStore with SQLite in temp dir."""
    import companion.config as cfg
    original_data_dir = cfg.DATA_DIR
    original_sqlite = cfg.SQLITE_PATH
    cfg.DATA_DIR = str(tmp_path)
    cfg.SQLITE_PATH = str(tmp_path / "companion.db")
    store = MemoryStore()
    yield store
    cfg.DATA_DIR = original_data_dir
    cfg.SQLITE_PATH = original_sqlite


def make_fact(fact_text: str, importance: int = 5, tags: list = None, kind: str = "event") -> Fact:
    return Fact(
        fact=fact_text,
        date="2026-06-01",
        importance=importance,
        confidence=0.9,
        source="test",
        source_type="test",
        memory_kind=kind,
        tags=tags or [],
        status="active",
    )


@pytest.fixture
def retrieval_mgr():
    return RetrievalBudgetManager(char_budget=5000, max_facts=10, max_reflections=3)


@pytest.fixture
def sample_facts():
    return [
        make_fact("Иван — 23-летний тестировщик", importance=10, tags=["core_identity"]),
        make_fact("Пса зовут Морзик", importance=9, tags=["anchor"]),
        make_fact("Любит Python", importance=7, tags=["pinned"]),
        make_fact("Работает на AMD A8 с 4GB RAM", importance=8, tags=["permanent"], kind="permanent"),
        make_fact("Сегодня была хорошая погода", importance=3, tags=[]),
        make_fact("Вчера купил хлеб", importance=2, tags=[]),
        make_fact("Тревожное расстройство F41.3", importance=9, tags=["core_identity", "medical"]),
        make_fact("Слушает метал", importance=4, tags=["music"]),
        make_fact("Пьёт амитриптилин 125мг", importance=8, tags=["medical"]),
        make_fact("Был в парке", importance=3, tags=[]),
    ]
