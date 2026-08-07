"""Core bot runtime: sessions, LLM routing, compress."""
from __future__ import annotations

import asyncio
import logging
import os
import re as _re
import time
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
from companion.config import FAISS_FLUSH_INTERVAL_SECONDS, SUMMARY_THRESHOLD
from companion.config import LLM_HISTORY_TOKEN_BUDGET, LLM_INPUT_TOKEN_BUDGET
from companion.context import ContextAggregator
from companion.critique_manager import apply_critique_to_text, run_self_critique
from companion.llm import client as llm
from companion.llm.analyzer import analyze_message
from companion.llm.pipeline import run_compress_pipeline
from companion.llm.sessions import create_default_session
from companion.memory.retrieval import RetrievalBudgetManager
from companion.memory.store import MemoryStore
from companion.models import Fact
from companion.llm.token_budget import estimate_tokens, trim_history
from companion import observability
from companion.policy_layer import policy_layer
from companion.reasoning import reasoning_engine
from companion.runtime_state import RuntimeState
from companion.handlers import commands

logger = logging.getLogger(__name__)

# Global singletons
memory_store = MemoryStore()
retrieval_mgr = RetrievalBudgetManager(store=memory_store)
context_aggregator = ContextAggregator(memory_store.db)

# Embedding retry worker (initialized after memory_store)
embedding_retry_worker = None

# In-memory sessions
user_chats: dict[int, Any] = {}
user_message_counts: dict[int, int] = {}
_importance_accumulator: dict[int, float] = {}  # tracks accumulated importance for smart compress

# Rate limiter for LLM requests
_user_request_times: dict[int, list[float]] = {}
_rate_checked_messages: dict[int, set[int]] = {}  # uid -> message_ids already counted
_compressing_users: set[int] = set()
_MAX_REQUESTS_PER_MINUTE = 10

# Temportal sync: tracks last user activity timestamp
last_activity: dict[int, float] = {}


async def proactive_ping_loop(bot):
    """Каждую минуту проверяет prospective memory и обычную проактивность."""
    from datetime import datetime
    from companion.proactive.loop import run_proactive_loop
    
    # Initialize embedding retry worker on first loop iteration
    global embedding_retry_worker
    if embedding_retry_worker is None:
        from companion.memory.embedding_retry_worker import init_embedding_retry_worker
        embedding_retry_worker = init_embedding_retry_worker(memory_store)
        await embedding_retry_worker.start()
        logger.info("Embedding Retry Worker initialized and started")
    
    last_subconscious_run = 0.0
    last_dreaming_run = 0.0
    last_health_check = 0.0
    last_faiss_flush = 0.0
    last_replay_learning_date = ""
    
    while True:
        try:
            await asyncio.sleep(60)
            now_dt = datetime.now()
            if now_dt.timestamp() - last_faiss_flush >= FAISS_FLUSH_INTERVAL_SECONDS:
                await asyncio.to_thread(memory_store.vector.flush_index)
                last_faiss_flush = now_dt.timestamp()
            hour = now_dt.hour

            if hour == 4 and last_replay_learning_date != now_dt.date().isoformat():
                from evaluation.learning import learn_replays
                learned = await learn_replays(memory_store, limit=10)
                logger.info("Nightly replay learning annotated %d replay(s)", learned)
                last_replay_learning_date = now_dt.date().isoformat()
            
            # Фоновое "Подсознание" (Background Consolidation) запускаем ночью (3:00 - 4:59)
            if 3 <= hour < 5:
                if (now_dt.timestamp() - last_health_check) > 12 * 3600:
                    from companion.memory.health import memory_health
                    from companion.memory.consolidation import (
                        consolidate_if_due, decay_fact_confidence, promote_patterns_to_insights,
                        revalidate_insight_provenance, reconcile_genome_parity, audit_provenance_cycles,
                        compute_homeostasis, homeostasis_sleep_due,
                    )
                    health = await asyncio.to_thread(memory_health, memory_store)
                    await asyncio.to_thread(consolidate_if_due, memory_store, 7)
                    await asyncio.to_thread(decay_fact_confidence, memory_store)
                    try:
                        promoted = await asyncio.to_thread(promote_patterns_to_insights, memory_store)
                        if promoted:
                            logger.info("Promoted %d time-earned pattern(s) into personality model", promoted)
                    except Exception as exc:
                        logger.error("Pattern promotion failed: %s", exc, exc_info=True)
                    try:
                        # Traits must answer to their sources, or an old mistake
                        # becomes an immortal personality trait.
                        prov = await asyncio.to_thread(revalidate_insight_provenance, memory_store)
                        if prov.get("weakened") or prov.get("refuted"):
                            logger.info("Provenance revalidation: %s", prov)
                    except Exception as exc:
                        logger.error("Provenance revalidation failed: %s", exc, exc_info=True)
                    try:
                        # R2: genome 1:1 invariant backfill for facts created
                        # before the cognitive schema existed.
                        parity = await asyncio.to_thread(reconcile_genome_parity, memory_store)
                        if parity.get("backfilled"):
                            logger.info("Genome parity backfilled %d fact(s)", parity["backfilled"])
                    except Exception as exc:
                        logger.error("Genome parity reconcile failed: %s", exc, exc_info=True)
                    try:
                        # R2: circular-provenance detector. A cycle means A justifies
                        # B and B justifies A — quarantine the members, never delete.
                        cycles = await asyncio.to_thread(audit_provenance_cycles, memory_store)
                        if cycles:
                            logger.warning("Provenance cycles detected: %d", len(cycles))
                            for cyc in cycles:
                                for fid in cyc[:-1]:
                                    try:
                                        from companion.memory.policies.base import PolicyDecision
                                        memory_store.persistence.apply_decision(
                                            fid,
                                            PolicyDecision(
                                                approved=True, action="quarantine",
                                                updates={"status": "quarantine"},
                                                reason="provenance_cycle",
                                                policy_name="CycleAuditorPolicy",
                                            ),
                                            reason="provenance_cycle", initiator="cycle_auditor",
                                        )
                                    except Exception as qexc:
                                        logger.warning("Cycle quarantine failed for %s: %s", fid, qexc)
                    except Exception as exc:
                        logger.error("Provenance cycle audit failed: %s", exc, exc_info=True)
                    try:
                        # R4: homeostasis entropy. Pure metric — records the
                        # semantic-poisoning pressure trend; a 3-sample moving
                        # average over tau (0.35) flags a forced Sleep Cycle.
                        ho = await asyncio.to_thread(compute_homeostasis, memory_store)
                        sleep_due = await asyncio.to_thread(homeostasis_sleep_due, memory_store)
                        if sleep_due:
                            logger.warning(
                                "Homeostasis breach: entropy trend above tau (%.3f > %.2f); "
                                "running Sleep Cycle", ho["entropy"], ho["tau"])
                            # R4/R6: forced consolidation + immune scan.
                            from companion.memory.sleep import run_sleep_cycle
                            from companion.memory.immune import immune_audit
                            sleep_stats = await asyncio.to_thread(run_sleep_cycle, memory_store)
                            logger.info("Sleep Cycle complete: %s", sleep_stats)
                            immune = await asyncio.to_thread(immune_audit, memory_store)
                            logger.info("Immune audit: %s", immune)
                        else:
                            logger.info("Homeostasis entropy: %.4f (tau %.2f)", ho["entropy"], ho["tau"])
                    except Exception as exc:
                        logger.error("Homeostasis metric failed: %s", exc, exc_info=True)
                    logger.info("Nightly memory health: %s", health)
                    try:
                        # Phase B: consistent memory snapshot (SQLite + FAISS
                        # cache) once per health window. VACUUM INTO is
                        # transactionally consistent; restore = offline op.
                        from companion.memory.snapshot import create_snapshot
                        name = await asyncio.to_thread(create_snapshot, memory_store)
                        logger.info("Nightly snapshot created: %s", name)
                    except Exception as exc:
                        logger.error("Nightly snapshot failed: %s", exc, exc_info=True)
                    try:
                        moved = await asyncio.to_thread(memory_store.db.archive_audit_log, 30)
                        logger.info("Nightly audit rotation archived %d row(s)", moved)
                    except Exception as exc:
                        logger.error("Nightly audit rotation failed: %s", exc, exc_info=True)
                    last_health_check = now_dt.timestamp()
                if (now_dt.timestamp() - last_subconscious_run) > 12 * 3600:
                    from companion.proactive.subconscious import run_subconscious_consolidation
                    await run_subconscious_consolidation(bot, memory_store)
                    last_subconscious_run = now_dt.timestamp()

            # Фоновый "Сон и Внутренний монолог" (Memory Dreaming / Inner Diary) запускаем раз в 4 часа
            if (now_dt.timestamp() - last_dreaming_run) > 4 * 3600:
                from companion.proactive.inner_monologue import run_memory_dreaming_cycle
                from companion.user_model import user_model
                await run_memory_dreaming_cycle(memory_store, user_model)
                last_dreaming_run = now_dt.timestamp()
            
            # Не пингуем юзера ночью
            if not (10 <= hour < 23):
                continue
                
            # Запуск нового детерминированного лупа
            await run_proactive_loop(bot, debug=False)
            
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("proactive_ping_loop crashed, backing off...")
            # Exponential backoff: 10s, 20s, 40s ... capped at 5 minutes.
            # Without this, a persistent error (DB corrupt, API down) would
            # produce log spam at loop-iteration speed (~millisecond intervals).
            backoff = min(300, getattr(proactive_ping_loop, "_backoff", 10))
            proactive_ping_loop._backoff = backoff * 2  # type: ignore[attr-defined]
            await asyncio.sleep(backoff)
        else:
            # Reset backoff on successful iteration
            proactive_ping_loop._backoff = 10  # type: ignore[attr-defined]


