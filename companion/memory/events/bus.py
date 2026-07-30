"""MemoryEventBus for publishing and subscribing to memory lifecycle events."""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Callable

from companion.memory.events.base import MemoryEvent

logger = logging.getLogger(__name__)


class MemoryEventBus:
    """Synchronous in-memory event bus for memory lifecycle events."""

    def __init__(self) -> None:
        self._subscribers: dict[type[MemoryEvent], list[Callable[[MemoryEvent], None]]] = defaultdict(list)
        self._any_subscribers: list[Callable[[MemoryEvent], None]] = []

    def subscribe(
        self,
        event_type: type[MemoryEvent] | None,
        handler: Callable[[MemoryEvent], None],
    ) -> None:
        """Subscribe a handler to a specific MemoryEvent subclass, or to all events if event_type is None."""
        if event_type is None:
            if handler not in self._any_subscribers:
                self._any_subscribers.append(handler)
        else:
            if handler not in self._subscribers[event_type]:
                self._subscribers[event_type].append(handler)
        logger.debug("Subscribed %s to event_type %s", handler, event_type)

    def unsubscribe(
        self,
        event_type: type[MemoryEvent] | None,
        handler: Callable[[MemoryEvent], None],
    ) -> None:
        """Unsubscribe a handler from a specific MemoryEvent subclass, or from wildcard if event_type is None."""
        if event_type is None:
            if handler in self._any_subscribers:
                self._any_subscribers.remove(handler)
        else:
            if handler in self._subscribers.get(event_type, []):
                self._subscribers[event_type].remove(handler)

    def publish(self, event: MemoryEvent) -> None:
        """Publish an event to all matching subscribers and wildcard subscribers."""
        logger.debug("Publishing memory event: %s", event)

        # Direct type matching and subclass matching
        target_handlers: list[Callable[[MemoryEvent], None]] = []
        for reg_type, handlers in self._subscribers.items():
            if isinstance(event, reg_type):
                for h in handlers:
                    if h not in target_handlers:
                        target_handlers.append(h)

        for h in self._any_subscribers:
            if h not in target_handlers:
                target_handlers.append(h)

        for handler in target_handlers:
            try:
                handler(event)
            except Exception:
                logger.exception(
                    "Error executing event handler %s for event %s",
                    handler,
                    event,
                )
