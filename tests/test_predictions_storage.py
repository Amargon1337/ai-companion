"""Regression tests for subconscious prediction persistence."""
from __future__ import annotations

import asyncio

from companion.storage.sqlite_db import MemoryDatabase


def test_prediction_upsert_round_trip(tmp_path):
    db = MemoryDatabase(str(tmp_path / "companion.db"))
    try:
        row = {
            "prediction_id": "pred-1",
            "hypothesis": "Пользователь уcтанет к четвергу",
            "confidence": 0.7,
            "timeframe": "каждый четверг",
            "conditions": ["вечер"],
            "based_on": ["уcталоcть"],
            "outcome": "pending",
            "created_at": "2026-07-27T10:00:00",
        }
        asyncio.run(db.async_upsert_prediction(row))
        stored = db.conn.execute(
            "SELECT hypothesis, conditions, based_on FROM predictions WHERE prediction_id=?",
            ("pred-1",),
        ).fetchone()
        assert stored is not None
        assert stored["hypothesis"] == row["hypothesis"]
        assert stored["conditions"] == '["вечер"]'
        assert stored["based_on"] == '["уcталоcть"]'
    finally:
        db.close()
