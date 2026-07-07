"""Core bot runtime: sessions, LLM routing, compress."""
from __future__ import annotations

import asyncio
import logging
import re as _re
import time
import uuid
from datetime import datetime
from typing import Any

from aiogram import types
from aiogram.enums import ChatAction

from companion.background_scheduler import (
    _last_reflection_time,
    _REFLECTION_COOLDOWN_SECONDS,
    background_user_model_reflection,
    run_background_tasks,
    safe_task,
)
from companion.config import BASE_DIR, SUMMARY_THRESHOLD
from companion.context import ContextAggregator
from companion.critique_manager import apply_critique_to_text, run_self_critique
from companion.grounding_handler import (
    grounding_answer_only,
    should_retry_with_grounding,
)
from companion.llm import client as llm
from companion.llm.analyzer import analyze_message
from companion.llm.pipeline import run_compress_pipeline
from companion.llm.sessions import create_default_session
from companion.memory.retrieval import RetrievalBudgetManager
from companion.memory.store import MemoryStore
from companion.models import Fact
from companion.policy_layer import policy_layer
from companion.policy_layer import UserState as PolicyUserState
from companion.reasoning import reasoning_engine
from companion.runtime_state import RuntimeState
from companion.services import memory_service, report_service

logger = logging.getLogger(__name__)

# Global singletons
memory_store = MemoryStore()
retrieval_mgr = RetrievalBudgetManager()
context_aggregator = ContextAggregator(memory_store.db)

# In-memory sessions
user_chats: dict[int, Any] = {}
user_message_counts: dict[int, int] = {}

# Rate limiter for LLM requests
_user_request_times: dict[int, list[float]] = {}
_compressing_users: set[int] = set()
_MAX_REQUESTS_PER_MINUTE = 10

# Temportal sync: tracks last user activity timestamp
last_activity: dict[int, float] = {}


async def proactive_ping_loop(bot):
    """Каждую минуту проверяет prospective memory и обычную проактивность."""
    from datetime import datetime
    from companion.proactive.loop import run_proactive_loop
    
    while True:
        try:
            await asyncio.sleep(60)
            now_dt = datetime.now()
            hour = now_dt.hour
            # Не пингуем ночью
            if not (10 <= hour < 23):
                continue
                
            # Запуск нового детерминированного лупа
            await run_proactive_loop(bot, debug=False)
            
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("proactive_ping_loop crashed, restarting...")


async def send_typing(message: types.Message):
    """Send typing indicator once."""
    try:
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    except Exception:
        pass


async def send_long_message(message: types.Message, text: str) -> None:
    max_len = 4000
    while len(text) > max_len:
        split = max_len
        m = _re.search(r"[.!?]\s", text[max_len - 200:max_len])
        if m:
            split = max_len - 200 + m.end()
        else:
            m = _re.search(r"\s", text[max_len - 100:max_len])
            if m:
                split = max_len - 100 + m.start()
            else:
                # No split point found — force split at max_len to prevent infinite loop
                split = max_len
        await message.answer(text[:split])
        text = text[split:].lstrip()
    if text:
        await message.answer(text)


async def wait_gemini_file_ready(uploaded: Any, timeout: int = 120) -> Any:
    for _ in range(timeout // 2):
        if uploaded.state.name != "PROCESSING":
            return uploaded
        await asyncio.sleep(2)
        uploaded = await llm.run_llm(llm.get_file, uploaded.name)
    raise TimeoutError(f"File {uploaded.name} not ready after {timeout}s")


def _query_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, str):
            return first
    return ""


async def _persist_session(user_id: int) -> None:
    await asyncio.to_thread(memory_store.db.save_session, user_id, user_message_counts.get(user_id, 0))


async def reset_context(message: types.Message) -> None:
    uid = message.from_user.id
    if uid in user_chats:
        del user_chats[uid]
        user_message_counts.pop(uid, None)
        await message.answer("Чат сброшен. Память в SQLite сохранена.")
    else:
        await message.answer("Активного чата нет.")


