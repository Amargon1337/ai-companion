"""Application entry — bot startup."""
from __future__ import annotations

import asyncio
import logging
import os
import signal
from logging.handlers import RotatingFileHandler

from aiogram import BaseMiddleware, Bot, Dispatcher, types

from companion.config import ADMIN_IDS, API_TOKEN, DATA_DIR, LOG_LEVEL, LOG_PATH
from companion.handlers import register_handlers, setup_bot_commands
from companion.storage.jsonl import rotate_jsonl

# Logging: stderr + rotating file
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_handler = RotatingFileHandler(LOG_PATH, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8")
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logging.getLogger().addHandler(_handler)
logger = logging.getLogger(__name__)


class AuthMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, types.Message):
            user = event.from_user
            if not user or user.id not in ADMIN_IDS:
                await event.answer("Пошёл нахуй, ты не Иван. Доступ закрыт.")
                return
        elif isinstance(event, types.CallbackQuery):
            user = event.from_user
            if not user or user.id not in ADMIN_IDS:
                await event.answer("Доступ закрыт.", show_alert=True)
                return
        return await handler(event, data)


async def run() -> None:
    # Rotate growing JSONL files on startup
    for fname in ("messages.jsonl", "policy_decisions.jsonl", "user_model_updates.jsonl"):
        rotate_jsonl(os.path.join(DATA_DIR, fname))
    logger.info("JSONL rotation check complete")

    from companion.bot_core import memory_store

    # Migrate timeline events to SQLite if needed
    from companion.config import TIMELINE_PATH
    if os.path.exists(TIMELINE_PATH):
        logger.info("Migrating timeline events from JSONL to SQLite...")
        try:
            import json
            import hashlib
            events = []
            with open(TIMELINE_PATH, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        e = json.loads(line)
                        events.append(e)
                    except Exception:
                        pass
            if events:
                for e in events:
                    h = hashlib.md5(f"{e['date']}_{e['event']}".encode("utf-8")).hexdigest()[:12]
                    id_ = f"evt_{h}"
                    memory_store.db.save_event(id_, e["date"], e["event"], e.get("importance", 5), e.get("description", ""))
            bak_path = TIMELINE_PATH + ".bak"
            os.rename(TIMELINE_PATH, bak_path)
            logger.info("Timeline migration complete. Archived to %s", bak_path)
        except Exception as exc:
            logger.error("Timeline migration failed: %s", exc)

    try:
        result = memory_store.reindex_all()
        logger.info("Vector index initialized: %s", result)
    except Exception as exc:
        logger.warning("Vector index initialization skipped: %s", exc)

    logger.info("Testing embedding API on startup...")
    if not memory_store.vector.test_embeddings():
        logger.critical("Embedding API test failed on startup. Disabling vector retrieval.")
        memory_store.vector.embeddings_enabled = False
    else:
        logger.info("Embedding API test successful.")

    bot = Bot(token=API_TOKEN)
    dp = Dispatcher()
    dp.message.outer_middleware(AuthMiddleware())
    dp.callback_query.outer_middleware(AuthMiddleware())
    register_handlers(dp, bot)
    await setup_bot_commands(bot)

    logger.info("Companion bot v2 started — memory architecture active")

    stop_event = asyncio.Event()

    def _signal_handler():
        logger.info("Shutdown signal received, stopping...")
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass

    # Start proactive ping loop (background)
    from companion.bot_core import proactive_ping_loop
    ping_task = asyncio.create_task(proactive_ping_loop(bot))

    try:
        await dp.start_polling(bot, handle_as_tasks=True)
    finally:
        logger.info("Shutting down bot...")
        ping_task.cancel()
        try:
            await asyncio.gather(ping_task, return_exceptions=True)
        except Exception:
            pass
        from companion.background_scheduler import cancel_all_tasks
        await cancel_all_tasks()
        await bot.session.close()
        logger.info("Bot shutdown complete.")


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
