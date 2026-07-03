import time
from dataclasses import dataclass
from companion.user_model import UserModel

@dataclass
class EngagementDecision:
    allowed: bool
    reason: str
    score: float

def evaluate_engagement(
    user_model: UserModel,
    last_activity_ts: float,
    current_ts: float = None
) -> EngagementDecision:
    """Оценивает, уместно ли сейчас отправлять проактивный пинг."""
    if current_ts is None:
        current_ts = time.time()
        
    proactivity = user_model.data.get("proactivity", {})
    last_ping_time = proactivity.get("last_ping_time", 0.0)
    ignored_pings = proactivity.get("consecutive_ignored_pings", 0)
    
    # 1. Защита от спама (Cooldown)
    # Минимум 12 часов между пингами
    COOLDOWN_HOURS = 12
    hours_since_last_ping = (current_ts - last_ping_time) / 3600.0
    if hours_since_last_ping < COOLDOWN_HOURS and last_ping_time > 0:
        return EngagementDecision(
            allowed=False,
            reason=f"cooldown_active (hours since last ping: {hours_since_last_ping:.1f} < {COOLDOWN_HOURS})",
            score=0.0
        )
        
    # 2. Защита от навязчивости (Ignored Pings)
    MAX_IGNORED_PINGS = 3
    if ignored_pings >= MAX_IGNORED_PINGS:
        return EngagementDecision(
            allowed=False,
            reason="too_many_ignored_pings",
            score=0.0
        )
        
    # 3. Достаточно ли пользователь молчал? (Min Silence)
    # Если юзер писал менее 6 часов назад, он и так активен
    MIN_SILENCE_HOURS = 6
    hours_of_silence = (current_ts - last_activity_ts) / 3600.0 if last_activity_ts > 0 else 999.0
    if hours_of_silence < MIN_SILENCE_HOURS:
        return EngagementDecision(
            allowed=False,
            reason=f"user_is_active (hours of silence: {hours_of_silence:.1f} < {MIN_SILENCE_HOURS})",
            score=0.2
        )
        
    # 4. Базовый Engagement Score
    base_score = 0.5
    
    # Модификаторы по стейту
    baseline = user_model.data.get("emotional_timeline", {}).get("baseline_state", "neutral")
    if baseline in ("depressed", "anxious"):
        # Если человеку плохо, пингуем чуть охотнее (увеличиваем score)
        base_score += 0.2
    elif baseline == "angry":
        # Если человек злится, лучше его лишний раз не трогать
        base_score -= 0.2
        
    # Модификаторы по тишине
    if hours_of_silence > 48:
        base_score += 0.1  # Долго не было
        
    # Модификаторы по игнорам
    if ignored_pings == 1:
        base_score -= 0.1
    elif ignored_pings == 2:
        base_score -= 0.3
        
    # Финальная оценка
    if base_score >= 0.4:
        return EngagementDecision(
            allowed=True,
            reason="engagement_ok",
            score=round(base_score, 2)
        )
    else:
        return EngagementDecision(
            allowed=False,
            reason="score_too_low",
            score=round(base_score, 2)
        )

def record_ping_sent(user_model: UserModel, current_ts: float = None):
    """Записывает факт отправки пинга (начинает cooldown и увеличивает счетчик игнора)."""
    if current_ts is None:
        current_ts = time.time()
        
    with user_model._lock:
        proact = user_model.data.setdefault("proactivity", {})
        proact["last_ping_time"] = current_ts
        proact["consecutive_ignored_pings"] = proact.get("consecutive_ignored_pings", 0) + 1
        proact["total_pings_sent"] = proact.get("total_pings_sent", 0) + 1

def record_user_replied(user_model: UserModel):
    """Сбрасывает счетчик игноров при ответе пользователя."""
    with user_model._lock:
        proact = user_model.data.setdefault("proactivity", {})
        proact["consecutive_ignored_pings"] = 0