async def show_goals(message: types.Message) -> None:
    goals = reasoning_engine.list_goals("active")
    if not goals:
        await message.answer("Нет целей. Сформулируй цель обычным текстом, например: 'моя цель - ...'")
        return
    lines = ["🎯 Твои цели:\n"]
    for goal in goals:
        status_emoji = {"active": "🟢", "paused": "⏸️", "completed": "✅", "abandoned": "❌"}.get(goal.status, "⚪")
        priority_bar = "█" * goal.priority + "░" * (10 - goal.priority)
        lines.append(f"{status_emoji} [{priority_bar}] {goal.title}")
        if goal.description:
            lines.append(f"   {goal.description[:60]}")
        lines.append(f"   ID: {goal.goal_id}\n")
    await send_long_message(message, "\n".join(lines))


async def add_goal_from_text(message: types.Message, text: str) -> None:
    from companion.reasoning import Goal
    title = _extract_goal_title(text)
    if not title:
        await message.answer("Не понял цель. Пример: 'моя цель - пройти собеседование'.")
        return
    existing = reasoning_engine.list_goals("active")
    if any(g.title.lower() == title.lower() for g in existing):
        await message.answer(f"⚠️ Цель уже существует: {title}")
        return
    goal = Goal(title=title, priority=5)
    reasoning_engine.add_goal(goal)
    await message.answer(f"✅ Цель добавлена: {title}\nID: {goal.goal_id}")


async def show_reasoning_state(message: types.Message) -> None:
    lines = ["🧠 Текущее состояние разума:\n"]
    active_goals = reasoning_engine.list_goals("active")
    if active_goals:
        lines.append("🎯 Активные цели:")
        for goal in active_goals[:3]:
            priority_bar = "█" * goal.priority + "░" * (10 - goal.priority)
            lines.append(f"  [{priority_bar}] {goal.title}")
        lines.append("")
    causal_links = reasoning_engine.list_causal_links(min_confidence=0.6)
    if causal_links:
        lines.append(f"🔗 Установлено {len(causal_links)} причинно-следственных связей")
        for link in causal_links[:3]:
            lines.append(f"  {link.cause} -> {link.effect} ({link.confidence:.0%})")
        lines.append("")
    world_model = reasoning_engine.world_model
    if world_model.get("active_contexts"):
        lines.append("🌍 Активные контексты:")
        for ctx in world_model["active_contexts"][:3]:
            lines.append(f"  • {ctx}")
        lines.append("")
    lines.append(f"Последнее обновление: {world_model.get('last_updated', '?')[:16]}")
    await send_long_message(message, "\n".join(lines))


async def show_todos(message: types.Message) -> None:
    todos = await memory_store.db.async_list_todos()
    if not todos:
        await message.answer("Список пуст.")
        return
    lines = [f"{i}. [{'✓' if t['done'] else '○'}] {t['text']}" for i, t in enumerate(todos, 1)]
    await message.answer("\n".join(lines))


async def add_todo(message: types.Message, text: str) -> None:
    task_text = text.strip()
    if not task_text:
        await message.answer("Что добавить в задачи?")
        return
    await memory_store.db.async_save_todo(f"todo_{uuid.uuid4().hex[:10]}", task_text)
    await message.answer("Задача добавлена.")


async def complete_todo(message: types.Message, text: str) -> None:
    todos = await memory_store.db.async_list_todos()
    idx = _extract_index(text) - 1
    if idx < 0 or idx >= len(todos):
        await message.answer("Нет такого номера.")
        return
    await memory_store.db.async_complete_todo(todos[idx]["id"])
    await message.answer("Готово.")


async def delete_todo(message: types.Message, text: str) -> None:
    todos = await memory_store.db.async_list_todos()
    idx = _extract_index(text) - 1
    if idx < 0 or idx >= len(todos):
        await message.answer("Нет такого номера.")
        return
    await memory_store.db.async_delete_todo(todos[idx]["id"])
    await message.answer("Готово.")


async def clear_done_todos(message: types.Message) -> None:
    deleted = await memory_store.db.async_clear_done_todos()
    await message.answer(f"Выполненные очищены: {deleted}.")


async def show_self_description(message: types.Message) -> None:
    from companion.self_model import self_model
    await send_long_message(message, self_model.get_self_description())


