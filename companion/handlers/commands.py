"""Command handlers for Amargon's Void."""
from __future__ import annotations

from aiogram import types

from companion import bot_core as core
from companion.llm import client as llm
from companion.llm.prompts import PERSONALITY_REPORT_PROMPT, RETROSPECTIVE_PROMPT
import asyncio
import re
import uuid
from datetime import datetime, timedelta
import json
from companion.models import Fact
from companion import observability
from companion.memory.importance import days_since
from companion.memory.health import collect_garbage, memory_health, memory_index_health
from companion.user_model import user_model


async def show_summary(message: types.Message) -> None:
    uid = message.from_user.id
    if uid in core.user_chats:
        await message.answer("Сжимаю...")
        summary = await core.compress_and_reset(uid)
        if summary:
            await core.send_long_message(message, f"Саммери:\n\n{summary}")
            return

    latest_list = core.memory_store.load_recent_summaries(1)
    latest = latest_list[0] if latest_list else ""
    if latest:
        await core.send_long_message(message, f"Последнее саммери:\n\n{latest}")
    else:
        await message.answer("Саммери ещё нет.")

async def show_personality(message: types.Message) -> None:
    store = core.memory_store
    profile_block = store.build_canonical_profile_text()
    latest_list = store.load_recent_summaries(1)
    summary = latest_list[0] if latest_list else ""
    prompt = PERSONALITY_REPORT_PROMPT.format(
        personality=profile_block,
        summary=summary,
    )
    await core.send_typing(message)
    text = await llm.run_llm(llm.oneshot, prompt)
    await core.send_long_message(message, text)


async def show_evolution(message: types.Message, months: int = 8) -> None:
    """Show durable changes without asking an LLM to invent a retrospective."""
    report = await asyncio.to_thread(user_model.evolution_report, months)
    lines = [
        f"[Evolution за последние {report['months']} мес.]",
        f"Взаимодействия: {report['interactions']}",
        f"Убеждения в модели: {report['new_beliefs']}",
        f"Доминирующее состояние: {report['dominant_state']}",
        f"Текущее состояние: {report['current_state']}",
    ]
    if report["previous_state"] != report["current_state"]:
        lines.append(f"Изменение состояния: {report['previous_state']} -> {report['current_state']}")
    if report["changes"]:
        lines.append("Изменения:")
        lines.extend(f"+ {item}" for item in report["changes"][-10:])
    interests = list(report["interests"].items())[:8]
    if interests:
        lines.append("Ведущие интересы:")
        lines.extend(f"+ {name}: {value}" for name, value in interests)
    if report["state_counts"]:
        lines.append("Эмоциональная динамика: " + ", ".join(
            f"{state}={count}" for state, count in report["state_counts"].items()
        ))
    await core.send_long_message(message, "\n".join(lines))


def parse_year_from_text(text: str) -> int:
    match = re.search(r"\b(19|20)\d{2}\b", text)
    if match:
        return int(match.group(0))
    return datetime.now().year


async def remember_text(message: types.Message, note: str) -> None:
    from companion.security.sanitizer import sanitize_markup
    note = sanitize_markup(note).strip() if note else ""
    if not note:
        await message.answer("Что запомнить? Используй `/remember <текст>` или напиши `запомни ...`.", parse_mode="Markdown")
        return

    async with core.memory_store.lock:
        fact = core.fact_from_permanent_note(note)
        await asyncio.to_thread(core.memory_store.add_fact, fact)
    uid = message.from_user.id
    core.user_chats.pop(uid, None)
    core.user_message_counts.pop(uid, None)
    await message.answer(f"📌 Запомнено:\n\n{note}")

async def show_facts(message: types.Message, query: str = "") -> None:
    store = core.memory_store
    args = query.strip()
    if args:
        results = store.search_facts(args, limit=15)
        hits = [f for f, _ in results]
        if not hits:
            await message.answer(f"Фактов по '{args}' нет.")
            return
        lines = [f"• [{f.memory_kind}|{f.importance}/10|{f.status}] {f.fact}" for f in hits]
        await core.send_long_message(message, f"Факты по '{args}':\n\n" + "\n".join(lines))
        return

    facts = store.recent_facts(20)
    if not facts:
        await message.answer("Fact Store пуст. Поговори — факты появятся при сжатии.")
        return
    lines = [f"• [{f.memory_kind}|{f.importance}/10] {f.fact}" for f in facts]
    await core.send_long_message(message, "Последние факты:\n\n" + "\n".join(lines))


