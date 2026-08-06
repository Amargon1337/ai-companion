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

    def __init__(self, async_mode: bool = False, journal_db: Any = None) -> None:
        self._subscribers: dict[type[MemoryEvent], list[Callable[[MemoryEvent], None]]] = defaultdict(list)
        self._any_subscribers: list[Callable[[MemoryEvent], None]] = []
        self._async = async_mode
        self._sub_lock = threading.Lock()
        # Phase B: durable event journal. When provided, every publish is
        # appended BEFORE side effects run and marked applied AFTER they
        # succeed; replay_pending() re-runs anything left unapplied (crash
        # between SQLite commit and FAISS drain).
        self._journal_db = journal_db
        self._journal_events = getattr(journal_db, "insert_event_journal", None)

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

    def _journal(self, event: MemoryEvent) -> int | None:
        """Persist the event BEFORE side effects; returns journal id or None."""
        if self._journal_events is None:
            return None
        try:
            from companion.memory.events.base import event_to_journal
            etype, payload = event_to_journal(event)
            return self._journal_events(etype, payload)
        except Exception:
            # Journaling must never break the mutation path.
            logger.exception("Event journal append failed for %s", event)
            return None

    def _mark_applied(self, journal_id: int | None) -> None:
        if journal_id is None or self._journal_db is None:
            return
        try:
            self._journal_db.mark_event_journal_applied(journal_id)
        except Exception:
            logger.exception("Event journal mark-applied failed for journal_id=%s", journal_id)

    def publish(self, event: MemoryEvent, *, journal: bool = True) -> None:
        """Publish an event to all matching subscribers and wildcard subscribers.

        In async mode the event is journaled (durable) before it is queued.
        ``journal=False`` is used by replay: the event is already in the
        journal, so appending it again would create a duplicate.
        """
        logger.debug("Publishing memory event: %s", event)
        if self._async:
            assert self._queue is not None
            # BUG-102 FIX: Don't accept new events during shutdown
            if not self._accepting_events:
                logger.warning("EventBus shutting down, rejecting event: %s", event)
                return
            journal_id = self._journal(event) if journal else None
            self._queue.put((journal_id, event))
            return
        self._dispatch(event)
        if journal:
            jid = self._journal(event)
            self._mark_applied(jid)

    def _drain(self) -> None:
        assert self._queue is not None
        while True:
            item = self._queue.get()
            try:
                if item is self._SHUTDOWN:
                    return
                journal_id, event = item
                self._dispatch(event)
                self._mark_applied(journal_id)
            finally:
                self._queue.task_done()

    def replay_pending(self) -> int:
        """Re-queue journaled events that were never marked applied.

        Called at startup: bridges the crash window between SQLite commit and
        FAISS drain. Idempotent handlers make replay safe.
        """
        if self._journal_db is None or not self._async:
            return 0
        from companion.memory.events.base import event_from_journal
        replayed = 0
        for row in self._journal_db.list_pending_event_journal():
            event = event_from_journal(str(row.get("event_type", "")),
                                       str(row.get("payload", "") or "{}"))
            if event is None:
                # Unknown/undecodable event: mark applied so it never blocks
                # the replay tail (can't do anything with it).
                self._mark_applied(int(row["id"]))
                continue
            try:
                self._queue.put((int(row["id"]), event))
                replayed += 1
            except Exception:
                logger.exception("Replay enqueue failed for journal id %s", row.get("id"))
        if replayed:
            logger.info("Event journal replay: re-queued %d pending event(s)", replayed)
        return replayed

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
                    item = self._queue.get_nowait()
                    if item is self._SHUTDOWN:
                        return
                    journal_id, event = item
                    try:
                        self._dispatch(event)
                        self._mark_applied(journal_id)
                    finally:
                        self._queue.task_done()
                except queue.Empty:
                    break
            
            # Now send shutdown sentinel
            self._queue.put(self._SHUTDOWN)
            self._worker.join(timeout=2.0)
            self._worker = None
