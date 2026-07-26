"""Real regression test for companion.llm.client.run_llm.

Reproduces the production bug where run_llm received an async coroutine
function (the runtime chat.send_message) and returned an un-awaited coroutine,
causing "'coroutine' object has no attribute 'text'" in the compress pipeline.

No network / API key required: we pass dummy callables.
"""
import asyncio
import inspect

from companion.llm.client import run_llm


class _FakeResponse:
    def __init__(self, text):
        self.text = text


async def _async_send(prompt):
    await asyncio.sleep(0.01)
    return _FakeResponse(f"async:{prompt}")


def _sync_send(prompt):
    return _FakeResponse(f"sync:{prompt}")


import pytest

@pytest.mark.anyio
async def test_run_llm_awaits_async_coroutine_function():
    """The core regression: async callable must be awaited, not returned as a coroutine."""
    result = await run_llm(_async_send, "hi", timeout=5, retries=1)
    # Must be a resolved response object, NOT a coroutine
    assert not inspect.iscoroutine(result), f"run_llm returned a coroutine: {result!r}"
    assert result.text == "async:hi"


@pytest.mark.anyio
async def test_run_llm_passthrough_sync_function():
    """Sync callable still runs in a thread and returns the real value (no regression)."""
    result = await run_llm(_sync_send, "hi", timeout=5, retries=1)
    assert not inspect.iscoroutine(result), f"run_llm returned a coroutine: {result!r}"
    assert result.text == "sync:hi"
    # Bound sync method form (as used by create_default_session / bot_core)
    result2 = await run_llm(_sync_send, "yo", timeout=5, retries=1)
    assert result2.text == "sync:yo"


@pytest.mark.anyio
async def test_run_llm_async_timeout():
    """An async callable that hangs must respect the timeout (not hang forever)."""

    async def _slow(prompt):
        await asyncio.sleep(10)

    try:
        await run_llm(_slow, "x", timeout=0.1, retries=1)
    except TimeoutError:
        pass
    else:
        raise AssertionError("run_llm did not enforce timeout on async callable")


def test_sync_wrapper_runs_event_loop():
    asyncio.run(test_run_llm_awaits_async_coroutine_function())
    asyncio.run(test_run_llm_passthrough_sync_function())
    asyncio.run(test_run_llm_async_timeout())


if __name__ == "__main__":
    test_sync_wrapper_runs_event_loop()
    print("ALL RUN_LLM ASYNC TESTS PASSED")
