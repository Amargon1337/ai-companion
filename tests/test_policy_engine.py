import pytest
from companion.llm.sessions import build_system_instruction
from companion.user_model import UserModel
from companion.memory.store import MemoryStore
from companion.memory.retrieval import RetrievalBudgetManager
from unittest.mock import patch, MagicMock

@pytest.fixture
def mock_memory_store():
    store = MagicMock(spec=MemoryStore)
    store.build_personality_snapshot_text.return_value = "Fake Snapshot"
    store.load_master_summary.return_value = "Fake Master Summary"
    store.list_facts.return_value = []
    store.list_reflections.return_value = []
    store.load_recent_summaries.return_value = []
    store.build_canonical_profile_text.return_value = "Fake Snapshot"
    store.db = MagicMock()
    store.db.list_permanent_notes.return_value = ["Notes"]
    store.db.get_meta.return_value = "Ivan DB"
    store.identity = MagicMock()
    store.identity.to_prompt_block.return_value = "Identity Block"
    return store

@pytest.fixture
def mock_retrieval_mgr():
    mgr = MagicMock(spec=RetrievalBudgetManager)
    bundle = MagicMock()
    bundle.to_prompt_block.return_value = "Retrieval Bundle Context"
    mgr.select.return_value = bundle
    return mgr

@patch("companion.llm.sessions.reasoning_engine")
def test_build_system_instruction_depressed_state(mock_reasoning, mock_memory_store, mock_retrieval_mgr):
    # Set the state to depressed
    from companion.user_model import user_model
    user_model.data["emotional_timeline"]["baseline_state"] = "depressed"
    
    # Invalidate cache if it existed
    from companion.llm.sessions import _PROMPT_CACHE
    _PROMPT_CACHE.clear()

    # Build prompt
    prompt = build_system_instruction(mock_memory_store, mock_retrieval_mgr, "test query")
    
    # 1. Assert memory block logic is present
    assert "Retrieval Bundle Context" in prompt
    assert "Fake Master Summary" in prompt
    assert "Ivan DB" in prompt
    
    # 2. Assert strategy and tone are depressed
    assert "мягкий и бережный тон" in prompt
    assert "НИКАКОГО сарказма" in prompt
    assert "валидируй эмоции" in prompt
    assert "НИКАКИХ непрошеных советов" in prompt
    
    # 3. Assert NO neutral or energized tones leaked
    assert "постирония" not in prompt
    assert "дерзкий" not in prompt
    
@patch("companion.llm.sessions.reasoning_engine")
def test_build_system_instruction_fallback_state(mock_reasoning, mock_memory_store, mock_retrieval_mgr):
    # Set the state to an invalid one
    from companion.user_model import user_model
    user_model.data["emotional_timeline"]["baseline_state"] = "super_burnt_out_and_tired"
    
    # Invalidate cache if it existed
    from companion.llm.sessions import _PROMPT_CACHE
    _PROMPT_CACHE.clear()

    # Build prompt
    prompt = build_system_instruction(mock_memory_store, mock_retrieval_mgr, "test query")
    
    # Should fallback to neutral
    assert "постирония" in prompt
    assert "развивай мысль пользователя" in prompt
    assert "НИКАКОГО сарказма" not in prompt
