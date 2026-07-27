"""Chat handlers — public companion command surface and text ingress."""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile

from aiogram import F, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import ReplyKeyboardRemove

from companion import bot_core as core
from companion.config import MAX_VIDEO_DOWNLOAD_BYTES
from companion.llm import client as llm
from companion.handlers import commands
import yt_dlp

logger = logging.getLogger(__name__)


def _make_temp_dir() -> str:
    return tempfile.mkdtemp(prefix="companion-tiktok-")

HELP_TEXT = (
    "<b>Команды:</b>\n"
    "/start — перезапустить companion UI\n"
    "/help — показать справку\n"
    "/summary — вручную получить саммери\n"
    "/personality — профиль личности\n"
    "/continuity — синтез траектории личности\n"
    "/timeline — подробная хронология трансформаций\n"
    "/metrics — когнитивная статистика бота\n"
    "/remember &lt;текст&gt; — сохранить важное навсегда\n\n"
    "<b>Обычным текстом тоже можно:</b>\n"
    "• что ты обо мне помнишь\n"
    "• какие у меня цели\n"
    "• покажи хронологию\n"
    "• сделай сводку недели\n"
    "• экспортируй дневник\n"
    "• добавь задачу купить лекарства\n"
    "• моя цель — найти новую работу\n\n"
    "Память, причинно-следственный анализ, LCE и оценка уверенности работают автоматически."
)


def get_main_keyboard():
    builder = InlineKeyboardBuilder()
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
            "Быстрые команды: /summary, /personality, /remember.",
            reply_markup=get_main_keyboard(),
        )

    @dp.message(Command("help"))
    async def cmd_help(message: types.Message):
        await message.answer(HELP_TEXT, parse_mode="HTML", reply_markup=get_main_keyboard())

    @dp.message(Command("summary", "summarize"))
    async def cmd_summary(message: types.Message):
        await commands.show_summary(message)

    @dp.message(Command("personality"))
    async def cmd_personality(message: types.Message):
        await commands.show_personality(message)

    @dp.message(Command("remember"))
    async def cmd_remember(message: types.Message):
        args = (message.text or "").split(maxsplit=1)
        note = args[1] if len(args) > 1 else ""
        await commands.remember_text(message, note)

    @dp.message(Command("continuity", "lce"))
    async def cmd_continuity(message: types.Message):
        await core.show_life_continuity(message)

    @dp.message(Command("timeline"))
    async def cmd_timeline(message: types.Message):
        await core.show_timeline(message)

    @dp.message(Command("metrics", "stats"))
    async def cmd_metrics(message: types.Message):
        await commands.show_metrics(message)

    @dp.message(Command("debug"))
    async def cmd_debug(message: types.Message):
        await commands.show_debug_retrieval(message)

    @dp.message(Command("why"))
    async def cmd_why(message: types.Message):
        await commands.show_why_retrieval(message)
        await message.answer("🧠 <b>Метрики:</b>\nПамять активна и консолидируется в фоне.", parse_mode="HTML")

    @dp.message(Command("search"))
    async def cmd_search(message: types.Message):
        await message.answer("Поиск в интернете отключён.")

    @dp.callback_query(F.data.startswith("action:"))
    async def inline_actions(callback: types.CallbackQuery):
        action = callback.data.split(":", 1)[1]
        await callback.answer()
        if action == "search":
            await callback.message.answer("Поиск в интернете отключён.")
        elif action == "summary":
            await commands.show_summary(callback.message)
        elif action == "personality":
            await commands.show_personality(callback.message)
        elif action == "help":
            msg = await callback.message.answer(HELP_TEXT, parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
            await msg.edit_reply_markup(reply_markup=get_main_keyboard())

    @dp.callback_query(F.data.startswith("cmd_ok:") | F.data.startswith("cmd_no:"))
    async def command_confirm(callback: types.CallbackQuery):
        core.cleanup_pending_commands()
        data = callback.data
        action, cmd_id = data.split(":", 1)
        pending = core.PENDING_COMMANDS.get(cmd_id)
        
        if not pending:
            await callback.answer("Команда устарела или не найдена.", show_alert=True)
            await callback.message.edit_reply_markup(reply_markup=None)
            return
            
        if pending["uid"] != callback.from_user.id:
            await callback.answer("Это не ваша команда.", show_alert=True)
            return

        if action == "cmd_ok":
            await callback.answer("Выполняю...")
            await callback.message.edit_text(
                callback.message.text + "\n\n✅ Подтверждено пользователем.",
                reply_markup=None
            )
            mock_msg = callback.message
            mock_msg.from_user = types.User(id=pending["uid"], is_bot=False, first_name="User")
            success = await core._route_command(mock_msg, pending["command"], pending["payload"])
            if success:
                await asyncio.to_thread(
                    core.memory_store.log_message,
                    "assistant",
                    f"[Выполнена команда: {pending['command']}]",
                    4, "command", [], pending["uid"]
                )
        else:
            await callback.answer("Отменено.")
            await callback.message.edit_text(
                callback.message.text + "\n\n❌ Отменено.",
                reply_markup=None
            )
            
        del core.PENDING_COMMANDS[cmd_id]

    @dp.message(F.text & ~F.text.startswith("/") & ~F.text.contains("tiktok.com"))
    async def text_handler(message: types.Message):
        text = message.text or ""
        note = _parse_remember_command(text)
        if note is not None:
            await commands.remember_text(message, note)
            return
        await core.process_llm_request(message, text)

    @dp.message(F.text & F.text.contains("tiktok.com"))
    async def tiktok_handler(message: types.Message):
        if not core.check_rate_limit(message.from_user.id, message):
            return
        await message.answer("Качаю TikTok...")
        await core.send_typing(message)
        temp_dir = _make_temp_dir()
        file_path = os.path.join(temp_dir, "downloaded.%(ext)s")
        vf = None
        try:
            with yt_dlp.YoutubeDL({
                "format": "best",
                "outtmpl": file_path,
                "quiet": True,
                "max_filesize": MAX_VIDEO_DOWNLOAD_BYTES,
            }) as ydl:
                await asyncio.to_thread(ydl.download, [message.text])

            candidates = [
                os.path.join(temp_dir, name)
                for name in os.listdir(temp_dir)
                if os.path.isfile(os.path.join(temp_dir, name))
            ]
            if not candidates:
                raise FileNotFoundError("TikTok download produced no files")

            actual_file = max(candidates, key=os.path.getsize)
            if os.path.getsize(actual_file) > MAX_VIDEO_DOWNLOAD_BYTES:
                limit_mb = MAX_VIDEO_DOWNLOAD_BYTES // (1024 * 1024)
                await message.answer(f"Видео слишком большое. Лимит: {limit_mb} MB.")
                return

            vf = await llm.run_llm(llm.upload_file, actual_file)
            vf = await core.wait_gemini_file_ready(vf)
            await core.process_llm_request(message, ["Опиши видео:", vf])
        except Exception as e:
            logger.error("TikTok download error: %s", e, exc_info=True)
            await message.answer("Произошла ошибка при загрузке TikTok. Подробности в логах.")
        finally:
            if vf:
                try:
                    await llm.run_llm(llm.delete_file, vf.name)
                except Exception:
                    pass
            shutil.rmtree(temp_dir, ignore_errors=True)


def _parse_remember_command(text: str) -> str | None:
    lowered = text.strip().lower()
    for prefix in ("запомни", "remember"):
        if lowered.startswith(prefix):
            return text.strip()[len(prefix):].strip(" :-—")
    return None
