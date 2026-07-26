"""Temporal & Contextual Awareness module for Amargon's Void."""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

try:
    from zoneinfo import ZoneInfo
    MINSK_TZ = ZoneInfo("Europe/Minsk")
except Exception:
    MINSK_TZ = timezone(timedelta(hours=3))

logger = logging.getLogger(__name__)


def get_current_time_minsk() -> datetime:
    """Return current time in Europe/Minsk timezone."""
    return datetime.now(MINSK_TZ)


def _plural_ru(n: int, form1: str, form2: str, form5: str) -> str:
    n = abs(n) % 100
    n1 = n % 10
    if 10 < n < 20:
        return form5
    if 1 < n1 < 5:
        return form2
    if n1 == 1:
        return form1
    return form5


def format_relative_time(ts_str: str, now: datetime | None = None) -> str:
    """Convert absolute timestamp string into Russian human-readable relative time."""
    if not ts_str or not ts_str.strip():
        return ""
    try:
        ts_clean = ts_str.strip()
        if "T" in ts_clean:
            dt = datetime.fromisoformat(ts_clean)
        elif " " in ts_clean:
            dt = datetime.strptime(ts_clean[:19], "%Y-%m-%d %H:%M:%S")
        elif len(ts_clean) == 10 and ts_clean.count("-") == 2:
            dt = datetime.strptime(ts_clean, "%Y-%m-%d")
        else:
            return ts_str

        now_dt = now or get_current_time_minsk()
        if dt.tzinfo is not None and now_dt.tzinfo is None:
            dt = dt.replace(tzinfo=None)
        elif dt.tzinfo is None and now_dt.tzinfo is not None:
            now_dt = now_dt.replace(tzinfo=None)

        diff = now_dt - dt
        seconds = diff.total_seconds()
        if seconds < 60:
            return "только что"
        minutes = int(seconds // 60)
        if minutes < 60:
            return f"{minutes} {_plural_ru(minutes, 'минуту', 'минуты', 'минут')} назад"
        hours = int(seconds // 3600)
        if hours < 24:
            return f"{hours} {_plural_ru(hours, 'час', 'часа', 'часов')} назад"
        days = int(seconds // 86400)
        if days == 1:
            return "вчера"
        if days == 2:
            return "позавчера"
        if days < 7:
            return f"{days} {_plural_ru(days, 'день', 'дня', 'дней')} назад"
        weeks = int(days // 7)
        if weeks < 5:
            return f"{weeks} {_plural_ru(weeks, 'неделю', 'недели', 'недель')} назад"
        months = int(days // 30)
        if months < 12:
            return f"{months} {_plural_ru(months, 'месяц', 'месяца', 'месяцев')} назад"
        years = max(1, int(days // 365))
        return f"{years} {_plural_ru(years, 'год', 'года', 'лет')} назад"
    except Exception:
        return ts_str


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
            if not isinstance(last_ts_str, str):
                return 0.0, "Первое начало диалога"
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


def build_temporal_context_block(store: Any = None) -> str:
    """Construct full markdown TEMPORAL & CONTEXTUAL AWARENESS block for system instruction."""
    now = get_current_time_minsk()
    now_str = now.strftime("%d.%m.%Y %H:%M")
    day_name = get_day_name_ru(now)
    phase_name, phase_cat = get_day_phase(now)
    gap_hours, gap_desc = get_inactivity_gap(store) if store else (0.0, "Диалог продолжается")
    guidance = generate_temporal_guidance(phase_name, day_name, gap_hours)

    from companion.user_model import user_model
    effective_state, momentum_metrics = user_model.get_effective_emotional_state()
    is_low_energy = effective_state in ("depressed", "anxious") or momentum_metrics.get("energy", 0.5) < 0.35 or momentum_metrics.get("sadness", 0.0) > 0.40

    if is_low_energy:
        morning_phrase = "Утром (с 06:00 до 11:00) учитывай зафиксированный спад сил Ивана после тяжелых дней. НЕ ИСПОЛЬЗУЙ бодрые утренние приветствия. Начни разговор мягко и спокойно без давления и восклицаний."
    else:
        morning_phrase = "Утром (с 06:00 до 11:00) используй утренние приветствия."

    lines = [
        "# TEMPORAL & CONTEXTUAL AWARENESS",
        f"Текущее локальное время: {day_name}, {now_str}. Анализируй время суток при ответе. Если сейчас глубокая ночь (с 01:00 до 05:00), а Иван пишет тебе, органично поинтересуйся, почему он не спит (возможно, опять засиделся в Reaper, играет в Dota 2 или пишет 'Ивангелие'). {morning_phrase}",
        f"- Фаза суток: {phase_name} ({phase_cat})",
        f"- Интервал с прошлого сообщения: {gap_desc}",
        f"- Контекстное указание: {guidance}",
    ]
    return "\n".join(lines)

