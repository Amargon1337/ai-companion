"""Debug pipeline test failure."""
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_TOKEN", "test:token")
os.environ.setdefault("GOOGLE_API_KEY", "test_key")
os.environ.setdefault("ADMIN_IDS", "12345")
os.environ.setdefault("LLM_TIMEOUT", "5")
os.environ.setdefault("LLM_RETRIES", "1")

import companion.config as cfg
tmp = tempfile.mkdtemp()
cfg.DATA_DIR = tmp
cfg.SQLITE_PATH = tmp + "/companion.db"

from companion.memory.store import MemoryStore
from companion.llm.pipeline import run_compress_pipeline

store = MemoryStore()
chat = MagicMock()
response = MagicMock()
response.text = "Test summary response."
chat.send_message.return_value = response

_LLM_ONESHOT_RETURN = '[{"fact": "test fact", "importance": 5, "confidence": 0.8, "tags": [], "memory_kind": "event"}]'
_LLM_PARSE_RETURN = [{"fact": "test fact", "importance": 5, "confidence": 0.8, "tags": [], "memory_kind": "event"}]
_LLM_PARSE_OBJ_RETURN = {"interests": {}}

with patch("companion.llm.client.parse_json_object", return_value=_LLM_PARSE_OBJ_RETURN), \
     patch("companion.llm.client.parse_json_array", return_value=_LLM_PARSE_RETURN), \
     patch("companion.llm.client.oneshot", return_value=_LLM_ONESHOT_RETURN):
    try:
        result = run_compress_pipeline(store, chat, 12345)
        print(f"Result: {result}")
    except Exception as e:
        import traceback
        traceback.print_exc()
