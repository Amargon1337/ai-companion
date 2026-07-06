"""Memory and timeline services reused by commands and NL intents."""
from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime

from aiogram import types

from companion import bot_core as core
from companion.config import DIARY_PATH
from companion.models import Fact
from companion.storage.legacy import LegacyStorage


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
        await asyncio.to_thread(LegacyStorage.save_permanent_note, note)
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
    notes = LegacyStorage.load_permanent_notes()
    if notes:
        await core.send_long_message(message, f"📌 Постоянная память:\n\n{notes}")
    else:
        await message.answer("Пусто. Напиши: запомни [текст]")


async def add_diary_entry(message: types.Message, text: str) -> None:
    entry = text.strip()
    if not entry:
        await message.answer("Что записать в дневник?")
        return
    LegacyStorage.save_diary(entry)
    await message.answer("Лог записан.")


async def export_diary(message: types.Message) -> None:
    if os.path.exists(DIARY_PATH):
        await message.answer_document(types.FSInputFile(DIARY_PATH))
    else:
        await message.answer("Дневник пуст.")


async def show_timeline(message: types.Message) -> None:
    events = LegacyStorage.load_events()
    if not events:
        await message.answer("Хронология пуста.")
        return
    lines = [f"{e['date']} [{e['importance']}/10] {e['event']}" for e in events]
    await core.send_long_message(message, "\n".join(lines))


async def show_year(message: types.Message, year: int) -> None:
    events = LegacyStorage.load_events(year)
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

    recent = LegacyStorage.load_events()
    if any(e.get("event", "") == title for e in recent[-10:]):
        return None

    # Note: auto_add_event_from_message is synchronous, so it MUST be called via to_thread.
    # But since it's synchronous, we leave the signature sync, and callers wrap it.
    LegacyStorage.save_event(title, min(10, max(5, importance)), clean[:500])
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
