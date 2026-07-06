import time
import logging
from companion.user_model import user_model
from companion.config import ADMIN_IDS
from companion.proactive.engagement import evaluate_engagement, record_ping_sent as engagement_record_ping
from companion.proactive.reasons import select_reason, PingReason, ReasonDecision
from companion.proactive.collector import collect_context
from companion.proactive.formatter import format_ping
from companion.proactive.telemetry import record_ping_sent as telemetry_record_ping

logger = logging.getLogger(__name__)

async def run_proactive_loop(bot, debug: bool = False):
    """
    Главный цикл проактивности бота.
    1. Оценивает, стоит ли отправлять сообщение.
    2. Выбирает причину.
    3. Собирает контекст.
    4. Формирует текст через LLM.
    5. Отправляет в Telegram и записывает телеметрию.
    """
    current_ts = time.time()
    
    # 1. Engagement Gate
    # Ищем last_activity для главного пользователя (для V1 используем ADMIN_ID или первый доступный)
    from companion.bot_core import last_activity  # lazy import: избегаем циклического импорта
    target_user_id = ADMIN_IDS[0] if ADMIN_IDS else 0
    if target_user_id == 0:
        logger.warning("run_proactive_loop: ADMIN_ID not configured.")
        return
        
    last_ts = last_activity.get(target_user_id, 0.0)
    decision = evaluate_engagement(user_model, last_ts, current_ts)
    
    if not decision.allowed:
        logger.info(f"Proactive ping skipped. Reason: {decision.reason}, Score: {decision.score}")
        return
        
    # 2. Reason Selector
    # В V1 мы жестко собираем кандидатов. Потом это можно будет делать динамически.
    candidates = [
        ReasonDecision(reason=PingReason.LONG_SILENCE),
        ReasonDecision(reason=PingReason.EMOTIONAL_CHECKIN),
        ReasonDecision(reason=PingReason.UNFINISHED_GOAL),
    ]
    
    selected_reason = select_reason(candidates)
    if not selected_reason:
        logger.warning("Proactive ping failed: no reason selected.")
        return
        
    # 3. Context Collector
    payload = collect_context(selected_reason, user_model)
    
    # 4. Policy Engine V6 -> Formatter
    from companion.llm.prompts import STRATEGY_PROFILES, TONE_PROFILES
    state = user_model.data.get("emotional_timeline", {}).get("baseline_state", "neutral")
    strategy_profile = STRATEGY_PROFILES.get(state, STRATEGY_PROFILES["neutral"])
    tone_profile = TONE_PROFILES.get(state, TONE_PROFILES["neutral"])
    
    message = await format_ping(
        payload=payload,
        strategy=strategy_profile,
        tone=tone_profile,
        debug=debug
    )
    
    if debug and isinstance(message, dict):
        logger.info(f"DEBUG Proactive Payload: {message}")
        message = message["message"]
        
    if not message or message.startswith("Error"):
        logger.error(f"Failed to generate proactive message: {message}")
        return
        
    # 5. Telegram Sender
    try:
        if not debug:
            await bot.send_message(chat_id=target_user_id, text=message)
            
        # 6. Telemetry & Stats
        engagement_record_ping(user_model, current_ts)
        telemetry_record_ping(
            reason=selected_reason.reason.name,
            baseline_state=state,
            urgency=payload.urgency,
            message=message
        )
        logger.info(f"Proactive ping sent successfully to {target_user_id}")
    except Exception as e:
        logger.error(f"Failed to send proactive ping via Telegram: {e}")