async def show_notes(message: types.Message) -> None:
    notes = "\n".join(await asyncio.to_thread(core.memory_store.db.list_permanent_notes))
    if notes:
        await core.send_long_message(message, f"📌 Постоянная память:\n\n{notes}")
    else:
        await message.answer("Пусто. Напиши: запомни [текст]")


async def add_diary_entry(message: types.Message, text: str) -> None:
    entry = text.strip()
    if not entry:
        await message.answer("Что записать в дневник?")
        return
    fact = Fact(
        fact=entry,
        date=datetime.now().strftime("%Y-%m-%d"),
        importance=6,
        confidence=0.8,
        source="diary_entry",
        source_type="user",
        memory_kind="event",
        tags=["diary"],
    )
    await asyncio.to_thread(core.memory_store.add_fact, fact)
    await message.answer("Лог записан.")


async def export_diary(message: types.Message) -> None:
    diary = [f for f in core.memory_store.list_all_facts() if "diary" in f.tags]
    if not diary:
        await message.answer("Дневник пуст.")
        return
    lines = [f"{f.date}: {f.fact}" for f in sorted(diary, key=lambda f: f.date)]
    await core.send_long_message(message, "\n".join(lines))


async def show_timeline(message: types.Message) -> None:
    events = await asyncio.to_thread(core.memory_store.db.load_events)
    if not events:
        await message.answer("Хронология пуста.")
        return
    lines = [f"{e['date']} [{e['importance']}/10] {e['event']}" for e in events]
    await core.send_long_message(message, "\n".join(lines))


async def show_episodes(message: types.Message, limit: int = 10) -> None:
    eps = await asyncio.to_thread(core.memory_store.db.list_episodes, limit)
    if not eps:
        await message.answer("Эпизоды не найдены.")
        return
    lines = ["[Эпизодическая память]"]
    for e in eps:
        date = e.get("date") or e.get("created_at")[:10]
        emotions = e.get("emotions", {})
        emo_str = ", ".join(f"{k}:{v:.0%}" for k, v in emotions.items() if v > 0.2)
        parts = [f"📅 {date} — {e.get('title','') or e.get('narrative','')[:80]}"]
        if e.get("participants"):
            parts.append(f"  👥 {', '.join(e['participants'])}")
        if emo_str:
            parts.append(f"  💭 {emo_str}")
        if e.get("lesson"):
            parts.append(f"  💡 {e['lesson'][:100]}")
        lines.append("\n".join(parts))
    await core.send_long_message(message, "\n\n".join(lines))


async def show_year(message: types.Message, year: int) -> None:
    events = await asyncio.to_thread(core.memory_store.db.load_events, year)
    if not events:
        await message.answer(f"Нет событий за {year}.")
        return
    lines = [f"📅 {year}"] + [f"  {e['date']} [{e['importance']}/10] {e['event']}" for e in events]
    await core.send_long_message(message, "\n".join(lines))


def auto_add_event_from_message(text: str, importance: int) -> Fact | None:
    from companion.security.sanitizer import sanitize_markup
    clean = sanitize_markup(text).strip() if text else ""
    if importance < 8 or len(clean) < 20:
        return None
    lowered = clean.lower()
    event_markers = [
        "сегодня", "вчера", "сходил", "был", "случилось", "произошло",
        "начал", "закончил", "купил", "расстался", "встретил", "устроился",
    ]
    if not any(marker in lowered for marker in event_markers):
        return None

    title = clean.split(".", 1)[0][:80].strip()
    if not title:
        return None

    fact = Fact(
        fact=clean[:500],
        date=datetime.now().strftime("%Y-%m-%d"),
        importance=min(10, max(5, importance)),
        confidence=0.8,
        source="auto_event",
        source_type="user",
        memory_kind="event",
        tags=["auto_event"],
    )
    core.memory_store.add_fact(fact)
    return fact
