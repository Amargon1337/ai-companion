"""Application entry — bot startup."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from logging.handlers import RotatingFileHandler

from aiogram import BaseMiddleware, Bot, Dispatcher, types

from companion.config import ADMIN_IDS, API_TOKEN, DATA_DIR, LOG_LEVEL, LOG_PATH
from companion.handlers import register_handlers, setup_bot_commands

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
                await event.answer("Доступ ограничен, ты не Иван. Отказано в доступе.")
                return
        elif isinstance(event, types.CallbackQuery):
            user = event.from_user
            if not user or user.id not in ADMIN_IDS:
                await event.answer("Доступ закрыт.", show_alert=True)
                return
        return await handler(event, data)

def sanitize_and_scan_legacy_files() -> None:
    from companion.config import BASE_DIR, DATA_DIR
    from companion.security.sanitizer import sanitize_markup, _looks_like_injection
    from datetime import datetime
    from companion.storage.sqlite_db import MemoryDatabase
    import hashlib
    from companion.models import Fact

    quarantine_log_path = os.path.join(DATA_DIR, "quarantine_review.log")
    notes_path = os.path.join(BASE_DIR, "permanent_notes.txt")
    os.makedirs(DATA_DIR, exist_ok=True)
    db = MemoryDatabase()

    # 1. Sanitize permanent_notes.txt
    if os.path.exists(notes_path):
        try:
            with open(notes_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            
            sanitized_lines = []
            pending_notes_path = os.path.join(DATA_DIR, "permanent_notes.pending_review.txt")
            updated = False
            
            for line in lines:
                clean_line = line.strip()
                if not clean_line:
                    continue
                
                sanitized = sanitize_markup(clean_line) or ""
                if _looks_like_injection(sanitized):
                    with open(pending_notes_path, "a", encoding="utf-8") as pf:
                        pf.write(f"{clean_line}\n")
                    with open(quarantine_log_path, "a", encoding="utf-8") as qf:
                        qf.write(f"[{datetime.now().isoformat()}] [SUSPICIOUS] [permanent_notes.txt] {clean_line}\n")
                    updated = True
                else:
                    sanitized_lines.append(sanitized)
                    if sanitized != clean_line:
                        updated = True
            
            if updated:
                with open(notes_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(sanitized_lines) + "\n")
                logger.info("permanent_notes.txt sanitized/quarantined.")
        except Exception as e:
            logger.error(f"Error sanitizing permanent_notes.txt: {e}")

    # 2. Sanitize world model
    try:
        wm_path = os.path.join(DATA_DIR, "world_model.json")
        if os.path.exists(wm_path):
            with open(wm_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            contexts = data.get("active_contexts", [])
            pending_contexts = data.get("pending_review_contexts", [])
            sanitized_contexts = []
            updated = False
            for ctx in contexts:
                sanitized_ctx = sanitize_markup(ctx) or ""
                if _looks_like_injection(sanitized_ctx):
                    pending_contexts.append(ctx)
                    with open(quarantine_log_path, "a", encoding="utf-8") as qf:
                        qf.write(f"[{datetime.now().isoformat()}] [SUSPICIOUS] [world_model.json] {ctx}\n")
                    logger.warning("Suspicious injection pattern detected in world_model.json! Moved to pending_review_contexts and logged.")
                    updated = True
                    continue
                if sanitized_ctx != ctx:
                    updated = True
                sanitized_contexts.append(sanitized_ctx)
            if updated:
                data["active_contexts"] = sanitized_contexts
                data["pending_review_contexts"] = pending_contexts
                tmp = wm_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, wm_path)
                logger.info("world_model.json updated (sanitized and/or quarantined).")
                db.save_state_model("world", data)
        else:
            data = db.get_state_model("world")
            if data:
                contexts = data.get("active_contexts", [])
                pending_contexts = data.get("pending_review_contexts", [])
                new_active = []
                updated = False
                for ctx in contexts:
                    sanitized = sanitize_markup(ctx) or ""
                    if _looks_like_injection(sanitized):
                        pending_contexts.append(ctx)
                        with open(quarantine_log_path, "a", encoding="utf-8") as qf:
                            qf.write(f"[{datetime.now().isoformat()}] [SUSPICIOUS] [world_model] {ctx}\n")
                        updated = True
                    else:
                        new_active.append(sanitized)
                        if sanitized != ctx:
                            updated = True
                if updated:
                    data["active_contexts"] = new_active
                    data["pending_review_contexts"] = pending_contexts
                    db.save_state_model("world", data)
    except Exception as e:
        logger.error(f"Error sanitizing world model: {e}")

    # 3. Migrate permanent_notes.txt to SQLite
    if os.path.exists(notes_path):
        try:
            with open(notes_path, "r", encoding="utf-8") as f:
                notes = [line.strip() for line in f if line.strip()]
            if notes:
                migration_key = "legacy_permanent_notes_migrated"
                sig = hashlib.sha256("\n".join(notes).encode("utf-8")).hexdigest()
                if db.get_meta(migration_key, "") != sig:
                    existing = {f.get("fact", "").strip() for f in db.list_facts(status=None)}
                    for note in notes:
                        if note not in existing:
                            fact = Fact(fact=note, date=datetime.now().strftime("%Y-%m-%d"), 
                                        importance=9, confidence=1.0, source="migration", 
                                        source_type="user", memory_kind="permanent", tags=["permanent"])
                            db._insert_fact(fact.to_dict())
                    db.set_meta(migration_key, sig)
                    logger.info("Migrated permanent_notes.txt to SQLite.")
        except Exception as e:
            logger.error(f"Error migrating permanent_notes.txt: {e}")


async def run() -> None:
    # Sanitize and migrate remaining file inputs into SQLite.
    sanitize_and_scan_legacy_files()



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

    # Phase D (Self-Healing): reconcile SQLite facts against the in-memory FAISS
    # index. recover_index_consistency was implemented and unit-tested but never
    # invoked at startup — orphan vectors and dropped embeddings accumulated in
    # silence until a manual rebuild. Run it once after reindex; it is idempotent.
    try:
        recovered = memory_store.recover_index_consistency()
        if recovered.get("missing_computed") or recovered.get("orphans_removed"):
            logger.warning("Index consistency repair applied at startup: %s", recovered)
        else:
            logger.info("Index consistency verified at startup: %s", recovered)
    except Exception as exc:
        logger.warning("Index consistency repair skipped: %s", exc)

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
        
        # Stop embedding retry worker first
        from companion.bot_core import embedding_retry_worker
        if embedding_retry_worker:
            try:
                await embedding_retry_worker.stop()
                logger.info("Embedding Retry Worker stopped. Stats: %s", embedding_retry_worker.get_stats())
            except Exception as exc:
                logger.error("Failed to stop embedding retry worker: %s", exc, exc_info=True)
        
        try:
            memory_store.vector.flush_index()
            memory_store.close()
        except Exception as exc:
            logger.error("Failed to flush FAISS index or close memory store during shutdown: %s", exc, exc_info=True)
        ping_task.cancel()
        try:
            await asyncio.gather(ping_task, return_exceptions=True)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.debug("Background task cleanup failed: %s", exc, exc_info=True)
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
