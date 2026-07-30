"""A full request path with real storage/retrieval and a deterministic fake LLM."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from companion import bot_core, observability
from companion.memory.retrieval import RetrievalBudgetManager
from companion.models import Fact


class FakeBot:
    async def send_chat_action(self, **_kwargs):
        return None


class FakeMessage:
    def __init__(self, user_id: int) -> None:
        self.from_user = SimpleNamespace(id=user_id)
        self.chat = SimpleNamespace(id=user_id)
        self.bot = FakeBot()
        self.answers: list[str] = []

    async def answer(self, text: str, **_kwargs):
        self.answers.append(text)


class FakeChat:
    model = "fake"

    def get_history(self):
        return []

    async def send_message(self, _payload):
        return SimpleNamespace(text="Его зовут Морзик")


def _discard_task(coro, _task_name="background"):
    coro.close()
    return MagicMock()


def test_telegram_request_to_retrieval_response_and_replay(memory_store):
    user_id = 123
    fact = Fact(
        id="e2e-dog",
        fact="Пcа зовут Морзик",
        date="2026-07-01",
        importance=9,
        confidence=1.0,
        source="integration",
        source_type="test",
        memory_kind="permanent",
        tags=["anchor"],
    )
    memory_store.add_fact(fact)
    reranker = MagicMock()
    reranker.rerank.side_effect = lambda _query, facts, **_kwargs: facts
    manager = RetrievalBudgetManager(store=memory_store, reranker=reranker)
    message = FakeMessage(user_id)
    fake_chat = FakeChat()
    analysis = {
        "estimated_importance": 6,
        "user_mood": {"energy": 0.5},
        "intent": "memory",
        "confidence": 0.9,
        "user_state": "neutral",
        "command": None,
        "needs_clarification": "",
    }

    bot_core.user_chats[user_id] = fake_chat
    bot_core.user_message_counts[user_id] = 0
    bot_core._user_request_times.pop(user_id, None)
    with (
        patch.object(bot_core, "memory_store", memory_store),
        patch.object(bot_core, "retrieval_mgr", manager),
        patch.object(bot_core, "context_aggregator", MagicMock(build_prompt_block=lambda: "")),
        patch.object(bot_core, "analyze_message", return_value=analysis),
        patch.object(bot_core.reasoning_engine, "auto_reasoning_context", return_value={}),
        patch.object(bot_core.commands, "auto_add_event_from_message"),
        patch.object(bot_core, "safe_task", side_effect=_discard_task),
        patch.object(bot_core, "run_self_critique", return_value=SimpleNamespace(score=10, critique="")),
        patch.object(bot_core, "apply_critique_to_text", side_effect=lambda text, _critique: text),
        patch("companion.user_model.user_model.record_emotional_state"),
        patch("companion.llm.client.aio_oneshot", return_value="Use the retrieved dog name."),
        patch.object(bot_core.llm, "run_llm", new=AsyncMock(return_value=SimpleNamespace(text="Его зовут Морзик"))),
    ):
        asyncio.run(bot_core.process_llm_request(message, "Как зовут пcа?"))

    assert message.answers[-1] == "Его зовут Морзик"
    stored = memory_store.recent_messages(min_importance=0, limit=10)
    assert {item.role for item in stored} >= {"user", "assistant"}
    trace = observability.latest_trace(user_id)
    assert trace is not None
    assert any(item["id"] == fact.id for item in trace.facts)
    replay = observability.load_replay(memory_store, trace.replay_id)
    assert replay is not None
    assert replay["response_text"] == "Его зовут Морзик"
    assert any(item["id"] == fact.id for item in replay["facts"])

    bot_core.user_chats.pop(user_id, None)
    bot_core.user_message_counts.pop(user_id, None)