async def show_selfie(message: types.Message) -> None:
    store = core.memory_store
    parts = []
    profile = store.build_canonical_profile_text()
    summary = store.load_master_summary()
    if profile:
        parts.append(profile)
    if summary:
        parts.append(summary)
    if not parts:
        await message.answer("Нет данных.")
        return
    await core.process_llm_request(
        message,
        f"Данные:\n\n{chr(10).join(parts)}\n\nПсихопортрет. Холодная аналитика.",
    )


async def show_week_digest(message: types.Message) -> None:
    cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    facts = [f for f in core.memory_store.list_all_facts() if (f.date or "") >= cutoff and ("diary" in f.tags or f.memory_kind == "event")]
    lines = [f"{f.date}: {f.fact}" for f in facts[:50]]
    if not lines:
        await message.answer("Дневник за неделю пуст.")
        return
    await core.process_llm_request(message, f"Записи:\n{chr(10).join(lines)}\n\nДайджест.")


async def show_monthbook(message: types.Message, ym: str | None = None) -> None:
    store = core.memory_store
    ym = ym or datetime.now().strftime("%Y-%m")
    await message.answer(f"Собираю главу за {ym}...")
    content = await store.db.async_load_monthbook(ym)
    if not content:
        content = await _build_monthbook(store, ym)
        if content:
            await store.db.async_save_monthbook(ym, content)
    await core.send_long_message(message, content or "Нет данных.")


async def show_retrospective(message: types.Message, days: int = 30) -> None:
    store = core.memory_store
    await message.answer("Собираю ретроспективу...")
    data = _collect_retrospective(days)
    personality = store.load_personality()
    reflections = store.list_reflections()[:5]
    parts = []
    if reflections:
        parts.append("[Выводы]\n" + "\n".join(r.insight for r in reflections))
    for key, value in data.items():
        parts.append(f"[{key}]\n{str(value)[:2000]}")
    await core.process_llm_request(
        message,
        RETROSPECTIVE_PROMPT.format(
            days=days,
            data="\n\n".join(parts) or "нет",
            changes=json.dumps(personality.get("changes", []), ensure_ascii=False),
        ),
    )


async def show_context(message: types.Message) -> None:
    latest_list = core.memory_store.load_recent_summaries(1)
    summary = latest_list[0] if latest_list else ""
    if summary:
        await core.send_long_message(message, f"Последнее саммери:\n\n{summary}")
    else:
        await message.answer("Саммери ещё нет.")


async def show_debug_retrieval(message: types.Message) -> None:
    trace = observability.latest_trace(message.from_user.id)
    if trace is None:
        await message.answer("Диагностических данных ещё нет. Сначала отправь обычное сообщение.")
        return
    lines = [
        "[Последний retrieval trace]",
        f"Запрос: {trace.query[:300]}",
        f"Replay ID: {trace.replay_id}",
        f"History: {trace.history_tokens} токенов",
        f"RAG context: {trace.context_tokens} токенов",
        f"Input estimate: {trace.input_tokens} токенов",
        f"Факты: {trace.counts.get('facts', 0)}",
        f"Beliefs: {trace.counts.get('beliefs', 0)}",
        f"Patterns: {trace.counts.get('patterns', 0)}",
        f"Predictions: {trace.counts.get('predictions', 0)}",
        f"Graph edges: {trace.counts.get('graph_edges', 0)}",
        f"Summaries: {trace.counts.get('summaries', 0)}",
        "Время: " + ", ".join(f"{key}={value:.0f} ms" for key, value in trace.timings_ms.items()),
    ]
    await core.send_long_message(message, "\n".join(lines))


