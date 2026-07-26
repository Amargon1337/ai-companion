import uuid
from datetime import datetime
from companion.storage.sqlite_db import MemoryDatabase

def record_ping_sent(
    reason: str,
    baseline_state: str,
    urgency: int,
    message: str
) -> str:
    """Записывает факт отправки пинга и возвращает его ID."""
    db = MemoryDatabase()
    try:
        event_id = str(uuid.uuid4())
        now_iso = datetime.now().isoformat()
        
        with db._conn() as conn:
            conn.execute(
                """
                INSERT INTO proactive_events 
                (id, timestamp, reason, baseline_state, urgency, message, sent, user_replied)
                VALUES (?, ?, ?, ?, ?, ?, 1, 0)
                """,
                (event_id, now_iso, reason, baseline_state, urgency, message)
            )
        return event_id
    finally:
        db.close()

def record_ping_reply(
    reply_delay_hours: float
) -> None:
    """
    Помечает последний отправленный пинг как отвеченный.
    Ищет последний пинг, который еще не отвечен.
    """
    db = MemoryDatabase()
    try:
        with db._conn() as conn:
            # Находим последний отправленный пинг
            row = conn.execute(
                "SELECT id FROM proactive_events ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            
            if row:
                event_id = row["id"]
                conn.execute(
                    """
                    UPDATE proactive_events 
                    SET user_replied = 1, reply_delay_hours = ? 
                    WHERE id = ?
                    """,
                    (reply_delay_hours, event_id)
                )
    finally:
        db.close()

def get_proactive_stats() -> dict:
    """Возвращает базовую аналитику по пингам для отладки."""
    db = MemoryDatabase()
    try:
        stats = {}
        with db._conn() as conn:
            total = conn.execute("SELECT COUNT(*) as c FROM proactive_events").fetchone()["c"]
            replied = conn.execute("SELECT COUNT(*) as c FROM proactive_events WHERE user_replied = 1").fetchone()["c"]
            
            stats["total_sent"] = total
            stats["replied"] = replied
            stats["reply_rate"] = (replied / total) if total > 0 else 0.0
            
            reasons = conn.execute(
                """
                SELECT reason, COUNT(*) as sent, SUM(user_replied) as replies 
                FROM proactive_events 
                GROUP BY reason
                """
            ).fetchall()
            
            stats["by_reason"] = {
                r["reason"]: {"sent": r["sent"], "replies": r["replies"]} 
                for r in reasons
            }
            
        return stats
    finally:
        db.close()
