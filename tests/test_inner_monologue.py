"""Tests for Memory Dreaming, Inner Monologue, Anti-Repetition Window and Random Anchor."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from companion.models import Fact
from companion.proactive.inner_monologue import (
    run_memory_dreaming_cycle,
    get_latest_unused_dream,
    mark_dream_used,
)
from companion.proactive.collector import ContextPayload, collect_context
from companion.proactive.reasons import PingReason, ReasonDecision
from companion.proactive.formatter import assemble_prompt


from tests.conftest import make_fact


def test_run_memory_dreaming_cycle():
    import anyio
    anyio.run(_async_run_memory_dreaming_cycle)


async def _async_run_memory_dreaming_cycle():
    mock_store = MagicMock()
    mock_store.get_random_fact.return_value = make_fact(
        "Иван работает над проектом в Reaper",
        importance=8,
    )

    mock_user_model = MagicMock()
    mock_user_model.data = {}
    mock_user_model.get_effective_emotional_state.return_value = ("neutral", {"energy": 0.5})

    with patch("companion.proactive.inner_monologue.aio_oneshot") as mock_llm:
        mock_llm.return_value = "Ночью я размышлял о твоем проекте в Reaper и подумал, что стоит разбить задачу на части."
        entry = await run_memory_dreaming_cycle(mock_store, mock_user_model)

        assert entry is not None
        assert "Reaper" in entry["insight"]
        assert entry["used"] is False

        assert "inner_monologue" in mock_user_model.data
        assert len(mock_user_model.data["inner_monologue"]) == 1

        mock_store.add_fact.assert_called_once()
        args, kwargs = mock_store.add_fact.call_args
        assert "dream_insight" in kwargs["tags"]


def test_get_latest_unused_dream_and_mark_used():
    mock_user_model = MagicMock()
    mock_user_model.data = {
        "inner_monologue": [
            {"id": "d1", "insight": "Old used dream", "used": True},
            {"id": "d2", "insight": "Fresh unused dream", "used": False},
        ]
    }

    latest = get_latest_unused_dream(mock_user_model)
    assert latest is not None
    assert latest["id"] == "d2"
    assert latest["insight"] == "Fresh unused dream"

    mark_dream_used(mock_user_model, "d2")
    mock_user_model.data["inner_monologue"][1]["used"] = True  # simulate mutation in mock
    assert get_latest_unused_dream(mock_user_model) is None


def test_assemble_prompt_includes_all_anti_repetition_and_dream_blocks():
    payload = ContextPayload(
        reason=PingReason.LONG_SILENCE,
        source_id=None,
        facts=["Иван тестировщик"],
        urgency=50,
        recent_pings=["Как твои дела?", "Привет, давно не виделись"],
        random_anchor="Любит играть на гитаре",
        dream_insight="Ночью размышлял о гитарных мелодиях...",
        dream_id="d10",
    )

    prompt = assemble_prompt(payload, strategy="challenge", tone="empathetic")

    assert "# RECENT PROACTIVE PINGS (DO NOT REPEAT TOPICS OR PHRASES)" in prompt
    assert "Как твои дела?" in prompt
    assert "# RANDOM MEMORY ANCHOR" in prompt
    assert "Любит играть на гитаре" in prompt
    assert "# INNER DIARY / DREAM INSIGHT" in prompt
    assert "Ночью размышлял о гитарных мелодиях..." in prompt