async def show_why(message: types.Message, fact_id: str = "") -> None:
    trace = observability.latest_trace(message.from_user.id)
    if trace is None or not trace.facts:
        await message.answer("Нет последнего retrieval trace c использованными фактами.")
        return
    facts = trace.facts
    if fact_id:
        facts = [fact for fact in facts if fact["id"] == fact_id]
    if not facts:
        await message.answer(f"Факт `{fact_id}` не найден в последнем retrieval.")
        return
    lines = ["[Почему эти факты попали в контекст]"]
    for fact in facts[:10]:
        similarity = "n/a" if fact["similarity"] is None else f"{fact['similarity']:.3f}"
        lines.extend([
            f"\n{fact['id']}",
            fact["text"][:500],
            f"Similarity: {similarity}",
            f"Importance: {fact['importance']:.2f}",
            f"Recency: {fact['recency']:.2f}",
            f"Retrieval score: {fact['retrieval_score']:.3f}",
            f"Graph relations: {fact['relations']}",
        ])
    await core.send_long_message(message, "\n".join(lines))


async def show_memory_stats(message: types.Message) -> None:
    stats = await asyncio.to_thread(observability.memory_stats, core.memory_store)
    averages = observability.average_timings()
    lines = [
        "[Memory stats]",
        f"Facts: {stats['facts']} (active: {stats['active_facts']})",
        f"Beliefs: {stats['beliefs']}",
        f"Patterns: {stats['patterns']}",
        f"Predictions: {stats['predictions']}",
        f"Messages: {stats['messages']}",
        f"Graph edges: {stats['graph_edges']}",
        f"FAISS vectors: {stats['faiss_vectors']}",
        f"FAISS dirty: {str(stats['faiss_dirty']).lower()}",
        f"Facts sent/used: {stats['fact_sent']}/{stats['fact_used']}",
    ]
    if averages:
        lines.append("Average timings: " + ", ".join(f"{key}={value:.0f} ms" for key, value in averages.items()))
    await core.send_long_message(message, "\n".join(lines))


async def inspect_fact(message: types.Message, fact_id: str) -> None:
    fact = await asyncio.to_thread(core.memory_store.get_fact, fact_id.strip())
    if fact is None:
        await message.answer(f"Факт `{fact_id}` не найден.")
        return
    relations = await asyncio.to_thread(core.memory_store.get_fact_relations, fact.id)
    raw_fact = await asyncio.to_thread(core.memory_store.db.get_fact, fact.id)
    age = days_since(fact.date or fact.created_at)
    from companion.memory.importance import decay_factor
    lines = [
        f"[Fact inspector: {fact.id}]",
        fact.fact,
        f"Статус: {fact.status}",
        f"Создан: {fact.created_at}",
        f"Дата: {fact.date}",
        f"Последнее использование: {(raw_fact or {}).get('last_accessed') or 'n/a'}",
        f"Importance: {fact.importance}/10",
        f"Confidence: {fact.confidence:.2f}",
        f"Recency: {decay_factor(age, fact.memory_kind):.2f}",
        f"Sent/used: {fact.facts_sent_count}/{fact.facts_used_count}",
        f"Tags: {', '.join(fact.tags) or 'нет'}",
        f"Relations: {len(relations)}",
    ]
    for relation in relations[:20]:
        lines.append(f"- {relation.get('relation')}: {relation.get('from_id')} -> {relation.get('to_id')}")
    await core.send_long_message(message, "\n".join(lines))


async def show_memory_health(message: types.Message) -> None:
    health = await asyncio.to_thread(memory_health, core.memory_store)
    lines = [
        "[Memory health]",
        f"Facts: {health['facts']} (active: {health['active_facts']})",
        f"Duplicate candidates: {health['duplicate_candidates']} in {health['duplicate_groups']} groups",
        f"Contradictions: {health['contradictions']}",
        f"Orphan active facts: {health['orphan_active_facts']}",
        f"Unused embeddings: {health['unused_embeddings']}",
        f"Stale predictions: {health['stale_predictions']}",
        f"GC candidates: {health['gc_candidates']}",
        f"Dormant/superseded/archived: {health['dormant_facts']}/{health['superseded_facts']}/{health['archived_facts']}",
        f"Memory quality score: {health['quality_score']}/100",
    ]
    await core.send_long_message(message, "\n".join(lines))


