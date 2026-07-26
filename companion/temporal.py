"""Temporal & Contextual Awareness module for Amargon's Void."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

RUSSIAN_DAYS = {
    0: "Понедельник",
    1: "Вторник",
    2: "Среда",
    3: "Четверг",
    4: "Пятница",
    5: "Суббота",
    6: "Воскресенье",
}


def get_day_name_ru(dt: datetime) -> str:
    return RUSSIAN_DAYS.get(dt.weekday(), "Неизвестный день")


def get_day_phase(dt: datetime) -> tuple[str, str]:
    """Return (phase_name_ru, phase_category)."""
    hour = dt.hour
    if 1 <= hour < 5:
        return "Глубокая ночь", "Ночной режим / бессонница / поздненочной кодинг"
    elif 5 <= hour < 9:
        return "Раннее утро", "Начало дня / пробуждение"
    elif 9 <= hour < 18:
        return "Дневное время", "Рабочий ритм / текущие дела"
    elif 18 <= hour < 23:
        return "Вечер", "Время отдыха / личные дела / прогулки с Морзиком"
    else:  # 23:00 - 01:00
        return "Поздний вечер / Полночь", "Завершение дня / поздний отдых"


def get_inactivity_gap(store: Any) -> tuple[float, str]:
    """Calculate hours since the last user message in SQLite database.

    Returns (gap_in_hours, human_readable_description).
    """
    try:
        with store.db._conn() as conn:
            row = conn.execute(
                "SELECT ts FROM messages WHERE role = 'user' ORDER BY ts DESC LIMIT 1"
            ).fetchone()
            if not row or not row[0]:
                return 0.0, "Первое начало диалога"
            
            last_ts_str = row[0]
            # Handle ISO format
            if "T" in last_ts_str:
                last_dt = datetime.fromisoformat(last_ts_str)
            else:
                last_dt = datetime.strptime(last_ts_str, "%Y-%m-%d %H:%M:%S")
            
            now = datetime.now()
            diff_seconds = (now - last_dt).total_seconds()
            gap_hours = max(0.0, diff_seconds / 3600.0)

            if gap_hours < 1.0:
                desc = "Непрерывный диалог (менее 1 часа)"
            elif gap_hours < 6.0:
                desc = f"Пауза около {int(gap_hours)} ч. (возврат к общению в течение дня)"
            elif gap_hours < 24.0:
                desc = f"Первый разговор за день (пауза {gap_hours:.1f} ч.)"
            elif gap_hours < 72.0:
                days = gap_hours / 24.0
                desc = f"Пауза в {days:.1f} дн. (Иван не писал пару дней)"
            else:
                days = gap_hours / 24.0
                desc = f"Длительный перерыв в общении ({int(days)} дн.)"

            return gap_hours, desc
    except Exception as e:
        logger.warning(f"Failed to calculate inactivity gap: {e}")
        return 0.0, "Диалог продолжается"


def generate_temporal_guidance(phase_name: str, day_name: str, gap_hours: float) -> str:
    """Generate subtle behavioral guidance for the LLM companion based on time & gap."""
    guidelines = []
    
    # Gap guidance
    if gap_hours >= 72.0:
        guidelines.append(
            "Иван не писал несколько дней — можно мягко поинтересоваться, как прошли эти дни, без навязчивости."
        )
    elif gap_hours >= 24.0:
        guidelines.append(
            "Это первое общение за день — естественное приветствие и учет настроения дня приветствуются."
        )
        
    # Night/Weekend guidance
    if phase_name == "Глубокая ночь":
        guidelines.append(
            "Сейчас глубокая ночь — учитывай возможный ночной режим работы или усталость, будь тепло и лаконично поддерживающим."
        )
    elif day_name in ("Суббота", "Воскресенье"):
        guidelines.append(
            "Сегодня выходной день — отличный период для отдыха, прогулок с собакой Морзиком и неторопливых тем."
        )
    elif day_name == "Пятница" and phase_name in ("Вечер", "Поздний вечер / Полночь"):
        guidelines.append(
            "Конец рабочей недели — время для расслабления и подведения итогов."
        )
    elif day_name == "Понедельник" and phase_name in ("Раннее утро", "Дневное время"):
        guidelines.append(
            "Начало рабочей недели — рабочий настрой, возможны сложности с задачами или созвонами."
        )
        
    if not guidelines:
        guidelines.append("Учитывай обычный дневной ритм общения, будь естественен.")
        
    return " ".join(guidelines)


def build_temporal_context_block(store: Any) -> str:
    """Construct full markdown TEMPORAL & CONTEXTUAL AWARENESS block for system instruction."""
    now = datetime.now()
    now_str = now.strftime("%d.%m.%Y %H:%M")
    day_name = get_day_name_ru(now)
    phase_name, phase_cat = get_day_phase(now)
    gap_hours, gap_desc = get_inactivity_gap(store)
    guidance = generate_temporal_guidance(phase_name, day_name, gap_hours)

    lines = [
        "# TEMPORAL & CONTEXTUAL AWARENESS",
        f"- Текущая дата и время: {day_name}, {now_str}",
        f"- Фаза суток: {phase_name} ({phase_cat})",
        f"- Интервал с прошлого сообщения: {gap_desc}",
        f"- Контекстное указание: {guidance}",
    ]
    return "\n".join(lines)
