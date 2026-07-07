"""Runtime cognitive context providers for prompt injection."""
from __future__ import annotations

import html
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone as datetime_timezone
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from companion import config
from companion.storage.sqlite_db import MemoryDatabase

logger = logging.getLogger(__name__)


def _timezone(name: str):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name.upper() == "UTC":
            return datetime_timezone.utc
        logger.warning("Unknown timezone %s, falling back to UTC", name)
        return datetime_timezone.utc


@dataclass(frozen=True)
class RuntimeContext:
    time: str
    date: str
    weekday: str
    weekend: bool
    hour: int
    period: str
    vibe: str
    timezone: str
    uptime_seconds: int | None = None
    cpu_load: float | None = None
    memory_pressure: str = "unknown"

    def to_prompt_xml(self) -> str:
        weekend = "true" if self.weekend else "false"
        cpu = "" if self.cpu_load is None else f"{self.cpu_load:.2f}"
        uptime = "" if self.uptime_seconds is None else str(self.uptime_seconds)
        return (
            "<runtime_context>\n"
            f"  <temporal time=\"{html.escape(self.time)}\" date=\"{html.escape(self.date)}\" "
            f"weekday=\"{html.escape(self.weekday)}\" weekend=\"{weekend}\" hour=\"{self.hour}\" "
            f"period=\"{html.escape(self.period)}\" vibe=\"{html.escape(self.vibe)}\" "
            f"timezone=\"{html.escape(self.timezone)}\" />\n"
            f"  <system uptime_seconds=\"{html.escape(uptime)}\" cpu_load=\"{html.escape(cpu)}\" "
            f"memory_pressure=\"{html.escape(self.memory_pressure)}\" />\n"
            "</runtime_context>"
        )


class VibeResolver:
    def __init__(self, rules: list[tuple[range, str, str]] | None = None) -> None:
        self.rules = rules or self._load_rules()

    def resolve(self, hour: int) -> tuple[str, str]:
        for hours, period, vibe in self.rules:
            if hour in hours:
                return period, vibe
        return "midday", "neutral"

    def _load_rules(self) -> list[tuple[range, str, str]]:
        raw = os.getenv("TEMPORAL_VIBE_RULES", "")
        if raw:
            try:
                parsed = json.loads(raw)
                rules = []
                for item in parsed:
                    start = int(item["start"])
                    end = int(item["end"])
                    rules.append((range(start, end), str(item["period"]), str(item["vibe"])))
                if rules:
                    return rules
            except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                logger.warning("Invalid TEMPORAL_VIBE_RULES, using defaults: %s", exc)
        return [
            (range(0, 5), "night", "nocturnal_nihilism"),
            (range(5, 9), "morning", "morning_neutrality"),
            (range(9, 12), "morning", "productive_warmup"),
            (range(12, 17), "afternoon", "productive_focus"),
            (range(17, 21), "evening", "social_evening"),
            (range(21, 24), "evening", "reflective_evening"),
        ]


class TemporalContextProvider:
    def __init__(
        self,
        timezone_name: str | None = None,
        clock: Callable[[], datetime] | None = None,
        vibe_resolver: VibeResolver | None = None,
    ) -> None:
        self.timezone_name = timezone_name or config.LOCAL_TIMEZONE
        self.timezone = _timezone(self.timezone_name)
        if self.timezone is datetime_timezone.utc and self.timezone_name.upper() != "UTC":
            self.timezone_name = "UTC"
        self.clock = clock
        self.vibe_resolver = vibe_resolver or VibeResolver()
        self.process_started_at = time.time()

    def get_context(self) -> RuntimeContext:
        now = self.clock() if self.clock else datetime.now(self.timezone)
        if now.tzinfo is None:
            now = now.replace(tzinfo=self.timezone)
        local_now = now.astimezone(self.timezone)
        period, vibe = self.vibe_resolver.resolve(local_now.hour)
        cpu_load = None
        if hasattr(os, "getloadavg"):
            try:
                cpu_load = float(os.getloadavg()[0])
            except OSError:
                cpu_load = None
        return RuntimeContext(
            time=local_now.strftime("%H:%M"),
            date=local_now.date().isoformat(),
            weekday=local_now.strftime("%A").lower(),
            weekend=local_now.weekday() >= 5,
            hour=local_now.hour,
            period=period,
            vibe=vibe,
            timezone=self.timezone_name,
            uptime_seconds=int(time.time() - self.process_started_at),
            cpu_load=cpu_load,
        )


