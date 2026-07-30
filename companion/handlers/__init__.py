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
        BotCommand(command="summary", description="Получить саммери"),
        BotCommand(command="personality", description="Профиль личности"),
        BotCommand(command="continuity", description="Синтез траектории личности"),
        BotCommand(command="timeline", description="Хронология изменений"),
        BotCommand(command="episodes", description="Эпизодическая память"),
        BotCommand(command="metrics", description="Когнитивная статистика"),
        BotCommand(command="evolution", description="Эволюция личности"),
        BotCommand(command="remember", description="Запомнить важное"),
        BotCommand(command="debug", description="Диагностика retrieval"),
        BotCommand(command="why", description="Почему выбран контекст"),
        BotCommand(command="memory_stats", description="Статистика памяти"),
        BotCommand(command="inspect", description="Инспектор факта"),
        BotCommand(command="memory_health", description="Здоровье памяти"),
        BotCommand(command="memory_gc", description="Очистка памяти dry-run"),
        BotCommand(command="replay", description="Воспроизвести retrieval"),
    ]
    await bot.set_my_commands(commands)
