"""Tests for shadow evaluation."""
import pytest
from unittest.mock import patch

from companion.llm.shadow_eval import evaluate_identity_change

@pytest.mark.anyio
async def test_evaluate_identity_change_new():
    # If old value is empty, it should immediately return True
    is_valid = await evaluate_identity_change("core_identity", "", "New value")
    assert is_valid is True

@pytest.mark.anyio
async def test_evaluate_identity_change_valid():
    with patch("companion.llm.shadow_eval.aio_oneshot", return_value='{"is_valid": true}') as mock:
        is_valid = await evaluate_identity_change("core_identity", "Old", "New")
        assert is_valid is True
        mock.assert_called_once()

@pytest.mark.anyio
async def test_evaluate_identity_change_invalid():
    with patch("companion.llm.shadow_eval.aio_oneshot", return_value='{"is_valid": false, "reason": "Too dramatic"}'):
        is_valid = await evaluate_identity_change("core_identity", "Old", "New")
        assert is_valid is False

@pytest.mark.anyio
async def test_evaluate_identity_change_fallback():
    with patch("companion.llm.shadow_eval.aio_oneshot", side_effect=Exception("API Error")):
        is_valid = await evaluate_identity_change("core_identity", "Old", "New")
        # Should fallback to True on error
        assert is_valid is True
