"""Tests for the deterministic user-model evolution report."""
from __future__ import annotations

from datetime import datetime, timedelta

from companion.user_model import UserModel


def test_evolution_report_aggregates_recent_changes_and_emotions():
    model = UserModel()
    now = datetime.now()
    model.data["total_interactions"] = 42
    model.data["beliefs"] = {"one": {"text": "test"}, "two": {"text": "test"}}
    model.data["changes"] = ["появилcя интереc к AI", "важнее cтала музыка"]
    model.data["interests"] = {"AI": 8, "QA": 2}
    model.data["emotional_timeline"]["state_history"] = [
        {"timestamp": (now - timedelta(days=10)).isoformat(), "state": "anxious", "mood": {"energy": 0.4}},
        {"timestamp": now.isoformat(), "state": "energized", "mood": {"energy": 0.8}},
    ]

    report = model.evolution_report(8)

    assert report["interactions"] == 42
    assert report["new_beliefs"] == 2
    assert report["dominant_state"] in {"anxious", "energized"}
    assert report["previous_state"] == "anxious"
    assert report["interests"]["AI"] == 8
