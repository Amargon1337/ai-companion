"""Legacy file storage — diary, summaries, timeline, mood, etc."""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from companion.config import (
    BASE_DIR,
    DIARY_PATH,
    IVAN_PATH,
    MONTHBOOK_DIR,
    MOOD_PATH,
    PERMANENT_NOTES_PATH,
    SUMMARIES_PATH,
    TIMELINE_PATH,
    TODO_PATH,
)


class LegacyStorage:
    @staticmethod
    def _atomic_write(path: str, content: str, mode: str = "w") -> None:
        """Write file atomically via temp file + replace (prevents race conditions)."""
        parent = os.path.dirname(path) or "."
        os.makedirs(parent, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=parent, suffix=".tmp")
        try:
            with os.fdopen(fd, mode, encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp, path)
        except Exception:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    @staticmethod
    def _atomic_write_json(path: str, data, **kwargs) -> None:
        """Write JSON atomically."""
        parent = os.path.dirname(path) or "."
        os.makedirs(parent, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, **kwargs)
            os.replace(tmp, path)
        except Exception:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    @staticmethod
    def load_memory() -> str:
        if os.path.exists(IVAN_PATH):
            with open(IVAN_PATH, encoding="utf-8") as f:
                return f.read()
        return ""

    @staticmethod
    def save_diary(entry: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(DIARY_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {entry}\n")

    @staticmethod
    def save_summary(summary: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        with open(SUMMARIES_PATH, "a", encoding="utf-8") as f:
            f.write(f"\n[{ts}]\n{summary}\n{'-' * 60}\n")

    @staticmethod
    def load_latest_summary() -> str:
        if not os.path.exists(SUMMARIES_PATH):
            return ""
        with open(SUMMARIES_PATH, encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            return ""
        blocks = [b.strip() for b in content.split("-" * 60) if b.strip()]
        return blocks[-1] if blocks else ""

    @staticmethod
    def load_all_summaries() -> list[str]:
        if not os.path.exists(SUMMARIES_PATH):
            return []
        with open(SUMMARIES_PATH, encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            return []
        return [b.strip() for b in content.split("-" * 60) if b.strip()]

    @staticmethod
    def load_master_summary() -> str:
        """
        БЛОК 3: SUMMARY STACK - Tier 3 (Master Summary)

        Загружает master summary - сжатую версию всей истории пользователя.

        ПРОБЛЕМА: Старые summaries не используются, контекст теряется.
        РЕШЕНИЕ: Master summary аккумулирует ключевую информацию из всех compress.

        ВЛИЯНИЕ НА КАЧЕСТВО:
        - После рестарта бот помнит долгосрочный контекст
        - Важные факты из прошлых месяцев доступны
        - Continuity через длинные периоды общения
        """
        master_path = os.path.join(BASE_DIR, "master_summary.txt")
        if not os.path.exists(master_path):
            return ""
        with open(master_path, encoding="utf-8") as f:
            return f.read().strip()

    @staticmethod
    def save_master_summary(content: str) -> None:
        """Сохраняет обновлённый master summary."""
        master_path = os.path.join(BASE_DIR, "master_summary.txt")
        LegacyStorage._atomic_write(master_path, content)

    @staticmethod
    def save_event(event: str, imp: int, desc: str) -> None:
        entry = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "event": event,
            "importance": imp,
            "description": desc,
        }
        with open(TIMELINE_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    @staticmethod
    def load_events(year: int | None = None) -> list[dict]:
        if not os.path.exists(TIMELINE_PATH):
            return []
        events = []
        with open(TIMELINE_PATH, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    e = json.loads(line)
                    if year is None or e["date"].startswith(str(year)):
                        events.append(e)
                except json.JSONDecodeError:
                    pass
        return sorted(events, key=lambda x: x["date"])

    @staticmethod
    def save_mood(score: int, tags: list[str], note: str) -> None:
        entry = {"ts": datetime.now().isoformat(), "score": score, "tags": tags, "note": note}
        with open(MOOD_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    @staticmethod
    def load_mood(days: int = 14) -> list[dict]:
        if not os.path.exists(MOOD_PATH):
            return []
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        moods = []
        with open(MOOD_PATH, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    e = json.loads(line)
                    if e.get("ts", "") >= cutoff:
                        moods.append(e)
                except json.JSONDecodeError:
                    pass
        return moods

    @staticmethod
    def save_permanent_note(note: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(PERMANENT_NOTES_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {note}\n")

    @staticmethod
    def load_permanent_notes() -> str:
        if not os.path.exists(PERMANENT_NOTES_PATH):
            return ""
        with open(PERMANENT_NOTES_PATH, encoding="utf-8") as f:
            return f.read().strip()

    @staticmethod
    def count_permanent_notes() -> int:
        notes = LegacyStorage.load_permanent_notes()
        return len([l for l in notes.split("\n") if l.strip()])

    @staticmethod
    def get_monthbook_path(ym: str) -> str:
        return os.path.join(MONTHBOOK_DIR, f"{ym}.txt")

    @staticmethod
    def load_monthbook(ym: str) -> str:
        p = LegacyStorage.get_monthbook_path(ym)
        if not os.path.exists(p):
            return ""
        with open(p, encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def save_monthbook(ym: str, content: str) -> None:
        LegacyStorage._atomic_write(LegacyStorage.get_monthbook_path(ym), content)

    @staticmethod
    def load_todos() -> list[dict]:
        if not os.path.exists(TODO_PATH):
            return []
        with open(TODO_PATH, encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def save_todos(todos: list[dict]) -> None:
        LegacyStorage._atomic_write_json(TODO_PATH, todos, indent=2)

    @staticmethod
    def get_week_diary() -> list[str]:
        if not os.path.exists(DIARY_PATH):
            return []
        week_ago = datetime.now() - timedelta(days=7)
        res = []
        with open(DIARY_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    if datetime.strptime(line[1:20], "%Y-%m-%d %H:%M:%S") >= week_ago:
                        res.append(line)
                except (ValueError, IndexError):
                    pass
        return res

    @staticmethod
    def count_diary_entries() -> int:
        if not os.path.exists(DIARY_PATH):
            return 0
        with open(DIARY_PATH, encoding="utf-8") as f:
            return sum(1 for l in f if l.strip())

    @staticmethod
    def get_selfie_data() -> list[str]:
        parts = []
        mem = LegacyStorage.load_memory()
        if mem:
            parts.append(f"[Статичная память]\n{mem}")
        notes = LegacyStorage.load_permanent_notes()
        if notes:
            parts.append(f"[Постоянные заметки]\n{notes}")
        if os.path.exists(DIARY_PATH):
            with open(DIARY_PATH, encoding="utf-8") as f:
                lines = f.read().strip().split("\n")
            if lines:
                parts.append("[Дневник]\n" + "\n".join(lines[-100:]))
        lat = LegacyStorage.load_latest_summary()
        if lat:
            parts.append(f"[Саммери]\n{lat}")
        return parts

    @staticmethod
    def parse_remember_command(text: str) -> str | None:
        m = re.match(r"^запомни(?:ть)?[!:,.]?\s*(.*)", text.strip(), re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(1).strip()
        return None