async def show_selfmap(message: types.Message) -> None:
    from companion.self_model import self_model
    km = self_model.data.get("knowledge_domains", {})
    lines = ["🗺️ Карта моих знаний о тебе:\n", "✅ Глубокое понимание:"]
    lines.extend(f"  • {topic}" for topic in km.get("deep_knowledge", []))
    lines.append("\n📖 Поверхностное понимание:")
    lines.extend(f"  • {topic}" for topic in km.get("surface_knowledge", []))
    lines.append("\n❓ Пробелы в знаниях:")
    lines.extend(f"  • {missing}" for missing in km.get("missing_data", []))
    await send_long_message(message, "\n".join(lines))


_compression_locks: dict[int, asyncio.Lock] = {}

def _get_compression_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _compression_locks:
        _compression_locks[user_id] = asyncio.Lock()
    return _compression_locks[user_id]

async def compress_and_reset(user_id: int) -> str | None:
    if user_id not in user_chats:
        return None

    lock = _get_compression_lock(user_id)
    if lock.locked():
        logger.info("Compression already in progress for user %d, skipping", user_id)
        return None

    async with lock:
        chat = user_chats[user_id]
        original_count = user_message_counts.get(user_id, 0)
        # Pre-decrement counter to prevent re-triggering during pipeline
        user_message_counts[user_id] = 0

        max_retries = 2
        try:
            for attempt in range(max_retries):
                try:
                    summary = await run_compress_pipeline(memory_store, chat, user_id)
                    if summary:
                        hist = [
                            llm.history_item("user", f"[Саммери]\n{summary}"),
                            llm.history_item("model", "Понял."),
                        ]
                        user_chats[user_id] = await llm.run_llm(
                            create_default_session, memory_store, retrieval_mgr, hist
                        )
                        await _persist_session(user_id)
                        return summary
                    else:
                        logger.warning(f"Compress attempt {attempt + 1}/{max_retries} returned empty summary")
                except Exception as e:
                    logger.error(f"Compress attempt {attempt + 1}/{max_retries} failed: {e}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2)

            user_message_counts[user_id] = user_message_counts.get(user_id, 0) + original_count
            logger.error(f"Compress failed after {max_retries} attempts for user {user_id}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error in compress_and_reset: {e}")
            user_message_counts[user_id] = user_message_counts.get(user_id, 0) + original_count
            return None


async def _restore_sessions() -> None:
    """Load persisted session state from SQLite into memory."""
    if not user_chats:
        saved = await asyncio.to_thread(memory_store.db.load_sessions)
        for uid, count in saved.items():
            if uid not in user_chats:
                user_message_counts[uid] = count
        if saved:
            logger.info("Restored %d sessions from database", len(saved))


async def build_context(message: types.Message, content_payload: Any) -> dict | None:
    """Unified preprocessing: rate limit, session, retrieval, policy, grounding."""
    await _restore_sessions()
    uid = message.from_user.id

    force_flash = False
    if isinstance(content_payload, str) and content_payload.startswith("!"):
        force_flash = True
        content_payload = content_payload[1:].strip()

    query = _query_text(content_payload)
    if not check_rate_limit(uid, message):
        return None

    state = RuntimeState()
    if isinstance(content_payload, str) and content_payload.strip():
        state.user_message = content_payload

        # LLM-based analysis replaces mood_lite, importance heuristics, and intent regex
        analysis = await asyncio.to_thread(analyze_message, content_payload)
        state.message_importance = analysis["estimated_importance"]
        state.mood_state = analysis["user_mood"]
        state.intent = analysis["intent"]
        state.intent_confidence = analysis["confidence"]
        state.user_state = analysis["user_state"]
        state.command = analysis["command"]

        await asyncio.to_thread(
            memory_store.log_message,
            "user", content_payload, analysis["estimated_importance"],
            "default", [], uid
        )
        safe_task(
            _extract_prospective_memory(content_payload),
            "prospective_memory_extract",
        )
        state.reasoning_context = await asyncio.to_thread(
            reasoning_engine.auto_reasoning_context, content_payload, analysis["estimated_importance"]
        )
        async with memory_store.lock:
            await asyncio.to_thread(memory_service.auto_add_event_from_message, content_payload, analysis["estimated_importance"])

    intent = state.intent or "memory"
    conf = state.intent_confidence or 0.6
    policy_decision = _get_policy_decision(state, query)
    state.policy_constraints = policy_decision.constraints if policy_decision else None
    ctx_data = await _load_retrieval_context(query, state.reasoning_context)

    # Блокируем web search для технических/кодовых запросов — ответ только из памяти
    _coding_keywords = {"питон", "python", "код", "скрипт", "бот"}
    _is_coding_query = query and any(kw in query.lower() for kw in _coding_keywords)
    if _is_coding_query and intent in ("world", "mixed"):
        state.intent = "memory" if intent == "world" else "chat_casual"

    # Фоновый поиск удалён — ручной поиск через /search работает в handlers/chat.py
    if uid not in user_chats:
        await _init_user_session(uid, query)
    user_message_counts[uid] = user_message_counts.get(uid, 0) + 1
    run_background_tasks(uid, state, memory_store, user_message_counts)
    if user_message_counts[uid] >= SUMMARY_THRESHOLD:
        await message.answer("Сжимаю контекст...")
        await compress_and_reset(uid)

    return {
        "uid": uid, "query": query, "state": state, "intent": intent,
        "policy_decision": policy_decision, "ctx_data": ctx_data,
        "force_flash": force_flash, "content_payload": content_payload,
    }

from companion.config import LLM_COMMAND_CONFIDENCE_THRESHOLD

SAFE_COMMANDS = {
    "show_facts", "show_notes", "export_diary", "show_timeline",
    "show_context", "week_digest", "retrospective", "selfie",
    "show_goals", "show_reasoning", "self_description",
    "knowledge_map", "show_todos", "monthbook", "show_year"
}

MUTATING_COMMANDS = {
    "reset_context", "clear_done", "complete_todo",
    "delete_todo", "add_goal", "diary_entry", "add_todo"
}

PENDING_COMMANDS: dict[str, dict[str, Any]] = {}
_PENDING_COMMAND_TTL_SECONDS = 15 * 60


def cleanup_pending_commands(now: float | None = None) -> None:
    current = now if now is not None else time.time()
    expired = [
        cmd_id for cmd_id, pending in PENDING_COMMANDS.items()
        if current - float(pending.get("created_at", 0)) > _PENDING_COMMAND_TTL_SECONDS
    ]
    for cmd_id in expired:
        PENDING_COMMANDS.pop(cmd_id, None)

async def _route_command(message: types.Message, command: str, text: str) -> bool:
    """Route a command from LLM analysis to the appropriate service."""
    routing = {
        "reset_context": reset_context,
        "show_facts": memory_service.show_facts,
        "show_notes": memory_service.show_notes,
        "export_diary": memory_service.export_diary,
        "show_timeline": memory_service.show_timeline,
        "show_context": report_service.show_context,
        "week_digest": report_service.show_week_digest,
        "retrospective": report_service.show_retrospective,
        "selfie": report_service.show_selfie,
        "show_goals": show_goals,
        "show_reasoning": show_reasoning_state,
        "self_description": show_self_description,
        "knowledge_map": show_selfmap,
        "show_todos": show_todos,
        "clear_done": clear_done_todos,
    }

    handler = routing.get(command)
    if handler:
        await handler(message)
        return True

    if command == "add_goal":
        payload = _strip_prefix(text, ["моя цель", "я хочу"])
        await add_goal_from_text(message, payload if payload else text)
        return True

    if command == "diary_entry":
        payload = _strip_prefix(text, ["запиши в дневник", "добавь в дневник", "сохрани в дневник"])
        await memory_service.add_diary_entry(message, payload if payload else text)
        return True

    if command == "add_todo":
        payload = _strip_prefix(text, ["добавь задачу", "создай задачу", "новая задача"])
        await add_todo(message, payload if payload else text)
        return True

    if command == "complete_todo":
        await complete_todo(message, text)
        return True

    if command == "delete_todo":
        await delete_todo(message, text)
        return True

    if command == "monthbook":
        match = _re.search(r"(20\d{2}-\d{2})", text)
        await report_service.show_monthbook(message, match.group(1) if match else None)
        return True

    if command == "show_year":
        year_match = _re.search(r"\d{4}", text)
        if year_match:
            await memory_service.show_year(message, year_match.group())
            return True

    return False


def _strip_prefix(text: str, prefixes: list[str]) -> str:
    lowered = text.lower().strip()
    for prefix in prefixes:
        if lowered.startswith(prefix):
            return text[len(prefix):].strip(" :-—")
    return text


def is_explicit_search_request(payload: Any) -> bool:
    text = ""
    if isinstance(payload, str):
        text = payload
    elif isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, str):
            text = first
    if not text:
        return False
    lowered = text.lower()
    search_keywords = ["интернет", "google", "гугл", "погугли", "поищи"]
    if any(kw in lowered for kw in search_keywords):
        action_verbs = ["посмотри", "найди", "поищи", "поиск", "проверь", "узнай", "загугли", "погугли"]
        if any(verb in lowered for verb in action_verbs):
            return True
        if any(lowered.startswith(verb) for verb in ["найди ", "поищи ", "погугли ", "загугли "]):
            return True
    return False


