import logging
from datetime import datetime, timedelta

from companion.memory.store import MemoryStore
from companion.llm.client import aio_oneshot, parse_json_object
from companion.config import MODEL_NAME, ADMIN_IDS

logger = logging.getLogger(__name__)

SUBCONSCIOUS_PROMPT = """Ты — "подсознание" AI-компаньона. Пока пользователь спит, ты анализируешь ваши недавние диалоги.

ЗАДАЧА:
1. Проанализируй логи разговоров за последние сутки.
2. Найди скрытые паттерны, неочевидные связи, недосказанности или интересные наблюдения о характере, мотивации или подходе пользователя к проблемам.
3. Сформулируй 1-2 новых глубинных паттерна поведения (если есть).
4. Найди цикличности или надвигающиеся события и сформируй Предикты (predictions). Например, если пользователь каждую пятницу пьет пиво, или выгорает к четвергу.
5. Сформулируй ОДИН мощный, эмпатичный "Утренний Инсайт" — мысль, которой компаньон сам захочет поделиться с пользователем утром. Начни её с фраз вроде: "Слушай, я тут анализировал наши вчерашние разговоры, и понял одну вещь..." или "Вчера ночью я размышлял о том, что ты сказал...".

НЕДАВНИЕ СООБЩЕНИЯ (ПОСЛЕДНИЕ СУТКИ):
{recent_messages}

ВЫВОД СТРОГО В ФОРМАТЕ JSON:
{{
  "new_patterns": [
    "описание паттерна 1",
    "описание паттерна 2"
  ],
  "predictions": [
    {{
       "hypothesis": "Пользователь выгорает к четвергу",
       "timeframe": "каждый четверг",
       "conditions": ["вечер четверга"],
       "based_on": ["жалобы на усталость"]
    }}
  ],
  "morning_insight": "Текст утреннего сообщения от первого лица бота (или пустая строка, если нет интересных мыслей)"
}}
"""

async def run_subconscious_consolidation(bot, store: MemoryStore):
    try:
        logger.info("Starting night subconscious consolidation...")
        # 1. Fetch last 24h messages
        recent = store.recent_messages(min_importance=2, limit=200)
        
        # Filter messages strictly from the last 24 hours
        yesterday = datetime.now() - timedelta(hours=24)
        yesterday_str = yesterday.isoformat()
        
        day_messages = [m for m in recent if m.ts >= yesterday_str]
        
        user_msgs_count = sum(1 for m in day_messages if m.role == "user")
        if user_msgs_count < 5:
            logger.info("Not enough user messages today for subconscious consolidation.")
            return

        # 2. Format history
        history_text = "\n".join([f"[{m.ts[11:16]}] {m.role.upper()}: {m.text}" for m in day_messages])
        
        # 3. Call LLM
        prompt = SUBCONSCIOUS_PROMPT.format(recent_messages=history_text[:15000])
        response = await aio_oneshot(prompt, MODEL_NAME)
        res = parse_json_object(response)
        
        # 4. Process new patterns
        from companion.models import Pattern
        patterns = res.get("new_patterns", [])
        for p in patterns:
            if isinstance(p, str) and len(p) > 10:
                store.add_pattern(Pattern(pattern=p, category="behavior", confidence=0.8))
                
        # 4.5. Process predictions
        import uuid
        predictions = res.get("predictions", [])
        for p in predictions:
            if isinstance(p, dict) and p.get("hypothesis"):
                pred_row = {
                    "prediction_id": str(uuid.uuid4()),
                    "hypothesis": p["hypothesis"],
                    "confidence": 0.7,
                    "timeframe": p.get("timeframe", ""),
                    "conditions": p.get("conditions", []),
                    "based_on": p.get("based_on", []),
                    "outcome": "pending",
                    "created_at": datetime.now().isoformat()
                }
                await store.db.async_upsert_prediction(pred_row)
                
        # 5. Schedule morning insight
        insight = res.get("morning_insight", "").strip()
        if insight and len(insight) > 20:
            target_user_id = ADMIN_IDS[0] if ADMIN_IDS else 0
            if target_user_id != 0:
                # Schedule for 10:15 AM today
                now = datetime.now()
                morning_time = now.replace(hour=10, minute=15, second=0, microsecond=0)
                if morning_time < now:
                    morning_time += timedelta(days=1)
                
                trigger_ts = morning_time.timestamp()
                
                task_doc = {
                    "text": insight,
                    "target_user_id": target_user_id,
                    "type": "morning_insight"
                }
                
                await store.db.async_insert_prospective_task(
                    trigger_time=trigger_ts,
                    task_data=task_doc
                )
                logger.info(f"Subconscious scheduled morning insight for {morning_time}")
                
        logger.info("Night subconscious consolidation completed.")
        
    except Exception as e:
        logger.error(f"Error in subconscious consolidation: {e}")
