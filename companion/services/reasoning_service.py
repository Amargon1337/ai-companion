"""Reasoning and task services for NL intents and runtime integration."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime

from aiogram import types

from companion import bot_core as core
from companion.reasoning import Goal, reasoning_engine
from companion.storage.legacy import LegacyStorage


async def reset_context(message: types.Message) -> None:
    uid = message.from_user.id
    if uid in core.user_chats:
        del core.user_chats[uid]
        core.user_message_counts.pop(uid, None)
        await message.answer("Чат сброшен. Память на диске сохранена.")
    else:
        await message.answer("Активного чата нет.")


async def show_goals(message: types.Message) -> None:
    goals = reasoning_engine.list_goals()
    if not goals:
        await message.answer("Нет целей. Сформулируй цель обычным текстом, например: 'моя цель — ...'")
        return
    lines = ["🎯 Твои цели:\n"]
    for goal in goals:
        status_emoji = {"active": "🟢", "paused": "⏸️", "completed": "✅", "abandoned": "❌"}.get(goal.status, "⚪")
        priority_bar = "█" * goal.priority + "░" * (10 - goal.priority)
        lines.append(f"{status_emoji} [{priority_bar}] {goal.title}")
        if goal.description:
            lines.append(f"   {goal.description[:60]}")
        lines.append(f"   ID: {goal.goal_id}\n")
    await core.send_long_message(message, "\n".join(lines))


async def add_goal_from_text(message: types.Message, text: str) -> None:
    title = _extract_goal_title(text)
    if not title:
        await message.answer("Не понял цель. Пример: 'моя цель — пройти собеседование'.")
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
            lines.append(f"  {link.cause} → {link.effect} ({link.confidence:.0%})")
        lines.append("")
    world_model = reasoning_engine.world_model
    if world_model.get("active_contexts"):
        lines.append("🌍 Активные контексты:")
        for ctx in world_model["active_contexts"][:3]:
            lines.append(f"  • {ctx}")
        lines.append("")
    lines.append(f"Последнее обновление: {world_model.get('last_updated', '?')[:16]}")
    await core.send_long_message(message, "\n".join(lines))


async def show_todos(message: types.Message) -> None:
    todos = await asyncio.to_thread(LegacyStorage.load_todos)
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
    todos = await asyncio.to_thread(LegacyStorage.load_todos)
    todos.append({"text": task_text, "done": False, "created": datetime.now().isoformat()})
    await asyncio.to_thread(LegacyStorage.save_todos, todos)
    await message.answer("Задача добавлена.")


async def complete_todo(message: types.Message, text: str) -> None:
    todos = await asyncio.to_thread(LegacyStorage.load_todos)
    idx = _extract_index(text) - 1
    if idx < 0 or idx >= len(todos):
        await message.answer("Нет такого номера.")
        return
    todos[idx]["done"] = True
    await asyncio.to_thread(LegacyStorage.save_todos, todos)
    await message.answer("Готово.")


async def delete_todo(message: types.Message, text: str) -> None:
    todos = await asyncio.to_thread(LegacyStorage.load_todos)
    idx = _extract_index(text) - 1
    if idx < 0 or idx >= len(todos):
        await message.answer("Нет такого номера.")
        return
    todos.pop(idx)
    await asyncio.to_thread(LegacyStorage.save_todos, todos)
    await message.answer("Готово.")


async def clear_done_todos(message: types.Message) -> None:
    all_todos = await asyncio.to_thread(LegacyStorage.load_todos)
    todos = [t for t in all_todos if not t.get("done")]
    await asyncio.to_thread(LegacyStorage.save_todos, todos)
    await message.answer("Выполненные очищены.")


async def show_self_description(message: types.Message) -> None:
    from companion.self_model import self_model

    await core.send_long_message(message, self_model.get_self_description())


async def show_selfmap(message: types.Message) -> None:
    from companion.self_model import self_model

    km = self_model.data.get("knowledge_domains", {})
    lines = ["🗺️ Карта моих знаний о тебе:\n", "✅ Глубокое понимание:"]
    for topic in km.get("deep_knowledge", []):
        lines.append(f"  • {topic}")
    lines.append("\n📖 Поверхностное понимание:")
    for topic in km.get("surface_knowledge", []):
        lines.append(f"  • {topic}")
    lines.append("\n❓ Пробелы в знаниях:")
    for missing in km.get("missing_data", []):
        lines.append(f"  • {missing}")
    await core.send_long_message(message, "\n".join(lines))


def _extract_index(text: str) -> int:
    match = re.search(r"\b(\d+)\b", text)
    if not match:
        return 0
    return int(match.group(1))


def _extract_goal_title(text: str) -> str:
    patterns = [
        r"моя цель\s*[-—:]?\s*(.+)",
        r"цель\s*[-—:]?\s*(.+)",
        r"хочу\s+(.+)",
    ]
    lowered = text.strip()
    for pattern in patterns:
        match = re.search(pattern, lowered, re.IGNORECASE)
        if match:
            return match.group(1).strip(" .")
    return ""
