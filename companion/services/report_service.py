"""Report and profile services reused by commands and NL intents."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from aiogram import types

from companion import bot_core as core
from companion.llm import client as llm
from companion.llm.prompts import PERSONALITY_REPORT_PROMPT, RETROSPECTIVE_PROMPT
from companion.storage.legacy import LegacyStorage


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


async def show_selfie(message: types.Message) -> None:
    parts = LegacyStorage.get_selfie_data()
    if not parts:
        await message.answer("Нет данных.")
        return
    await core.process_llm_request(
        message,
        f"Данные:\n\n{chr(10).join(parts)}\n\nПсихопортрет. Холодная аналитика.",
    )


async def show_week_digest(message: types.Message) -> None:
    lines = LegacyStorage.get_week_diary()
    if not lines:
        await message.answer("Дневник за неделю пуст.")
        return
    await core.process_llm_request(message, f"Записи:\n{chr(10).join(lines)}\n\nДайджест.")


async def show_monthbook(message: types.Message, ym: str | None = None) -> None:
    store = core.memory_store
    ym = ym or datetime.now().strftime("%Y-%m")
    await message.answer(f"Собираю главу за {ym}...")
    content = LegacyStorage.load_monthbook(ym)
    if not content:
        content = await _build_monthbook(store, ym)
        if content:
            LegacyStorage.save_monthbook(ym, content)
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


def _collect_retrospective(days: int) -> dict:
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    result = {}
    week = LegacyStorage.get_week_diary()
    if week:
        result["diary_week"] = week
    events = [e for e in LegacyStorage.load_events() if e["date"] >= cutoff]
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
    events = [e for e in LegacyStorage.load_events() if e["date"].startswith(ym)]
    if events:
        parts.append("[События]\n" + "\n".join(f"{e['date']}: {e['event']}" for e in events))
    if not parts:
        return ""
    personality = store.build_canonical_profile_text()
    prompt = f"Данные за {ym}:\n\n{chr(10).join(parts)}\n\n{personality}\n\nГлава автобиографии. Третье лицо. Грязный реализм."
    return await llm.run_llm(llm.oneshot, prompt)
