"""MemoryEventBus for publishing and subscribing to memory lifecycle events.

Two delivery modes:

- ``async_mode=False`` (default): ``publish`` dispatches handlers synchronously on
  the caller's thread. This is the historical behaviour and the mode used by
  tests, since it gives callers immediate, exception-isolated delivery.
- ``async_mode=True``: ``publish`` enqueues events and returns immediately; a
  daemon worker thread drains the queue. Handlers may perform blocking I/O
  (embedding API, FAISS writes) without stalling the mutating caller (e.g.
  ``add_fact``), decoupling a network hiccup from the conversation path.

In async mode delivery is at-least-once but unordered-across-subscribers; a
handler exception is logged and does not poison the worker or other events.
"""
from __future__ import annotations

import logging
import queue
import threading
from collections import defaultdict
from typing import Callable

from companion.memory.events.base import MemoryEvent

logger = logging.getLogger(__name__)


class MemoryEventBus:
    """In-memory event bus for memory lifecycle events.

    Synchronous by default; set ``async_mode=True`` to decouple handlers from
    the publishing call path.
    """

    _SHUTDOWN = object()  # sentinel that stops the worker

    def __init__(self, async_mode: bool = False) -> None:
        self._subscribers: dict[type[MemoryEvent], list[Callable[[MemoryEvent], None]]] = defaultdict(list)
        self._any_subscribers: list[Callable[[MemoryEvent], None]] = []
        self._async = async_mode
        self._sub_lock = threading.Lock()

        self._queue: queue.Queue[object] | None = None
        self._worker: threading.Thread | None = None
        self._accepting_events = True  # BUG-102 FIX: Track if we accept new events during shutdown
        if self._async:
            self._queue = queue.Queue()
            self._worker = threading.Thread(
                target=self._drain, name="memory-event-bus", daemon=True
            )
            self._worker.start()

    def subscribe(
        self,
        event_type: type[MemoryEvent] | None,
        handler: Callable[[MemoryEvent], None],
    ) -> None:
        """Subscribe a handler to a specific MemoryEvent subclass, or to all events if event_type is None."""
        with self._sub_lock:
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
        with self._sub_lock:
            if event_type is None:
                if handler in self._any_subscribers:
                    self._any_subscribers.remove(handler)
            else:
                if handler in self._subscribers.get(event_type, []):
                    self._subscribers[event_type].remove(handler)

    def _handlers_for(self, event: MemoryEvent) -> list[Callable[[MemoryEvent], None]]:
        # Snapshot under the sub-lock so publish is safe against concurrent
        # subscribe/unsubscribe while the bus is live in async mode.
        target_handlers: list[Callable[[MemoryEvent], None]] = []
        with self._sub_lock:
            for reg_type, handlers in self._subscribers.items():
                if isinstance(event, reg_type):
                    for h in handlers:
                        if h not in target_handlers:
                            target_handlers.append(h)
            for h in self._any_subscribers:
                if h not in target_handlers:
                    target_handlers.append(h)
        return target_handlers

    def _dispatch(self, event: MemoryEvent) -> None:
        for handler in self._handlers_for(event):
            try:
                handler(event)
            except Exception:
                logger.exception(
                    "Error executing event handler %s for event %s",
                    handler,
                    event,
                )

    def publish(self, event: MemoryEvent) -> None:
        """Publish an event to all matching subscribers and wildcard subscribers.
        
        BUG-102 FIX: Reject events during shutdown to prevent race conditions.
        """
        logger.debug("Publishing memory event: %s", event)
        if self._async:
            assert self._queue is not None
            # BUG-102 FIX: Don't accept new events during shutdown
            if not self._accepting_events:
                logger.warning("EventBus shutting down, rejecting event: %s", event)
                return
            self._queue.put(event)
            return
        self._dispatch(event)

    def _drain(self) -> None:
        assert self._queue is not None
        while True:
            event = self._queue.get()
            try:
                if event is self._SHUTDOWN:
                    return
                self._dispatch(event)
            finally:
                self._queue.task_done()

    def flush(self, timeout: float | None = 5.0) -> None:
        """Block until all queued events are processed (async mode only)."""
        if self._queue is not None:
            try:
                # Queue.join has no timeout; poll via a deadline loop.
                import time
                deadline = None if timeout is None else time.time() + timeout
                while self._queue.unfinished_tasks:
                    if deadline is not None and time.time() > deadline:
                        break
                    time.sleep(0.01)
            except Exception:
                pass

    def shutdown(self) -> None:
        """Stop the worker thread gracefully (async mode only).
        
        BUG-102 FIX: Set accepting_events=False before draining to prevent race conditions.
        """
        if self._worker is not None and self._queue is not None:
            # BUG-102 FIX: Stop accepting new events FIRST
            self._accepting_events = False
            logger.info("EventBus shutdown: stopping event acceptance, draining %d pending events", self._queue.qsize())
            
            # Process all remaining events before sending shutdown signal
            while not self._queue.empty():
                try:
                    event = self._queue.get_nowait()
                    if event is self._SHUTDOWN:
                        return
                    try:
                        self._dispatch(event)
                    finally:
                        self._queue.task_done()
                except queue.Empty:
                    break
            
            # Now send shutdown sentinel
            self._queue.put(self._SHUTDOWN)
            self._worker.join(timeout=2.0)
            self._worker = None
