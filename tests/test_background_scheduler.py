"""Tests for background_scheduler — circuit breaker logic."""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from companion.background_scheduler import (
    _check_circuit_breaker,
    _record_failure,
    _record_success,
    _background_task_failures,
    _background_task_cooldown_until,
)


@pytest.fixture(autouse=True)
def reset_state():
    """Reset circuit breaker state before each test."""
    _background_task_failures.clear()
    _background_task_cooldown_until.clear()
    yield


class TestCircuitBreaker:
    def test_initial_state_allows_execution(self):
        assert _check_circuit_breaker("test_task") is True

    def test_after_few_failures_still_allows(self):
        for _ in range(3):
            _record_failure("test_task")
        assert _check_circuit_breaker("test_task") is True
        assert _background_task_failures["test_task"] == 3

    def test_after_max_failures_triggers_cooldown(self):
        for _ in range(5):
            _record_failure("test_task")
        assert _check_circuit_breaker("test_task") is False
        assert "test_task" in _background_task_cooldown_until

    def test_success_resets_failure_count(self):
        _record_failure("test_task")
        _record_failure("test_task")
        _record_success("test_task")
        assert _background_task_failures["test_task"] == 0

    def test_after_cooldown_expires_allows_again(self):
        for _ in range(5):
            _record_failure("test_task")
        assert _check_circuit_breaker("test_task") is False
        _background_task_cooldown_until["test_task"] = time.time() - 1
        assert _check_circuit_breaker("test_task") is True

    def test_different_tasks_independent(self):
        for _ in range(5):
            _record_failure("failing_task")
        _record_success("healthy_task")
        assert _check_circuit_breaker("failing_task") is False
        assert _check_circuit_breaker("healthy_task") is True

    def test_exactly_at_max_failures_triggers(self):
        for _ in range(4):
            _record_failure("test_task")
        assert _check_circuit_breaker("test_task") is True
        _record_failure("test_task")
        assert _check_circuit_breaker("test_task") is False
