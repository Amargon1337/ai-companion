"""Durable SQLite-backed background worker for critical companion work."""
from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Any

logger = logging.getLogger(__name__)
JobHandler = Callable[[dict[str, Any]], Awaitable[None]]


class DurableJobWorker:
    def __init__(self, db: Any, worker_id: str, *, poll_seconds: float = 1.0) -> None:
        self.db = db
        self.worker_id = worker_id
        self.poll_seconds = poll_seconds
        self.handlers: dict[str, JobHandler] = {}
        self._task: asyncio.Task | None = None
        self._running = False

    def register(self, job_type: str, handler: JobHandler) -> None:
        self.handlers[job_type] = handler

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        stale_before = (datetime.now() - timedelta(minutes=10)).isoformat()
        await asyncio.to_thread(self.db.recover_stale_jobs, stale_before)
        self._task = asyncio.create_task(self._loop(), name=f"durable-jobs:{self.worker_id}")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while self._running:
            job = await asyncio.to_thread(self.db.claim_due_job, self.worker_id)
            if job is None:
                await asyncio.sleep(self.poll_seconds)
                continue
            await self._run_one(job)

    async def _run_one(self, job: dict[str, Any]) -> None:
        handler = self.handlers.get(str(job["job_type"]))
        if handler is None:
            await asyncio.to_thread(
                self.db.complete_job, job["job_id"], job["attempt_id"],
                error=f"unknown durable job type: {job['job_type']}", permanent=True,
            )
            return
        try:
            await handler(job)
        except asyncio.CancelledError:
            raise
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            await asyncio.to_thread(self.db.complete_job, job["job_id"], job["attempt_id"], error=str(exc), permanent=True)
        except Exception as exc:
            # Retry with bounded exponential backoff and jitter. The claim is
            # durable, so a crash after this point is recovered by stale locks.
            attempts = int(job.get("attempt_count", 1))
            delay = min(3600.0, (2 ** min(attempts, 10)) + random.uniform(0, 1))
            retry_at = (datetime.now() + timedelta(seconds=delay)).isoformat()
            await asyncio.to_thread(self.db.complete_job, job["job_id"], job["attempt_id"], error=str(exc), retry_at=retry_at)
        else:
            await asyncio.to_thread(self.db.complete_job, job["job_id"], job["attempt_id"])
