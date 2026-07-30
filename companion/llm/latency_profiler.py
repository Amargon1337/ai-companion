"""Phase 6.3: Latency Profiler — per-stage execution time tracking."""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class LatencyProfile:
    """Latency breakdown for a single turn."""
    stage_latencies: dict[str, float] = field(default_factory=dict)  # stage -> ms

    @property
    def total_ms(self) -> float:
        return sum(self.stage_latencies.values())

    def report(self) -> str:
        lines = []
        for name, ms in sorted(self.stage_latencies.items(), key=lambda x: -x[1]):
            lines.append(f"  {name:<20s} {ms:>8.1f} ms")
        lines.append(f"  {'─' * 30}")
        lines.append(f"  {'TOTAL':<20s} {self.total_ms:>8.1f} ms")
        return "\n".join(lines)


class LatencyProfiler:
    """Tracks execution time per pipeline stage."""

    def __init__(self) -> None:
        self._history: list[LatencyProfile] = []
        self._current: dict[str, float] = {}  # stage -> start_time

    def start_stage(self, stage_name: str) -> None:
        self._current[stage_name] = time.perf_counter()

    def end_stage(self, stage_name: str) -> float:
        start = self._current.pop(stage_name, None)
        if start is None:
            return 0.0
        elapsed_ms = (time.perf_counter() - start) * 1000
        return elapsed_ms

    def profile_turn(self, stage_latencies: dict[str, float]) -> LatencyProfile:
        """Creates a LatencyProfile from collected stage timings."""
        lp = LatencyProfile(stage_latencies=dict(stage_latencies))
        self._history.append(lp)
        if len(self._history) > 100:
            self._history = self._history[-100:]
        return lp

    @property
    def average_total_ms(self) -> float:
        if not self._history:
            return 0.0
        return sum(p.total_ms for p in self._history) / len(self._history)

    @property
    def last_profile(self) -> LatencyProfile | None:
        return self._history[-1] if self._history else None