async def send_typing(message: types.Message):
    """Send typing indicator once."""
    try:
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    except Exception as exc:
        logger.debug("Unable to send typing indicator: %s", exc, exc_info=True)


async def send_long_message(message: types.Message, text: str, **kwargs) -> None:
    """Send a long message, splitting into chunks of max 4000 chars.

    Tries to split at sentence/word boundaries. Falls back to hard split
    if no boundary found. Safety guard: split is clamped to >=1 to prevent
    infinite loops in edge cases.
    """
    max_len = 4000
    max_chunks = 100  # safety limit to prevent runaway loops
    chunks_sent = 0
    while len(text) > max_len:
        chunks_sent += 1
        if chunks_sent > max_chunks:
            # Safety valve: send remaining text as-is and stop
            await message.answer(text, **kwargs)
            return
        split = max_len
        m = _re.search(r"[.!?]\s", text[max_len - 200:max_len])
        if m:
            split = max_len - 200 + m.end()
        else:
            m = _re.search(r"\s", text[max_len - 100:max_len])
            if m:
                split = max_len - 100 + m.start()
        # Guarantee forward progress: split must consume at least 1 char
        split = max(1, min(split, max_len))
        await message.answer(text[:split], **kwargs)
        text = text[split:].lstrip()
    if text:
        await message.answer(text, **kwargs)


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


def _format_working_memory_block(slots: list[dict[str, Any]]) -> str:
    """R3/A1: format live working-memory slots into a compact prompt block.

    «Стол» перед моделью: текущая цель, активная идентичность, открытые
    вопросы, salient-факты и аффективное состояние — уже отфильтрованные по
    TTL и салиентности. Пусто -> пустая строка (блок не вставляется).
    """
    if not slots:
        return ""
    lines: list[str] = ["[Рабочая память — актуальное состояние диалога]"]
    for s in slots[:20]:
        st = str(s.get("slot_type", ""))
        payload = str(s.get("payload", "")).strip()
        if not payload:
            continue
        label = {
            "current_goal": "Текущая цель",
            "active_identity": "Активная идентичность",
            "open_question": "Открытый вопрос",
            "salient_fact": "Salient-факт",
            "affective_state": "Эмоциональный фон",
        }.get(st, st)
        lines.append(f"• {label}: {payload[:200]}")
    return "\n".join(lines)


