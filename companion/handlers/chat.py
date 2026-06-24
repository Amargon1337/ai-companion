"""Chat handlers — public companion command surface and text ingress."""
from __future__ import annotations

import asyncio
import logging
import os
import uuid

from aiogram import F, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from companion import bot_core as core
from companion.config import BASE_DIR
from companion.llm import client as llm
from companion.services import memory_service, report_service
from companion.storage.legacy import LegacyStorage
import yt_dlp

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "<b>Команды:</b>\n"
    "/start — перезапустить companion UI\n"
    "/help — показать справку\n"
    "/search &lt;запрос&gt; — принудительный web search\n"
    "/summary — вручную получить саммери\n"
    "/personality — профиль личности\n"
    "/remember &lt;текст&gt; — сохранить важное навсегда\n\n"
    "<b>Обычным текстом тоже можно:</b>\n"
    "• что ты обо мне помнишь\n"
    "• какие у меня цели\n"
    "• покажи хронологию\n"
    "• сделай сводку недели\n"
    "• экспортируй дневник\n"
    "• добавь задачу купить лекарства\n"
    "• моя цель — найти новую работу\n\n"
    "Поиск, настроение, причинно-следственный анализ, прогнозирование и оценка уверенности работают автоматически."
)


def get_main_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🔍 Поиск в Web", callback_data="action:search")
    builder.button(text="📊 Получить сводку", callback_data="action:summary")
    builder.button(text="👤 Профиль личности", callback_data="action:personality")
    builder.button(text="💡 Инфо / Помощь", callback_data="action:help")
    builder.adjust(2, 2)
    return builder.as_markup()


def register(dp, bot) -> None:
    store = core.memory_store

    @dp.message(Command("start"))
    async def cmd_start(message: types.Message):
        await message.answer(
            "Companion online. Пиши обычным текстом — память, reasoning и поиск работают в фоне.\n\n"
            "Быстрые команды: /search, /summary, /personality, /remember.",
            reply_markup=get_main_keyboard(),
        )

    @dp.message(Command("help"))
    async def cmd_help(message: types.Message):
        await message.answer(HELP_TEXT, parse_mode="HTML", reply_markup=get_main_keyboard())

    @dp.message(Command("summary", "summarize"))
    async def cmd_summary(message: types.Message):
        await report_service.show_summary(message)

    @dp.message(Command("personality"))
    async def cmd_personality(message: types.Message):
        await report_service.show_personality(message)

    @dp.message(Command("remember"))
    async def cmd_remember(message: types.Message):
        args = (message.text or "").split(maxsplit=1)
        note = args[1] if len(args) > 1 else ""
        await memory_service.remember_text(message, note)

    @dp.message(Command("search"))
    async def cmd_search(message: types.Message):
        query = (message.text or "").replace("/search", "", 1).strip()
        if not query:
            await message.answer("Формат: /search <запрос>")
            return
        if not core.check_rate_limit(message.from_user.id, message):
            return
        await message.answer("Ищу в Google...")
        try:
            rc = await asyncio.to_thread(core.reasoning_engine.auto_reasoning_context, query)
            ctx_data = core._load_retrieval_context(query, rc)
            ctx = core.retrieval_mgr.select(
                query=query,
                facts=ctx_data["facts"],
                reflections=ctx_data["reflections"],
                summaries=ctx_data["summaries"],
                permanent_notes=ctx_data["permanent_notes"],
                personality_snapshot=ctx_data["personality"],
                recent_messages=ctx_data["recent"],
                active_goals=ctx_data.get("active_goals", []),
                causal_links=ctx_data.get("causal_links", []),
                predictions=ctx_data.get("predictions", []),
                world_model_context=ctx_data.get("world_model_context", ""),
            ).to_prompt_block()
            await core.send_typing(message)
            text, sources = await llm.run_llm(llm.search_with_grounding, query, ctx)
            reply = f"🔍 {text}"
            if sources:
                reply += f"\n\n📎 Источники:\n{sources}"
            await core.send_long_message(message, reply)
            store.log_message(role="assistant", text=text[:500], importance=5, mode="search", user_id=message.from_user.id)
        except Exception as e:
            await message.answer(f"Ошибка поиска: {e}")

    @dp.callback_query(F.data.startswith("action:"))
    async def inline_actions(callback: types.CallbackQuery):
        action = callback.data.split(":", 1)[1]
        await callback.answer()
        if action == "search":
            await callback.message.answer("Формат: /search <запрос>")
        elif action == "summary":
            await report_service.show_summary(callback.message)
        elif action == "personality":
            await report_service.show_personality(callback.message)
        elif action == "help":
            await callback.message.answer(HELP_TEXT, parse_mode="HTML", reply_markup=get_main_keyboard())

    @dp.message(F.text & ~F.text.startswith("/") & ~F.text.contains("tiktok.com"))
    async def text_handler(message: types.Message):
        text = message.text or ""
        note = LegacyStorage.parse_remember_command(text)
        if note is not None:
            await memory_service.remember_text(message, note)
            return
        await core.process_llm_request(message, text)

    @dp.message(F.text & F.text.contains("tiktok.com"))
    async def tiktok_handler(message: types.Message):
        await message.answer("Качаю TikTok...")
        await core.send_typing(message)
        file_path = os.path.join(BASE_DIR, f"{uuid.uuid4().hex}.mp4")
        try:
            with yt_dlp.YoutubeDL({"format": "best", "outtmpl": file_path, "quiet": True}) as ydl:
                await asyncio.to_thread(ydl.download, [message.text])
            vf = await llm.run_llm(llm.upload_file, file_path)
            vf = await core.wait_gemini_file_ready(vf)
            await core.process_llm_request(message, ["Опиши видео:", vf])
        except Exception as e:
            await message.answer(f"Ошибка TikTok: {e}")
        finally:
            if os.path.exists(file_path):
                os.remove(file_path)