async def process_llm_request(message: types.Message, content_payload: Any) -> None:
    cleanup_pending_commands()
    last_activity[message.from_user.id] = time.time()
    
    from companion.user_model import user_model
    from companion.proactive.engagement import record_user_replied
    record_user_replied(user_model)

    ctx = await build_context(message, content_payload)
    if ctx is None:
        return

    state = ctx["state"]

    # Route commands via LLM-analyzed intent
    if state.intent == "command" and state.command and state.intent_confidence >= LLM_COMMAND_CONFIDENCE_THRESHOLD:
        command = state.command
        if command in MUTATING_COMMANDS:
            if not isinstance(ctx["content_payload"], str) or not ctx["content_payload"].startswith("/"):
                import uuid
                from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                cmd_id = uuid.uuid4().hex[:8]
                PENDING_COMMANDS[cmd_id] = {
                    "command": command,
                    "payload": str(ctx["content_payload"]),
                    "uid": ctx["uid"],
                    "created_at": time.time(),
                }
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text="✅ Выполнить", callback_data=f"cmd_ok:{cmd_id}"),
                        InlineKeyboardButton(text="❌ Отмена", callback_data=f"cmd_no:{cmd_id}")
                    ]
                ])
                safe_task(message.answer(
                    f"⚠️ Команда `{command}` изменяет данные.\n"
                    f"LLM предложила это действие. Подтвердить выполнение?",
                    reply_markup=kb
                ), "destructive_confirm")
                state.command = None
                return

        try:
            if await _route_command(message, command, str(ctx["content_payload"])):
                await asyncio.to_thread(
                    memory_store.log_message,
                    "assistant",
                    f"[Выполнена команда: {command}]",
                    4, "command", [], ctx["uid"]
                )
                return
        finally:
            state.command = None

    # Handle explicit search request
    if is_explicit_search_request(ctx["content_payload"]):
        from companion.grounding_handler import handle_grounding
        if await handle_grounding(
            message=message,
            query=ctx["query"],
            ctx_data=ctx["ctx_data"],
            uid=ctx["uid"],
            retrieval_mgr=retrieval_mgr,
            memory_store=memory_store
        ):
            return

    chat = user_chats[ctx["uid"]]
    await _generate_and_send_response(
        message, chat, state, ctx["content_payload"], ctx["query"],
        ctx["ctx_data"], ctx["policy_decision"], ctx["uid"],
        force_flash=ctx.get("force_flash", False)
    )