def _load_genome_scores(fact_ids: list[str]) -> dict[str, float]:
    """Batch-load survival_score from the genome table for gravity scoring."""
    if not fact_ids:
        return {}
    scores: dict[str, float] = {}
    try:
        with memory_store.db._conn() as conn:
            for chunk_start in range(0, len(fact_ids), 400):
                chunk = fact_ids[chunk_start:chunk_start + 400]
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"SELECT memory_id, survival_score FROM memory_genome WHERE memory_id IN ({placeholders})",
                    chunk,
                ).fetchall()
                for r in rows:
                    scores[str(r[0])] = float(r[1] or 0.5)
    except Exception as exc:
        logger.debug("genome score load failed: %s", exc)
    return scores


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


async def show_life_continuity(message: types.Message) -> None:
    """Команда /continuity (алиас /lce): синтез траектории личности.

    reflect_on_self(): объединяет модель человека (снимок) и жизненные
    переходы (LCE, траекторию) в читаемый ответ 'что во мне изменилось'.
    pending_review не показываем (не проверено)."""
    hm = memory_store.get_human_model()
    transitions = [t for t in memory_store.get_recent_transitions(15)
                   if t.status not in ("pending_review",)]
    if not any(hm.all_insights()) and not transitions:
        await message.answer("Пока недостаточно данных, чтобы судить о траектории. "
                             "Дай мне пожить с тобой подольше.")
        return
    lines = ["🧭 Траектория личности (Life Continuity Engine):\n"]
    if transitions:
        lines.append("Что изменилось:")
        for i, t in enumerate(transitions, 1):
            dom = t.domain
            tag = ""
            if t.status == "completed":
                tag = " (завершён)"
            elif t.status == "reversed":
                tag = " (обращён)"
            lines.append(f"{i}. [{dom}]{tag} {t.from_state} → {t.to_state}")
            if t.explanation:
                lines.append(f"   почему: {t.explanation}")
    else:
        lines.append("Я пока не фиксирую устойчивых переходов — только текущее состояние:")
    # Текущий снимок (HumanModel) как контекст 'кто ты сейчас'
    for dim, label in (("goals", "Цели"), ("strengths", "Силы"),
                        ("fears", "Страхи"), ("recurring_mistakes", "Повторы"),
                        ("long_term_trends", "Тренды")):
        items = [i.text for i in getattr(hm, dim) if i.text]
        if items:
            lines.append(f"\n{label}:")
            lines.extend(f"  • {x}" for x in items[:8])
    await send_long_message(message, "\n".join(lines))

async def show_timeline(message: types.Message) -> None:
    """Команда /timeline: хронология изменений личности."""
    transitions = [t for t in memory_store.get_recent_transitions(50)
                   if t.status not in ("pending_review",)]
    
    if not transitions:
        await message.answer("Пока недостаточно данных для построения хронологии.")
        return

    transitions.sort(key=lambda x: getattr(x, "created_at", ""))

    lines = ["🕰 <b>Хронология трансформаций:</b>\n"]
    for i, t in enumerate(transitions, 1):
        date_str = getattr(t, "created_at", "")[:10]  # YYYY-MM-DD
        lines.append(f"<b>[{date_str}] {t.domain.upper()}</b>")
        lines.append(f"🔄 Изменение: <i>{t.from_state}</i> ➔ <b>{t.to_state}</b>")
        if t.explanation:
            lines.append(f"💡 Почему: {t.explanation}")
        if t.trigger_events:
            evs = "; ".join(t.trigger_events)
            lines.append(f"🔗 Триггеры: {evs}")
        lines.append("")

    await send_long_message(message, "\n".join(lines), parse_mode="HTML")


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

    logger.info("[COMPRESSION] Превышен лимит сообщений. Запуск фонового сжатия контекста...")
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
    if query.strip():
        state.user_message = query

        from evaluation.learning import annotate_previous_satisfaction
        await asyncio.to_thread(annotate_previous_satisfaction, memory_store, uid, query)

        # LLM-based analysis replaces mood_lite, importance heuristics, and intent regex
        started = time.perf_counter()
        analysis = await asyncio.to_thread(analyze_message, query)
        trace = observability.active_trace(uid)
        if trace:
            trace.timings_ms["analyzer"] = (time.perf_counter() - started) * 1000
        state.message_importance = analysis["estimated_importance"]
        state.mood_state = analysis["user_mood"]
        state.intent = analysis["intent"]
        state.intent_confidence = analysis["confidence"]
        state.user_state = analysis["user_state"]
        state.command = analysis["command"]
        state.needs_clarification = analysis.get("needs_clarification", "")

        from companion.user_model import user_model
        user_model.record_emotional_state(state.mood_state, state.user_state)

        await asyncio.to_thread(
            memory_store.log_message,
            "user", query, analysis["estimated_importance"],
            "default", [], uid
        )
        safe_task(
            _extract_prospective_memory(query),
            "prospective_memory_extract",
        )
        state.reasoning_context = await asyncio.to_thread(
            reasoning_engine.auto_reasoning_context, query, analysis["estimated_importance"]
        )
        async with memory_store.lock:
            await asyncio.to_thread(commands.auto_add_event_from_message, query, analysis["estimated_importance"])

    intent = state.intent or "memory"
    policy_decision = _get_policy_decision(state, query)
    state.policy_constraints = policy_decision.constraints if policy_decision else None
    logger.info(f"[MEMORY] Запуск RAG. Важность: {state.message_importance}. Сборка когнитивного контекста...")
    retrieval_started = time.perf_counter()
    ctx_data = await _load_retrieval_context(query, state.reasoning_context, state.message_importance, state.intent)
    trace = observability.active_trace(uid)
    if trace:
        trace.timings_ms["retrieval"] = (time.perf_counter() - retrieval_started) * 1000

    # Блокируем web search для технических/кодовых запросов — ответ только из памяти
    _coding_keywords = {"питон", "python", "код", "скрипт", "бот"}
    _is_coding_query = query and any(kw in query.lower() for kw in _coding_keywords)
    if _is_coding_query and intent in ("world", "mixed"):
        state.intent = "memory" if intent == "world" else "chat_casual"

    # R3 (K5): refresh the bounded working-memory slots for this turn. Runs in a
    # worker thread; guarded — working memory is an optimization, never a
    # correctness dependency of the conversation path.
    try:
        rc = state.reasoning_context or {}
        facts = ctx_data.get("facts") or []
        faiss_scores = ctx_data.get("faiss_scores") or {}
        top_facts = [(f, faiss_scores.get(f.id, 0.0)) for f in facts[:10]]
        await asyncio.to_thread(
            memory_store.working_memory.update_from_turn,
            user_id=uid,
            mood_state=state.mood_state,
            needs_clarification=state.needs_clarification or "",
            captured_goal=rc.get("captured_goal", "") or "",
            active_goals=rc.get("active_goals", []) or [],
            top_facts=top_facts,
        )
    except Exception as exc:
        logger.debug("working memory update skipped: %s", exc)

    # Фоновый поиск удалён — ручной поиск через /search работает в handlers/chat.py
    if uid not in user_chats:
        await _init_user_session(uid, query)
    user_message_counts[uid] = user_message_counts.get(uid, 0) + 1
    
    # Track accumulated importance — emotionally dense conversations
    # should compress sooner than light chatting.
    _importance_accumulator[uid] = _importance_accumulator.get(uid, 0) + state.message_importance
    
    run_background_tasks(uid, state, memory_store, user_message_counts)
    
    # Smart compress: trigger when EITHER:
    # 1. Message count reaches threshold (hard limit, prevents token overflow)
    # 2. Accumulated importance exceeds threshold (emotional conversations
    #    compress sooner because there's more worth extracting)
    importance_threshold = SUMMARY_THRESHOLD * 5  # ~250 importance points
    should_compress = (
        user_message_counts[uid] >= SUMMARY_THRESHOLD
        or _importance_accumulator.get(uid, 0) >= importance_threshold
    )
    if should_compress:
        await message.answer("Сжимаю контекст...")
        await compress_and_reset(uid)
        _importance_accumulator[uid] = 0  # reset after compress

    return {
        "uid": uid, "query": query, "state": state, "intent": intent,
        "policy_decision": policy_decision, "ctx_data": ctx_data,
        "force_flash": force_flash, "content_payload": content_payload,
    }

