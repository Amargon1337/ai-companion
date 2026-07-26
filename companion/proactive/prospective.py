"""Automatic prospective memory extraction and due-task handling."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any

from companion.config import MODEL_NAME
from companion.llm import client as llm

logger = logging.getLogger(__name__)


EXTRACT_PROMPT = """Extract future user intentions from the message.
Return ONLY a JSON array. Each item: {"text": string, "due_ts": unix_timestamp, "confidence": 0..1}.
Only include clear future reminders/tasks/promises/plans. If no clear due time, infer conservative timestamp from context when possible, otherwise return [].
Current UNIX time: {now_ts}
Current local datetime: {now_iso}
Message: {message}
"""


async def extract_prospective_tasks(store, message_text: str, source_message_id: str | None = None) -> int:
    clean = (message_text or "").strip()
    if len(clean) < 8 or not _looks_future_relevant(clean):
        return 0

    now_ts = time.time()
    prompt = EXTRACT_PROMPT.format(
        now_ts=int(now_ts),
        now_iso=datetime.now().isoformat(timespec="minutes"),
        message=clean[:1500],
    )
    try:
        raw = await llm.aio_oneshot(prompt, model=MODEL_NAME)
        items = llm.parse_json_array(raw)
    except Exception as exc:
        logger.debug("Prospective extraction skipped: %s", exc)
        items = _heuristic_extract(clean, now_ts)

    created = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        due_ts = _coerce_due_ts(item.get("due_ts"), now_ts)
        confidence = float(item.get("confidence", 0.6) or 0.6)
        if not text or not due_ts or confidence < 0.55:
            continue
        task_id = "ptask_" + hashlib.sha1(f"{text}|{int(due_ts)}".encode("utf-8")).hexdigest()[:14]
        await store.db.async_upsert_prospective_task({
            "task_id": task_id,
            "text": text[:500],
            "due_ts": due_ts,
            "status": "pending",
            "source_message_id": source_message_id,
            "created_at": datetime.now().isoformat(),
            "metadata": {"confidence": confidence, "source": "auto_dialogue"},
        })
        created += 1
    return created


def _looks_future_relevant(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "завтра", "послезавтра", "через", "напомни", "не забыть", "нужно будет",
        "надо будет", "сделаю", "позже", "вечером", "утром", "на выходных",
    )
    return any(marker in lowered for marker in markers)


def _coerce_due_ts(value: Any, now_ts: float) -> float | None:
    try:
        due_ts = float(value)
    except (TypeError, ValueError):
        return None
    if due_ts <= now_ts - 60 or due_ts > now_ts + 366 * 24 * 3600:
        return None
    return due_ts


def _heuristic_extract(text: str, now_ts: float) -> list[dict[str, Any]]:
    lowered = text.lower()
    delta = None
    if "послезавтра" in lowered:
        delta = timedelta(days=2)
    elif "завтра" in lowered:
        delta = timedelta(days=1)
    else:
        match = re.search(r"через\s+(\d+)\s+(минут|час|дн)", lowered)
        if match:
            amount = int(match.group(1))
            unit = match.group(2)
            if unit.startswith("минут"):
                delta = timedelta(minutes=amount)
            elif unit.startswith("час"):
                delta = timedelta(hours=amount)
            else:
                delta = timedelta(days=amount)
    if not delta:
        return []
    return [{"text": text[:500], "due_ts": now_ts + delta.total_seconds(), "confidence": 0.6}]


def build_due_task_payload(task: dict[str, Any]) -> str:
    due = datetime.fromtimestamp(float(task["due_ts"])).strftime("%Y-%m-%d %H:%M")
    return json.dumps({"task": task["text"], "due": due}, ensure_ascii=False)