async def _extract_prospective_memory(text: str) -> None:
    try:
        from companion.proactive.prospective import extract_prospective_tasks
        created = await extract_prospective_tasks(memory_store, text)
        if created:
            logger.info("Prospective memory captured %d task(s)", created)
    except Exception as exc:
        logger.debug("Prospective memory extraction failed: %s", exc)


def check_rate_limit(uid: int, message: types.Message) -> bool:
    now = time.time()
    if uid not in _user_request_times:
        _user_request_times[uid] = []
    _user_request_times[uid] = [t for t in _user_request_times[uid] if now - t < 60]

    if len(_user_request_times[uid]) >= _MAX_REQUESTS_PER_MINUTE:
        safe_task(message.answer(
            "\u23f3 Слишком много запросов. Подожди минуту."
        ), "rate_limit_message")
        return False

    _user_request_times[uid].append(now)
    return True


def _get_policy_decision(state: RuntimeState, query: str) -> Any | None:
    if state.user_message:
        user_state = policy_layer.from_analyzer_state(state.user_state)
        policy_decision = policy_layer.decide_policy(
            user_state=user_state,
            message_context={"user_message_length": len(state.user_message)}
        )
        logger.info(f"Policy decision: mode={policy_decision.response_mode.value} (from analyzer state={state.user_state})")
        return policy_decision
    return None