async def show_memory_index_health(message: types.Message) -> None:
    stats = await asyncio.to_thread(memory_index_health, core.memory_store)
    lines = [
        "[Memory index health]",
        f"active facts: {stats['active_facts']}",
        f"indexed vectors: {stats['indexed_vectors']}",
        f"orphan vectors: {stats['orphan_vectors']}",
        f"missing vectors: {stats['missing_vectors']}",
    ]
    await core.send_long_message(message, "\n".join(lines))


async def show_replay(message: types.Message, replay_id: str) -> None:
    replay = await asyncio.to_thread(
        observability.load_replay,
        core.memory_store,
        replay_id.strip(),
    )
    if replay is None:
        await message.answer(f"Replay `{replay_id}` не найден.")
        return
    lines = [
        f"[Retrieval replay {replay['replay_id']}]",
        f"Запрос: {replay.get('query', '')[:500]}",
        f"History: {replay.get('history_tokens', 0)} tokens",
        f"Context: {replay.get('context_tokens', 0)} tokens",
        f"Input: {replay.get('input_tokens', 0)} tokens",
        "Timings: " + ", ".join(
            f"{key}={value:.0f} ms"
            for key, value in replay.get("timings_ms", {}).items()
        ),
        f"Response: {replay.get('response_text', '')[:1500]}",
        "Facts:",
    ]
    for fact in replay.get("facts", [])[:20]:
        similarity = fact.get("similarity")
        similarity_text = "n/a" if similarity is None else f"{similarity:.3f}"
        lines.append(
            f"- {fact.get('id')}: similarity={similarity_text}, "
            f"score={fact.get('retrieval_score', 0):.3f}: {fact.get('text', '')[:180]}"
        )
    await core.send_long_message(message, "\n".join(lines))


async def run_memory_gc(message: types.Message, apply: bool = False) -> None:
    candidates = await asyncio.to_thread(collect_garbage, core.memory_store, apply=apply)
    if not candidates:
        await message.answer("Memory GC: кандидатов нет.")
        return
    mode = "архивировано" if apply else "кандидатов (dry-run)"
    lines = [f"Memory GC: {len(candidates)} {mode}."]
    for item in candidates[:20]:
        lines.append(f"- {item.fact_id}: {item.age_days} дней, importance {item.importance}: {item.text[:120]}")
    if not apply:
        lines.append("Для применения: /memory_gc apply")
    await core.send_long_message(message, "\n".join(lines))


def _collect_retrospective(days: int) -> dict:
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    result = {}
    facts = [f for f in core.memory_store.list_all_facts() if (f.date or "") >= cutoff and ("diary" in f.tags or f.memory_kind == "event")]
    week = [f"{f.date}: {f.fact}" for f in facts[:50]]
    if week:
        result["diary_week"] = week
    events = [e for e in core.memory_store.db.load_events() if e["date"] >= cutoff]
    if events:
        result["events"] = events
    return result


async def _build_monthbook(store, ym: str) -> str:
    parts = []
    facts = store.facts_for_period(ym, min_importance=5)
    if facts:
        parts.append("[Факты месяца]\n" + "\n".join(f"- [{f.importance}/10] {f.fact}" for f in facts))
    reflections = [r for r in store.list_reflections() if r.period == ym]
    if reflections:
        parts.append("[Выводы]\n" + "\n".join(f"- {r.insight}" for r in reflections))
    hi_msgs = store.high_importance_messages_for_period(ym, 7)
    if hi_msgs:
        parts.append("[Ключевые реплики]\n" + "\n".join(f"- {m.text[:200]}" for m in hi_msgs[:15]))
    events = [e for e in store.db.load_events() if e["date"].startswith(ym)]
    if events:
        parts.append("[События]\n" + "\n".join(f"{e['date']}: {e['event']}" for e in events))
    if not parts:
        return ""
    personality = store.build_canonical_profile_text()
    prompt = f"Данные за {ym}:\n\n{chr(10).join(parts)}\n\n{personality}\n\nГлава автобиографии. Третье лицо. Грязный реализм."
    return await llm.run_llm(llm.oneshot, prompt)
