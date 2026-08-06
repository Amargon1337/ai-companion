"""Projection Rebuilder for Phase C1.6 Replay Verification Layer."""
from __future__ import annotations

import logging
from typing import Any

from companion.memory.event_store import EventStore, MemoryEventType

logger = logging.getLogger(__name__)


class ProjectionRebuilder:
    """Reconstructs expected projection state from Event Store without mutating production tables."""

    def __init__(self, event_store: EventStore) -> None:
        self.event_store = event_store

    def build_snapshot(self) -> dict[str, dict[str, Any]]:
        """Reconstructs expected state of all aggregates from events.

        Does not mutate production tables.
        Returns a dictionary mapping aggregate_id -> replayed state dictionary.
        """
        events = self.event_store.get_all_events()
        snapshot: dict[str, dict[str, Any]] = {}

        for event in events:
            agg_id = event.aggregate_id
            if agg_id not in snapshot:
                snapshot[agg_id] = {}
            snapshot[agg_id] = self.event_store._apply_event(snapshot[agg_id], event)

        result: dict[str, dict[str, Any]] = {}
        for agg_id, state in snapshot.items():
            if "fact" in state or "id" in state:
                # Normalize enum attributes to standard strings for comparison
                if "origin" in state and hasattr(state["origin"], "value"):
                    state["origin"] = str(state["origin"].value)
                elif "origin" in state:
                    state["origin"] = str(state["origin"])
                
                if "identity_layer" in state and hasattr(state["identity_layer"], "value"):
                    state["identity_layer"] = str(state["identity_layer"].value)
                elif "identity_layer" in state:
                    state["identity_layer"] = str(state["identity_layer"])

                state.setdefault("status", "active")
                state.setdefault("importance", 5)
                state.setdefault("facts_sent_count", 0)
                state.setdefault("facts_used_count", 0)
                result[agg_id] = state

        return result