async def _load_retrieval_context(query: str = "", reasoning_context: dict[str, Any] | None = None):
    """All operations here are blocking I/O (SQLite, file reads, embedding API).
    Must run via to_thread to avoid freezing the event loop."""
    def _load_sync() -> dict[str, Any]:
        all_facts = memory_store.list_facts("active")
        if query:
            search_results = memory_store.search_facts(query, limit=30)
            searched = [f for f, _ in search_results]
            faiss_scores = {f.id: score for f, score in search_results}
            merged = {f.id: f for f in searched}
            for f in all_facts:
                if f.memory_kind == "permanent" or f.importance >= 9 or any(
                    t.lower() in ["pinned", "core_identity", "anchor"] for t in f.tags
                ):
                    merged[f.id] = f
            facts_list = list(merged.values())
        else:
            faiss_scores = {}
            facts_list = all_facts

        return {
            "facts": facts_list,
            "reflections": memory_store.search_reflections(query, limit=10) if query else memory_store.list_reflections("active")[:10],
            "summaries": memory_store.search_summaries(query, limit=3) if query else memory_store.load_recent_summaries(3),
            "permanent_notes": "\n".join(memory_store.db.list_permanent_notes()),
            "identity_vault_block": "",
            "personality": memory_store.build_canonical_profile_text(),
            "user_model_context": "",
            "recent": memory_store.recent_messages(min_importance=6, limit=10),
            "active_goals": reasoning_context.get("active_goals", []) if reasoning_context else [],
            "causal_links": reasoning_context.get("causal_links", []) if reasoning_context else [],
            "predictions": [],
            "world_model_context": reasoning_context.get("world_model_context", "") if reasoning_context else "",
            "faiss_scores": faiss_scores,
            "runtime_context_block": context_aggregator.build_prompt_block(),
        }

    return await asyncio.to_thread(_load_sync)


async def _init_user_session(uid, query):
    latest_list = memory_store.load_recent_summaries(1)
    latest = latest_list[0] if latest_list else ""
    # Раньше summary-контекст передавался как history, из-за чего guard
    # "if not history" в create_default_session не срабатывал и последние
    # сообщения (несжатое окно) НЕ реконструировались → забывание после рестарта.
    summary_hist = (
        [llm.history_item("user", f"[Контекст]\n{latest}"), llm.history_item("model", "Понял.")]
        if latest else []
    )
    user_chats[uid] = await llm.run_llm(
        create_default_session, memory_store, retrieval_mgr, summary_hist, query
    )
    await _persist_session(uid)


