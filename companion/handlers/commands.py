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
        await core.send_long_message(message, f"Поcледнее cаммери:\n\n{latest}")
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


async def show_metrics(message: types.Message) -> None:
    stats = await asyncio.to_thread(core.memory_store.observability_stats)
    counts = stats["counts"]
    metric = stats.get("last_retrieval_metric") or {}
    metric_line = "нет данных"
    if metric:
        metric_line = (
            f"facts {metric.get('facts_sent', 0)}/{metric.get('facts_used', 0)}, "
            f"goals {metric.get('goals_sent', 0)}/{metric.get('goals_used', 0)}, "
            f"reflections {metric.get('reflections_sent', 0)}/{metric.get('reflections_used', 0)}"
        )
    text = (
        "Memory stats\n\n"
        f"Facts: {counts['facts']} (active: {stats['active_facts']})\n"
        f"Beliefs: {counts['beliefs']}\n"
        f"Patterns: {counts['patterns']}\n"
        f"Reflections: {counts['reflections']}\n"
        f"Predictions: {counts['predictions']}\n"
        f"Messages: {counts['messages']}\n"
        f"Graph edges: {counts['fact_relations']}\n"
        f"Embedding cache: {stats['embedding_cache']}\n"
        f"FAISS vectors: {stats['faiss_total']}\n"
        f"FAISS dirty: {stats['faiss_dirty']}\n"
        f"Last retrieval usage: {metric_line}"
    )
    await core.send_long_message(message, text)


async def show_debug_retrieval(message: types.Message) -> None:
    debug = core.retrieval_mgr.last_debug
    if not debug:
        await message.answer("Retrieval ещё не выполнялcя в этом процеccе.")
        return
    text = (
        "Last retrieval\n\n"
        f"Query: {debug.get('query') or '(empty)'}\n"
        f"Candidates: {debug.get('candidate_facts', 0)}\n"
        f"Selected facts: {debug.get('selected_facts', 0)}\n"
        f"Reflections: {debug.get('selected_reflections', 0)}\n"
        f"Patterns: {debug.get('selected_patterns', 0)}\n"
        f"Summaries: {debug.get('summaries', 0)}\n"
    )
    for index, fact in enumerate(debug.get("facts", [])[:10], start=1):
        text += f"\n{index}. [{fact['score']}] {fact['text'][:180]}"
    await core.send_long_message(message, text)


async def show_why_retrieval(message: types.Message) -> None:
    debug = core.retrieval_mgr.last_debug
    if not debug or not debug.get("facts"):
        await message.answer("Нет поcледнего retrieval c выбранными фактами.")
        return
    parts = [f"Почему эти факты попали в поcледний context?\nЗапроc: {debug.get('query') or '(empty)'}"]
    for fact in debug["facts"][:10]:
        details = fact.get("details") or {}
        parts.append(
            f"\n• {fact['text'][:180]}\n"
            f"  score={fact['score']}; similarity={details.get('similarity', 0)}; "
            f"importance={details.get('importance', 0)}; recency={details.get('recency', 0)}; "
            f"mood={details.get('mood_boost', 0)}; kind={details.get('kind_boost', 0)}"
        )
    await core.send_long_message(message, "\n".join(parts))


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
