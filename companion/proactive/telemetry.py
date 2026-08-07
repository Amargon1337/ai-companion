"""Proactive telemetry through the production composition-root database."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
def _db():
    from companion.container import get_container
    return get_container().db


def record_ping_sent(
    reason: str,
    baseline_state: str,
    urgency: int,
    message: str
) -> str:
    """Записывает факт отправки пинга и возвращает его ID."""
    db = _db()
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


def record_ping_reply(
    reply_delay_hours: float
) -> None:
    """
    Помечает последний отправленный пинг как отвеченный.
    Ищет последний пинг, который еще не отвечен.
    """
    with _db()._conn() as conn:
        row = conn.execute(
            "SELECT id FROM proactive_events WHERE sent=1 AND user_replied=0 ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()

        if row:
            conn.execute(
                """
                UPDATE proactive_events
                SET user_replied = 1, reply_delay_hours = ?
                WHERE id = ?
                """,
                (reply_delay_hours, row["id"])
            )


def get_recent_pings(limit: int = 3) -> list[str]:
    """Возвращает тексты последних отправленных проактивных сообщений."""
    try:
        with _db()._conn() as conn:
            rows = conn.execute(
                "SELECT message FROM proactive_events WHERE sent=1 ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [str(row["message"]) for row in rows if row["message"]]
    except Exception as exc:
        logging.getLogger(__name__).warning("Failed to fetch proactive telemetry: %s", exc)
        return []


def get_proactive_stats() -> dict:
    """Возвращает базовую аналитику по пингам для отладки."""
    try:
        with _db()._conn() as conn:
            total = int(conn.execute("SELECT COUNT(*) FROM proactive_events").fetchone()[0])
            replied = int(conn.execute("SELECT COUNT(*) FROM proactive_events WHERE user_replied=1").fetchone()[0])
            reasons = conn.execute(
                "SELECT reason, COUNT(*) as sent, SUM(user_replied) as replies FROM proactive_events GROUP BY reason"
            ).fetchall()

        return {
            "total_sent": total,
            "replied": replied,
            "reply_rate": (replied / total) if total > 0 else 0.0,
            "by_reason": {r["reason"]: {"sent": r["sent"], "replies": r["replies"]} for r in reasons},
        }
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Failed to fetch proactive stats: %s", e)
        return {}