async def _generate_and_send_response(message, chat, state, content_payload, query, ctx_data, policy_decision, uid, force_flash: bool = False):
    bundle = None
    ctx_block = None
    if isinstance(content_payload, str) and query:
        bundle = retrieval_mgr.select(
            query=query, facts=ctx_data["facts"], reflections=ctx_data["reflections"],
            summaries=ctx_data["summaries"], permanent_notes=ctx_data["permanent_notes"],
            identity_vault_block=ctx_data.get("identity_vault_block", ""),
            personality_snapshot=ctx_data["personality"], recent_messages=ctx_data["recent"],
            active_goals=ctx_data.get("active_goals", []),
            causal_links=ctx_data.get("causal_links", []),
            predictions=[],
            world_model_context=ctx_data.get("world_model_context", ""),
            user_model_context=ctx_data.get("user_model_context", ""),
            unified_profile_block=ctx_data.get("unified_profile_block", ""),
            mood=state.mood_state,
            faiss_scores=ctx_data.get("faiss_scores", {}),
            runtime_context_block=ctx_data.get("runtime_context_block", ""),
        )
        ctx_block = bundle.to_prompt_block()
        user_block = _build_user_prompt_block(content_payload, state.reasoning_context, ctx_block)
        if state.policy_constraints and policy_decision:
            content_payload = policy_layer.format_prompt_with_policy(
                base_prompt=user_block,
                policy=policy_decision
            )
        else:
            content_payload = user_block

    desired_model = "gemini-3.1-flash-lite" if force_flash else "gemma-4-31b-it"
    current_model = getattr(chat, "model", None)
    
    # We must unconditionally recreate the chat session on every turn to inject the updated RAG context
    # into the system_instruction, because we no longer inject it into the user_block.
    if current_model != desired_model:
        logger.info(f"[ROUTER] Switching model from {current_model} to {desired_model}")
        
    history = chat.get_history() if hasattr(chat, "get_history") else getattr(chat, "history", getattr(chat, "_curated_history", []))
    from companion.llm.sessions import build_system_instruction
    chat = llm.client.chats.create(
        model=desired_model,
        history=history,
        config=llm.make_config(
            system_instruction=build_system_instruction(
                memory_store, retrieval_mgr, query, precomputed_context=ctx_block
            ),
            temperature=0.7,
        )
    )
    user_chats[uid] = chat

    try:
        async def typing_loop():
            try:
                while True:
                    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
                    await asyncio.sleep(4)
            except asyncio.CancelledError:
                pass
            except Exception as ex:
                logger.debug(f"Typing loop error: {ex}")

        typing_task = asyncio.create_task(typing_loop())

        try:
            try:
                response = await llm.run_llm(chat.send_message, content_payload, timeout=250)
            except Exception as e:
                if not force_flash:
                    logger.warning(f"[ROUTER] [WARN] Gemma failed, falling back to Flash-lite: {e}")
                    history = chat.get_history()
                    from companion.llm.sessions import build_system_instruction
                    chat = llm.client.chats.create(
                        model="gemini-3.1-flash-lite",
                        history=history,
                        config=llm.make_config(
                            system_instruction=build_system_instruction(
                                memory_store, retrieval_mgr, query, precomputed_context=ctx_block
                            ),
                            temperature=0.7,
                        )
                    )
                    user_chats[uid] = chat
                    response = await llm.run_llm(chat.send_message, content_payload, timeout=250)
                else:
                    raise e
        finally:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass

        text = _extract_response_text(response)
        if not text:
            await message.answer("API вернул пустой ответ без текста.")
            state.llm_response = ""
            return

        if state.policy_constraints and policy_decision and text:
            text = policy_layer.enforce_constraints(text, state.policy_constraints)

        critique = run_self_critique(query, text, ctx_data)
        state.critique_result = critique
        if text:
            text = apply_critique_to_text(text, critique)
        if critique.get("confidence", 1.0) < 0.55 and query and should_retry_with_grounding(query, critique):
            grounded = await grounding_answer_only(query, ctx_data, retrieval_mgr)
            if grounded:
                text = grounded

        await send_long_message(message, text)
        msg_rec = None
        if text:
            msg_rec = await asyncio.to_thread(
                memory_store.log_message,
                "assistant", text, 5, "default", [], uid
            )
        state.llm_response = text

        if bundle and ctx_block and msg_rec and text:
            try:
                fs, fu, gs, gu, rs, ru = _analyze_context_utilization(text, bundle)
                await asyncio.to_thread(
                    memory_store.db.insert_retrieval_metrics,
                    msg_rec.id, fs, fu, gs, gu, rs, ru
                )
            except Exception as e:
                logger.error(f"Failed to record retrieval metrics: {e}")

        if state.message_importance >= 7 and text:
            now = time.time()
            if now - _last_reflection_time.get(uid, 0) >= _REFLECTION_COOLDOWN_SECONDS:
                _last_reflection_time[uid] = now
                safe_task(background_user_model_reflection(state, memory_store), "user_model_reflection")
    except Exception as e:
        logger.error(f"LLM error: {e}")
        logger.error("LLM error details: %s", e, exc_info=True)
        await message.answer("Произошла ошибка при обработке запроса. Подробности в логах.")