MULTIMODAL_PROMPT = """Проанализируй этот медиафайл, отправленный пользователем.
Выдели ключевые объекты, контекст и смысл. Извлеки 1-3 факта о жизни пользователя (например, вещи, домашние животные, хобби, локации, люди), которые стоит запомнить.
Верни ответ СТРОГО в формате JSON:
{{
  "description": "Краткое описание того, что ты увидел или услышал",
  "facts": ["факт 1", "факт 2"]
}}
"""

async def process_multimodal_request(message: types.Message):
    """Handles photo/voice inputs, extracts facts via Gemini Vision, and saves them to memory."""
    uid = message.from_user.id
    if not message.photo and not message.voice and not message.document:
        return

    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    
    import tempfile
    import json
    
    try:
        # 1. Download file
        temp_dir = tempfile.mkdtemp(prefix="companion-media-")
        if message.photo:
            file_id = message.photo[-1].file_id
            ext = ".jpg"
        elif message.voice:
            file_id = message.voice.file_id
            ext = ".ogg"
        else:
            file_id = message.document.file_id
            ext = os.path.splitext(message.document.file_name or "")[1]
            
        file = await message.bot.get_file(file_id)
        local_path = os.path.join(temp_dir, f"media{ext}")
        await message.bot.download_file(file.file_path, local_path)
        
        # 2. Upload to Gemini
        from companion.llm.client import aio_upload_file, aio_oneshot_multimodal, aio_delete_file
        uploaded_media = await aio_upload_file(local_path)
        
        # 3. Analyze
        prompt = MULTIMODAL_PROMPT
        if message.caption:
            prompt += f"\n\nПользователь добавил подпись: {message.caption}"
            
        response_text = await aio_oneshot_multimodal([uploaded_media, prompt])
        
        # 4. Parse & Save
        try:
            res = json.loads(response_text)
            desc = res.get("description", "")
            facts = res.get("facts", [])
            
            # Log as system fact
            for f in facts:
                fact_text = f"[Multimodal Memory] На медиафайле пользователя зафиксировано: {f}"
                await asyncio.to_thread(
                    memory_store.log_message,
                    "system", fact_text, 8,
                    "memory", ["vision", "audio"], uid
                )
                
            # Instead of a robotic log, pass the description to the persona pipeline
            synthetic_query = f"[Пользователь прислал медиафайл. Внутреннее зрение увидело: {desc}]"
            if message.caption:
                synthetic_query += f"\nКомментарий пользователя: {message.caption}"
            
            logger.info(f"[MULTIMODAL] Сгенерирован скрытый промпт: {synthetic_query}")
            
            # This triggers the normal chat pipeline (RAG, reasoning, and character response)
            await process_llm_request(message, synthetic_query)

            
        except json.JSONDecodeError:
            await message.reply("Я изучил(а) файл, но не смог(ла) структурировать факты. 🤷‍♂️")
            
        # Cleanup
        await aio_delete_file(uploaded_media.name)
        os.remove(local_path)
        os.rmdir(temp_dir)
        
    except Exception as e:
        logger.error(f"Multimodal processing error: {e}")
        await message.reply("У меня не получилось обработать этот файл. 😔")

