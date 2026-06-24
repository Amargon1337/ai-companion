"""Telegram handlers package."""
from __future__ import annotations

import logging

from aiogram.types import BotCommand

from companion.handlers import chat, media

logger = logging.getLogger(__name__)


def register_handlers(dp, bot) -> None:
    chat.register(dp, bot)
    media.register(dp, bot)
    logger.info("Registered handlers: chat, media")


async def setup_bot_commands(bot) -> None:
    commands = [
        BotCommand(command="start", description="Запуск companion"),
        BotCommand(command="help", description="Все возможности"),
        BotCommand(command="search", description="Принудительный веб-поиск"),
        BotCommand(command="summary", description="Получить саммери"),
        BotCommand(command="personality", description="Профиль личности"),
        BotCommand(command="remember", description="Запомнить важное"),
    ]
    await bot.set_my_commands(commands)
