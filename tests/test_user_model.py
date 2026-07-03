"""Tests for user_model reflection."""
import pytest
from unittest.mock import patch, MagicMock

from companion.user_model import UserModel

@pytest.fixture
def mock_memory_store():
    with patch("companion.bot_core.memory_store") as mock:
        yield mock

@pytest.mark.anyio
async def test_reflect_after_interaction_drift_control(mock_memory_store, tmp_path):
    model = UserModel()
    # Provide old values to trigger checks
    model.data["identity"] = {
        "who_they_are": "Old core",
        "who_they_want_to_be": "Old ambition",
        "who_they_fear_becoming": "Old fear",
        "core_traits": ["Trait1"],
        "values": ["Value1"],
        "roles": ["Role1"]
    }

    # Simulate an LLM reflection that updates multiple fields
    reflection_json = '''{
        "identity_updates": {
            "who_they_are": "New core",
            "who_they_want_to_be": "New ambition",
            "who_they_fear_becoming": "New fear",
            "core_traits_to_add": ["Trait2"],
            "values_to_add": ["Value2"],
            "roles_to_add": ["Role2"]
        }
    }'''

    with patch("companion.llm.client.aio_oneshot", return_value=reflection_json):
        # Mock evaluate_identity_change to return True for scalar, False for list to see if it blocks correctly
        def mock_eval(category, old, new):
            # Block fears and roles to test drift control
            if category in ("fears", "roles"):
                return False
            return True
            
        with patch("companion.llm.shadow_eval.evaluate_identity_change", side_effect=mock_eval) as mock_shadow:
            reflection_res = await model.reflect_after_interaction("fake user msg", "fake bot response", [])
            
            # evaluate_identity_change should be called 6 times
            assert mock_shadow.call_count == 6
            
            # Check what was applied to the model
            ident = model.data["identity"]
            
            # Allowed fields should be updated
            assert ident["who_they_are"] == "New core"
            assert ident["who_they_want_to_be"] == "New ambition"
            assert "Trait2" in ident["core_traits"]
            assert "Value2" in ident["values"]
            
            # Blocked fields should remain unchanged
            assert ident["who_they_fear_becoming"] == "Old fear"
            assert "Role2" not in ident["roles"]
            
            # Reflection should contain the blocked message
            discoveries = reflection_res.get("discoveries", [])
            assert any("ShadowEvaluator blocked fears drift." in d for d in discoveries)
            assert any("ShadowEvaluator blocked roles drift." in d for d in discoveries)
            
            # Also check that memory_store.identity.update_identity was called with the right values
            # and explicitly with explicit_overwrite=True
            calls = mock_memory_store.identity.update_identity.call_args_list
            assert len(calls) == 6
            for call in calls:
                assert call.kwargs.get("explicit_overwrite") is True