def _analyze_context_utilization(response_text: str, bundle: Any) -> tuple[int, int, int, int, int, int]:
    facts_sent = len(bundle.facts) if bundle.facts else 0
    reflections_sent = len(bundle.reflections) if bundle.reflections else 0
    goals_sent = len(bundle.active_goals) if bundle.active_goals else 0

    facts_used = 0
    reflections_used = 0
    goals_used = 0

    resp_lower = response_text.lower()

    if bundle.facts:
        sent_ids = []
        used_ids = []
        for f in bundle.facts:
            sent_ids.append(f.id)
            f_text = f.fact.lower()
            words = [w for w in _re.findall(r'[а-яёa-z0-9]+', f_text) if len(w) > 3]
            used = False
            if words:
                matches = sum(1 for w in words if w in resp_lower)
                if matches / len(words) >= 0.5:
                    facts_used += 1
                    used = True
                    used_ids.append(f.id)
            
        try:
            memory_store.db.increment_fact_usage_batch(sent_ids, used_ids)
        except Exception as e:
            logger.error("Failed to batch increment fact usage: %s", e)

    if bundle.reflections:
        for r in bundle.reflections:
            r_text = r.insight.lower()
            words = [w for w in _re.findall(r'[а-яёa-z0-9]+', r_text) if len(w) > 3]
            if not words:
                continue
            matches = sum(1 for w in words if w in resp_lower)
            if matches / len(words) >= 0.5:
                reflections_used += 1

    if bundle.active_goals:
        for g in bundle.active_goals:
            g_clean = _re.sub(r'^•\s*\[\d+/\d+\]\s*', '', g).lower()
            words = [w for w in _re.findall(r'[а-яёa-z0-9]+', g_clean) if len(w) > 3]
            if not words:
                continue
            matches = sum(1 for w in words if w in resp_lower)
            if matches / len(words) >= 0.5:
                goals_used += 1

    return facts_sent, facts_used, goals_sent, goals_used, reflections_sent, reflections_used


def _extract_response_text(response: Any) -> str:
    try:
        text = getattr(response, "text", None)
    except Exception as e:
        logger.warning("Failed to extract Gemini response text: %s", e)
        return ""
    return text if isinstance(text, str) else ""


def _extract_index(text: str) -> int:
    match = _re.search(r"\b(\d+)\b", text)
    return int(match.group(1)) if match else 0


def _extract_goal_title(text: str) -> str:
    patterns = [
        r"моя цель\s*[-—:]?\s*(.+)",
        r"цель\s*[-—:]?\s*(.+)",
        r"хочу\s+(.+)",
    ]
    lowered = text.strip()
    for pattern in patterns:
        match = _re.search(pattern, lowered, _re.IGNORECASE)
        if match:
            return match.group(1).strip(" .")
    return ""


def fact_from_permanent_note(note: str) -> Fact:
    return Fact(
        fact=note,
        date=datetime.now().strftime("%Y-%m-%d"),
        importance=9,
        confidence=1.0,
        source="permanent_note",
        source_type="user",
        memory_kind="permanent",
        tags=["permanent"],
    )


def _build_user_prompt_block(content_payload: str, reasoning_context: dict[str, Any], retrieval_context: str) -> str:
    now = datetime.now()
    parts = [f"[Системное время: {now.strftime('%Y-%m-%d %H:%M')}]"]
    parts.append(f"[Сообщение пользователя]\n{content_payload}")
    if reasoning_context.get("causal_trigger"):
        parts.append("[Режим reasoning]\nПользователь спрашивает о причинах. Используй причинно-следственный анализ, если контекст это поддерживает.")
    if reasoning_context.get("future_trigger"):
        parts.append("[Режим reasoning]\nПользователь спрашивает о будущем. Учитывай прогнозы, условия и степень неопределенности.")
    # REMOVED: Do not inject retrieval_context into the user message history to prevent 429 quota exhaustion and 260k context limit violation.
    # It is already injected into the system instruction.
    return "\n\n".join(parts)