from companion.config import LLM_COMMAND_CONFIDENCE_THRESHOLD

MUTATING_COMMANDS = {
    "reset_context", "add_goal", "diary_entry"
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
        "show_facts": commands.show_facts,
        "show_notes": commands.show_notes,
        "export_diary": commands.export_diary,
        "show_timeline": commands.show_timeline,
        "show_context": commands.show_context,
        "week_digest": commands.show_week_digest,
        "retrospective": commands.show_retrospective,
        "selfie": commands.show_selfie,
        "show_goals": show_goals,
        "show_reasoning": show_reasoning_state,
        "self_description": show_self_description,
        "knowledge_map": show_selfmap,
        "debug_retrieval": commands.show_debug_retrieval,
        "why": commands.show_why,
        "memory_stats": commands.show_memory_stats,
        "memory_health": commands.show_memory_health,
        "replay": commands.show_replay,
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
        await commands.add_diary_entry(message, payload if payload else text)
        return True


    if command == "monthbook":
        match = _re.search(r"(20\d{2}-\d{2})", text)
        await commands.show_monthbook(message, match.group(1) if match else None)
        return True

    if command == "show_year":
        year_match = _re.search(r"\d{4}", text)
        if year_match:
            await commands.show_year(message, year_match.group())
            return True

    if command == "inspect_fact":
        fact_id = text.strip().split()[-1] if text.strip() else ""
        if fact_id:
            await commands.inspect_fact(message, fact_id)
            return True

    if command == "memory_gc":
        await commands.run_memory_gc(message, apply=text.strip().lower() == "apply")
        return True

    if command == "replay":
        replay_id = text.strip().split()[-1] if text.strip() else ""
        if replay_id:
            await commands.show_replay(message, replay_id)
            return True

    return False


def _strip_prefix(text: str, prefixes: list[str]) -> str:
    lowered = text.lower().strip()
    for prefix in prefixes:
        if lowered.startswith(prefix):
            return text[len(prefix):].strip(" :-—")
    return text



from companion.llm.telemetry import observe

@observe(name="process_llm_request")
async def process_llm_request(message: types.Message, content_payload: Any) -> None:
    cleanup_pending_commands()
    uid = message.from_user.id
    last_activity[uid] = time.time()
    observability.begin_trace(uid, _query_text(content_payload))
    
    from companion.user_model import user_model
    from companion.proactive.engagement import record_user_replied
    record_user_replied(user_model)

    ctx = await build_context(message, content_payload)
    if ctx is None:
        observability.finish_trace(uid)
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
                observability.finish_trace(uid)
                return

        try:
            if await _route_command(message, command, str(ctx["content_payload"])):
                await asyncio.to_thread(
                    memory_store.log_message,
                    "assistant",
                    f"[Выполнена команда: {command}]",
                    4, "command", [], ctx["uid"]
                )
                observability.finish_trace(uid)
                return
        finally:
            state.command = None

    chat = user_chats[ctx["uid"]]
    logger.info("[GENERATION] Контекст собран. Отправка запроса на генерацию финального ответа пользователю...")
    await _generate_and_send_response(
        message, chat, state, ctx["content_payload"], ctx["query"],
        ctx["ctx_data"], ctx["policy_decision"], ctx["uid"],
        force_flash=ctx.get("force_flash", False)
    )
    trace = observability.finish_trace(uid)
    if trace:
        await asyncio.to_thread(observability.save_replay, trace, memory_store)


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
    msg_id = getattr(message, "message_id", None)
    if uid not in _user_request_times:
        _user_request_times[uid] = []
    _user_request_times[uid] = [t for t in _user_request_times[uid] if now - t < 60]

    # Idempotency: the same message (media flows call us from the handler AND
    # from build_context) must not be counted twice against the per-minute limit.
    if msg_id is not None:
        checked = _rate_checked_messages.setdefault(uid, set())
        if msg_id in checked:
            return True

    if len(_user_request_times[uid]) >= _MAX_REQUESTS_PER_MINUTE:
        safe_task(message.answer(
            "\u23f3 Слишком много запросов. Подожди минуту."
        ), "rate_limit_message")
        return False

    _user_request_times[uid].append(now)
    if msg_id is not None:
        _rate_checked_messages.setdefault(uid, set()).add(msg_id)
        # Bounded memory: drop stale tracking once it grows (10 req/min × 60 min).
        if len(_rate_checked_messages[uid]) > 600:
            _rate_checked_messages[uid].clear()
    return True


def _analyze_hidden_emotion(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
        
    chars = len(text)
    words = len(text.split())
    
    if words <= 3 and chars < 20:
        if "." in text and "!" not in text and "?" not in text:
            return "[СИСТЕМНОЕ СООБЩЕНИЕ: Пользователь отвечает сухо и коротко. Возможно, устал или занят. Поддержи его кратким ответом без давления.]"
        elif "!" not in text and "?" not in text:
            return "[СИСТЕМНОЕ СООБЩЕНИЕ: Пользователь отвечает кратко. Отвечай не слишком длинно (1-2 абзаца), но держи свой характер.]"
            
    if "..." in text or ".." in text:
        return "[СИСТЕМНОЕ СООБЩЕНИЕ: В тексте есть многоточия. Пользователь может быть в задумчивости, неуверенности или грусти. Отвечай мягко.]"
        
    import re
    if re.search(r"[А-ЯЁ]{4,}", text) and "!" in text:
        return "[СИСТЕМНОЕ СООБЩЕНИЕ: Пользователь пишет КАПСОМ и с восклицаниями. Возможен сильный эмоциональный всплеск. Отреагируй на эту интенсивность адекватно.]"
        
    return ""


def _get_policy_decision(state: RuntimeState, query: str) -> Any | None:
    # Temporarily disabled per user request
    return None


async def _load_retrieval_context(query: str = "", reasoning_context: dict[str, Any] | None = None, importance: int = 5, intent: str = ""):
    """All operations here are blocking I/O (SQLite, file reads, embedding API).
    Must run via to_thread to avoid freezing the event loop."""
    def _load_sync() -> dict[str, Any]:
        all_facts = memory_store.list_facts("active")
        if query:
            dynamic_limit = max(5, int(5 + (importance * 6.5)))
            search_results = memory_store.search_facts(query, limit=dynamic_limit)
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

        raw_goals = memory_store.db.list_goals(status="active")[:2]
        formatted_goals = [f"{g.get('title', '')}: {g.get('description', '')}" for g in raw_goals]

        # Timeline block: последние 10 событий
        events = memory_store.db.load_events()
        timeline_lines = []
        for e in events[-10:]:
            timeline_lines.append(f"{e['date']} — {e['event']}")
        timeline_block = "\n".join(timeline_lines)

        return {
            "facts": facts_list,
            "reflections": memory_store.search_reflections(query, limit=10) if query else memory_store.list_reflections("active")[:10],
            "patterns": memory_store.search_patterns(query, limit=10) if query else memory_store.list_patterns("active")[:10],
            "summaries": memory_store.search_summaries(query, limit=3) if query else memory_store.load_recent_summaries(3),
            "permanent_notes": "\n".join(memory_store.db.list_permanent_notes()),
            "identity_vault_block": "",
            "personality": memory_store.build_canonical_profile_text(),
            "user_model_context": "",
            "comm_prefs": memory_store.get_comm_pref(),
            "human_model": memory_store.get_human_model(),
            "life_transitions": memory_store.get_active_transitions(),
            "recent": memory_store.recent_messages(min_importance=6, limit=10),
            "active_goals": formatted_goals if formatted_goals else (reasoning_context.get("active_goals", []) if reasoning_context else []),
            "causal_links": reasoning_context.get("causal_links", []) if reasoning_context else [],
            "predictions": [],
            "world_model_context": reasoning_context.get("world_model_context", "") if reasoning_context else "",
            "faiss_scores": faiss_scores,
            "runtime_context_block": context_aggregator.build_prompt_block(),
            "timeline_block": timeline_block,
            "intent": intent or "",
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


def _sanitize_chat_history(raw_history: Any) -> list[dict]:
    """Ensures every history item is a dict with role strictly in ('user', 'model')."""
    if not raw_history:
        return []
    clean = []
    for m in raw_history:
        if isinstance(m, dict):
            raw_role = m.get("role")
            parts = m.get("parts", [])
            text = parts[0].get("text", "") if (parts and isinstance(parts[0], dict)) else str(parts)
        else:
            raw_role = getattr(m, "role", None)
            parts = getattr(m, "parts", [])
            text = getattr(parts[0], "text", "") if parts else str(parts)

        role = "model" if raw_role in ("assistant", "model") else "user"
        if raw_role not in ("user", "model", "assistant") and text:
            text = f"[Note]: {text}"
        clean.append({"role": role, "parts": [{"text": text or ""}]})
    return trim_history(clean, LLM_HISTORY_TOKEN_BUDGET)


@observe(name="generate_and_send_response")
async def _generate_and_send_response(message, chat, state, content_payload, query, ctx_data, policy_decision, uid, force_flash: bool = False):
    raw_history = chat.get_history() if hasattr(chat, "get_history") else getattr(chat, "history", getattr(chat, "_curated_history", []))
    history = _sanitize_chat_history(raw_history)
    bundle = None
    ctx_block = None
    if isinstance(content_payload, str) and query:
        rerank_started = time.perf_counter()
        # R3/A1: свежие рабочие слоты диалога + genome survival для Cognitive
        # Gravity. Snapshot уже обновлён в build_context (guarded, не фатален).
        try:
            wm_slots = await asyncio.to_thread(memory_store.working_memory.snapshot, uid)
        except Exception:
            wm_slots = []
        wm_block = _format_working_memory_block(wm_slots)
        wm_ids = {s.get("ref_id") for s in wm_slots if s.get("ref_kind") == "fact" and s.get("ref_id")}
        try:
            fact_ids = [f.id for f in ctx_data["facts"]]
            genome_scores = await asyncio.to_thread(_load_genome_scores, fact_ids)
        except Exception:
            genome_scores = {}
        # H2: retrieval_mgr.select может делать sync LLM-вызовы (HyDE, LLM-judge
        # rerank) — не блокируем event loop.
        bundle = await asyncio.to_thread(
            retrieval_mgr.select,
            query=query, facts=ctx_data["facts"], reflections=ctx_data["reflections"],
            patterns=ctx_data.get("patterns", []),
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
            comm_prefs=ctx_data.get("comm_prefs"),
            human_model=ctx_data.get("human_model"),
            life_transitions=ctx_data.get("life_transitions"),
            store=memory_store,
            timeline_block=ctx_data.get("timeline_block", ""),
            intent=ctx_data.get("intent", ""),
            working_memory_block=wm_block,
            working_memory_ids=wm_ids,
            genome_scores=genome_scores,
        )
        trace = observability.active_trace(uid)
        if trace:
            trace.timings_ms["rerank_and_bundle"] = (time.perf_counter() - rerank_started) * 1000
            await asyncio.to_thread(
                observability.capture_bundle,
                trace,
                bundle,
                ctx_data.get("faiss_scores", {}),
                memory_store,
            )
        logger.info(f"[RAG] Собран бандл памяти. Фактов: {len(bundle.facts) if bundle.facts else 0}, Рефлексий: {len(bundle.reflections) if bundle.reflections else 0}.")
        if bundle.facts:
            logger.info("[RAG DEBUG] Top 5 (Dynamic) Facts:")
            # Filter out pinned facts (score 99.0) to only show dynamically retrieved ones
            dynamic_facts = [f for f in bundle.facts if getattr(f, 'retrieval_score', 0.0) < 99.0]
            for i, f in enumerate(dynamic_facts[:5]):
                score_str = f"score={getattr(f, 'retrieval_score', 0.0):.3f}"
                logger.info(f"  #{i+1}: {f.fact[:80]}... | {score_str}")
        master_summary = memory_store.load_master_summary()
        ctx_block = ""
        if master_summary:
            ctx_block += f"<conversational_memory>\n[Master Summary]\n{master_summary[:2000]}\n</conversational_memory>\n\n"
            
        hidden_emotion = _analyze_hidden_emotion(query)
        if hidden_emotion:
            ctx_block += f"{hidden_emotion}\n\n"
        
        # Emotional callback: "last time you talked about work, you were stressed"
        # This is what makes the bot feel like a friend, not a search engine.
        # Zero LLM cost — pure heuristic analysis.
        try:
            from companion.memory.emotional_context import build_emotional_callback
            emotional_hint = build_emotional_callback(memory_store, query)
            if emotional_hint:
                ctx_block += f"{emotional_hint}\n\n"
        except Exception as exc:
            logger.debug("Emotional callback skipped: %s", exc)
            
        # Закомментировано по просьбе (отключение калибратора/допроса):
        # if getattr(state, "needs_clarification", ""):
        #     ctx_block += f"[СИСТЕМНОЕ СООБЩЕНИЕ: {state.needs_clarification} Вплети этот уточняющий вопрос органично в конец своего ответа, чтобы узнать больше деталей и поддержать диалог.]\n\n"
            
        ctx_block += bundle.to_prompt_block()
        
        user_block = _build_user_prompt_block(content_payload, state.reasoning_context)
        if state.policy_constraints and policy_decision:
            base_payload = policy_layer.format_prompt_with_policy(
                base_prompt=user_block,
                policy=policy_decision
            )
        else:
            base_payload = user_block
            
        content_payload = base_payload

    from companion.config import FINAL_RESPONSE_MODEL, MODEL_NAME
    desired_model = MODEL_NAME if force_flash else FINAL_RESPONSE_MODEL
    current_model = getattr(chat, "model", None)
    
    if current_model != desired_model:
        logger.info(f"[ROUTER] Switching model from {current_model} to {desired_model}")
        
    if not history:
        raw_history = chat.get_history() if hasattr(chat, "get_history") else getattr(chat, "history", getattr(chat, "_curated_history", []))
        history = _sanitize_chat_history(raw_history)

    # Фаза 1: CoT plan — отключена по умолчанию.
    # На 500 RPD каждый вызов = 1 разговор из бюджета.
    # План не добавляет качества: финальная модель и так видит весь контекст.
    # Включается только для важных сообщений (importance >= 8).
    plan = ""
    if state.message_importance >= 8:
        @observe(as_type="generation", name="cot_phase_1")
        async def generate_plan() -> str:
            history_text = ""
            for m in history[-5:]:
                if isinstance(m, dict):
                    role = m.get("role", "unknown")
                    parts = m.get("parts", [])
                    text = parts[0].get("text", "") if (parts and isinstance(parts[0], dict)) else ""
                else:
                    role = getattr(m, "role", "unknown")
                    parts = getattr(m, "parts", [])
                    text = getattr(parts[0], "text", "") if parts else ""
                history_text += f"[{str(role).upper()}]: {text}\n"
                
            plan_prompt = (
                "Перед тем как дать финальный ответ, проанализируй историю диалога, текущий запрос пользователя и контекст памяти. "
                "Учти контекст последних реплик! Напиши краткий внутренний план (Chain of Thought), "
                "как лучше ответить.\n\n"
                f"История диалога (последние 5 сообщений):\n{history_text}\n"
                f"Запрос пользователя:\n{query}\n\nКонтекст памяти:\n{ctx_block}"
            )
            try:
                from companion.llm.client import aio_oneshot
                return await aio_oneshot(plan_prompt, model=MODEL_NAME, temperature=0.5)
            except Exception as e:
                logger.warning(f"Phase 1 plan generation failed: {e}")
                return ""

        plan = await generate_plan()
        if plan:
            logger.info("[ROUTER] Фаза 1 (CoT) выполнена для важного сообщения.")
            content_payload = (
                f"[SYSTEM: Внутренний план ответа]\n"
                f"{plan}\n\n"
                f"[USER]\n{base_payload}"
            )
    # else: plan stays "" — skip CoT entirely for normal messages

    # Создаем базовый чат для ответа только после получения плана,
    # чтобы вшить в него отфильтрованный контекст.
    # Но если план упадет, мы используем полный ctx_block как запасной вариант.
    base_chat_config = {
        "model": desired_model,
        "history": history,
        "temperature": 0.7
    }

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
            # Фаза 2: Контекст в конце промпта и удаление CoT
            from companion.llm.sessions import build_system_instruction
            
            system_instruction = await asyncio.to_thread(
                build_system_instruction,
                memory_store, retrieval_mgr, query, precomputed_context=ctx_block
            )
            reserved_tokens = estimate_tokens(system_instruction) + estimate_tokens(str(content_payload))
            history_budget = min(
                LLM_HISTORY_TOKEN_BUDGET,
                max(0, LLM_INPUT_TOKEN_BUDGET - reserved_tokens),
            )
            bounded_history = trim_history(base_chat_config["history"], history_budget)
            trace = observability.active_trace(uid)
            if trace:
                trace.history_tokens = sum(
                    estimate_tokens(part.get("text", "")) + 4
                    for item in bounded_history
                    for part in item.get("parts", [])
                    if isinstance(part, dict)
                )
                trace.input_tokens = reserved_tokens + trace.history_tokens

            chat = llm.client.chats.create(
                model=base_chat_config["model"],
                history=bounded_history,
                config=llm.make_config(
                    system_instruction=system_instruction,
                    temperature=base_chat_config["temperature"],
                )
            )
            user_chats[uid] = chat
            
            logger.info("[ROUTER] Sending final query with context appended at the end.")
            
            @observe(as_type="generation", name="cot_phase_2")
            async def execute_final_response(c, payload: str) -> Any:
                return await llm.run_llm(c.send_message, payload, timeout=250)
            
            try:
                llm_started = time.perf_counter()
                response = await execute_final_response(chat, content_payload)
                trace = observability.active_trace(uid)
                if trace:
                    trace.timings_ms["gemini"] = (time.perf_counter() - llm_started) * 1000
            except Exception as e:
                # Если мы уже на flash-lite или нас принудительно переключили, не пытаемся фоллбэчиться
                if not force_flash and desired_model != MODEL_NAME:
                    logger.warning(f"[ROUTER] [WARN] Primary model failed, falling back to Flash-lite: {e}")
                    chat = llm.client.chats.create(
                        model=MODEL_NAME,
                        history=bounded_history,
                        config=llm.make_config(
                            system_instruction=system_instruction,
                            temperature=0.7,
                        )
                    )
                    user_chats[uid] = chat
                    response = await execute_final_response(chat, content_payload)
                else:
                    raise e
        finally:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass

        text = _extract_response_text(response)
        if not text and not force_flash:
            # Пустой ответ (сandidates=0 без finish_reason) — известный баг
            # flash-линейки Gemini (issue python-genai #1289, forum #86564):
            # модель изредка возвращает пустой ответ без ошибки, ретраи той же
            # моделью не помогают. Один ретрай тем же запросом, затем fallback
            # на запасную модель (если она отличается от текущей).
            logger.warning("[ROUTER] Empty response from model; retrying once...")
            try:
                response = await execute_final_response(chat, content_payload)
                text = _extract_response_text(response)
            except Exception as retry_exc:
                logger.warning("[ROUTER] Retry after empty response failed: %s", retry_exc)
            if not text:
                from companion.config import FALLBACK_RESPONSE_MODEL
                fallback_model = FALLBACK_RESPONSE_MODEL
                if fallback_model and fallback_model != desired_model:
                    logger.warning("[ROUTER] Empty response persists; switching to fallback model %s", fallback_model)
                    try:
                        chat = llm.client.chats.create(
                            model=fallback_model,
                            history=bounded_history,
                            config=llm.make_config(
                                system_instruction=system_instruction,
                                temperature=0.7,
                            ),
                        )
                        user_chats[uid] = chat
                        response = await execute_final_response(chat, content_payload)
                        text = _extract_response_text(response)
                    except Exception as fb_exc:
                        logger.warning("[ROUTER] Fallback model failed: %s", fb_exc)
        if not text:
            await message.answer("API вернул пустой ответ без текста.")
            state.llm_response = ""
            return

        if state.policy_constraints and policy_decision and text:
            text = policy_layer.enforce_constraints(text, state.policy_constraints)

        critique = run_self_critique(query, text, ctx_data)
        logger.info(f"[CRITIQUE] Оценка: {getattr(critique, 'score', '?')}/10. Замечания: {getattr(critique, 'critique', 'Нет')}")
        state.critique_result = critique
        if text:
            text = apply_critique_to_text(text, critique)
        await send_long_message(message, text)
        msg_rec = None
        if text:
            msg_rec = await asyncio.to_thread(
                memory_store.log_message,
                "assistant", text, 5, "default", [], uid
            )
        state.llm_response = text
        trace = observability.active_trace(uid)
        if trace:
            trace.response_text = text

        # Track emotional state for future callbacks (zero LLM cost)
        try:
            from companion.memory.emotional_context import track_emotional_state
            track_emotional_state(memory_store, query, text)
        except Exception:
            pass

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
            if words:
                matches = sum(1 for w in words if w in resp_lower)
                if matches / len(words) >= 0.5:
                    facts_used += 1
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
    """Извлечь текст из ответа Gemini; при пустом — диагностировать причину.

    response.text == None когда модель вернула только thinking-блоки
    (part.thought=True), function call (AFC) или пустой candidates
    (safety-блок / обрыв генерации). Тогда собираем текст из parts вручную
    и логируем finish_reason + safety-рейтинги, чтобы пустой ответ
    был диагностируемым, а не немой ошибкой.
    """
    if response is None:
        return ""
    try:
        text = getattr(response, "text", None)
    except Exception as e:
        logger.warning("Failed to extract Gemini response text: %s", e)
        text = None
    if isinstance(text, str) and text.strip():
        return text.strip()

    # Диагностика + ручной сбор из candidates[].content.parts
    part_types: list[str] = []
    collected_parts: list[str] = []
    try:
        for candidate in getattr(response, "candidates", None) or []:
            for part in getattr(getattr(candidate, "content", None), "parts", None) or []:
                try:
                    for field_name, field_value in part.model_dump(
                        exclude={"text", "thought", "thought_signature"}
                    ).items():
                        if field_value is not None:
                            part_types.append(field_name)
                except Exception:
                    pass
                part_text = getattr(part, "text", None)
                if isinstance(part_text, str) and not getattr(part, "thought", False):
                    collected_parts.append(part_text)
    except Exception as e:
        logger.warning("Failed to inspect Gemini response parts: %s", e)

    if collected_parts:
        joined = "".join(collected_parts).strip()
        if joined:
            logger.info("Recovered text from response parts: %d chars", len(joined))
            return joined

    logger.warning(
        "Gemini empty response: finish_reason=%s candidates=%d safety=%s part_types=%s",
        getattr(response, "finish_reason", None),
        len(getattr(response, "candidates", None) or []),
        getattr(response, "safety_ratings", None),
        part_types,
    )
    # Полный дамп для диагностики: prompt_feedback (block_reason входа),
    # usage_metadata (дошёл ли запрос до модели), model_version.
    try:
        dump = response.model_dump(
            exclude_none=False,
            exclude={"candidates"},
        )
        logger.warning("Gemini empty response full dump: %s", dump)
    except Exception as dump_exc:
        logger.warning("Failed to dump empty response: %s", dump_exc)
    return ""


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


def _build_user_prompt_block(content_payload: str, reasoning_context: dict[str, Any]) -> str:
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
