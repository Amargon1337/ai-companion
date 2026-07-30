"""Unit tests for MemoryEventBus and event publishing."""
from __future__ import annotations

from companion.memory.events import (
    FactArchivedEvent,
    FactCreatedEvent,
    FactUpdatedEvent,
    MemoryEvent,
    MemoryEventBus,
)


def test_memory_event_bus_subscribe_and_publish() -> None:
    bus = MemoryEventBus()
    received_events: list[MemoryEvent] = []
    wildcard_events: list[MemoryEvent] = []

    def on_archived(ev: MemoryEvent) -> None:
        received_events.append(ev)

    def on_any(ev: MemoryEvent) -> None:
        wildcard_events.append(ev)

    bus.subscribe(FactArchivedEvent, on_archived)
    bus.subscribe(None, on_any)

    e1 = FactCreatedEvent(fact_id="f1", fact_text="hello")
    e2 = FactArchivedEvent(fact_id="f1", fact_text="hello", reason="stale")

    bus.publish(e1)
    bus.publish(e2)

    assert len(received_events) == 1
    assert isinstance(received_events[0], FactArchivedEvent)
    assert received_events[0].fact_id == "f1"

    assert len(wildcard_events) == 2
    assert wildcard_events[0] == e1
    assert wildcard_events[1] == e2


def test_memory_event_bus_unsubscribe() -> None:
    bus = MemoryEventBus()
    count = 0

    def handler(ev: MemoryEvent) -> None:
        nonlocal count
        count += 1

    bus.subscribe(FactUpdatedEvent, handler)
    bus.publish(FactUpdatedEvent(fact_id="f2"))
    assert count == 1

    bus.unsubscribe(FactUpdatedEvent, handler)
    bus.publish(FactUpdatedEvent(fact_id="f2"))
    assert count == 1