class RuntimeContextProvider:
    def __init__(self, temporal_provider: TemporalContextProvider | None = None, ttl_seconds: int = 30) -> None:
        self.temporal_provider = temporal_provider or TemporalContextProvider()
        self.ttl_seconds = ttl_seconds
        self._cached: RuntimeContext | None = None
        self._cached_at = 0.0

    def get_context(self) -> RuntimeContext:
        now = time.time()
        if self._cached and now - self._cached_at < self.ttl_seconds:
            return self._cached
        self._cached = self.temporal_provider.get_context()
        self._cached_at = now
        return self._cached


@dataclass(frozen=True)
class CounterValue:
    counter_name: str
    description: str
    days: int
    status: str
    starts_in_days: int | None = None


class TemporalDeltaEngine:
    def __init__(self, db: MemoryDatabase) -> None:
        self.db = db

    def create_counter(
        self,
        counter_name: str,
        description: str,
        start_date: date,
        timezone: str | None = None,
        allow_future: bool = False,
    ) -> int:
        tz = timezone or config.LOCAL_TIMEZONE
        today = datetime.now(_timezone(tz)).date()
        if start_date > today and not allow_future:
            raise ValueError("start_date cannot be in the future unless allow_future=True")
        return self.db.create_temporal_counter(counter_name, description, start_date.isoformat(), tz)

    def pause_counter(self, counter_name: str, pause_date: date, reason: str | None = None) -> None:
        self.db.pause_temporal_counter(counter_name, pause_date.isoformat(), reason)

    def resume_counter(self, counter_name: str, resume_date: date) -> None:
        self.db.resume_temporal_counter(counter_name, resume_date.isoformat())

    def archive_counter(self, counter_name: str, archived: bool = True) -> None:
        self.db.update_temporal_counter(counter_name, archived=archived)

    def delete_counter(self, counter_name: str) -> None:
        self.db.delete_temporal_counter(counter_name)

    def list_values(self) -> list[CounterValue]:
        values: list[CounterValue] = []
        for row in self.db.list_temporal_counters():
            start = date.fromisoformat(row["start_date"])
            tz = row["timezone"] or config.LOCAL_TIMEZONE
            today = datetime.now(_timezone(tz)).date()
            if start > today:
                values.append(CounterValue(row["counter_name"], row["description"], 0, row["status"], (start - today).days))
                continue
            elapsed = (today - start).days
            paused = 0
            for pause in self.db.list_temporal_counter_pauses(int(row["id"])):
                pause_start = date.fromisoformat(pause["pause_start_date"])
                pause_end = date.fromisoformat(pause["pause_end_date"]) if pause["pause_end_date"] else today
                if pause_end < pause_start:
                    continue
                paused += (pause_end - pause_start).days
            values.append(CounterValue(row["counter_name"], row["description"], max(0, elapsed - paused), row["status"]))
        return values

    def to_prompt_xml(self) -> str:
        lines = ["<temporal_deltas>"]
        for counter in self.list_values():
            attrs = [
                f'name="{html.escape(counter.counter_name)}"',
                f'description="{html.escape(counter.description)}"',
                f'days="{counter.days}"',
                f'status="{html.escape(counter.status)}"',
            ]
            if counter.starts_in_days is not None:
                attrs.append(f'starts_in_days="{counter.starts_in_days}"')
            lines.append(f"  <counter {' '.join(attrs)} />")
        lines.append("</temporal_deltas>")
        return "\n".join(lines)


class ContextAggregator:
    def __init__(self, db: MemoryDatabase, runtime_provider: RuntimeContextProvider | None = None) -> None:
        self.db = db
        self.runtime_provider = runtime_provider or RuntimeContextProvider()
        self.delta_engine = TemporalDeltaEngine(db)

    def build_prompt_block(self) -> str:
        blocks: list[str] = []
        if config.ENABLE_TEMPORAL_CONTEXT:
            try:
                blocks.append(self.runtime_provider.get_context().to_prompt_xml())
            except Exception as exc:
                logger.warning("Temporal context unavailable: %s", exc)
        if config.ENABLE_TEMPORAL_DELTAS:
            try:
                delta_xml = self.delta_engine.to_prompt_xml()
                if "<counter " in delta_xml:
                    blocks.append(delta_xml)
            except Exception as exc:
                logger.warning("Temporal deltas unavailable: %s", exc)
        return "\n\n".join(blocks)
