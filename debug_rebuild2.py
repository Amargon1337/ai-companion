import os
os.environ.setdefault('API_TOKEN', 'test:token')
os.environ.setdefault('GOOGLE_API_KEY', 'test_key')
os.environ.setdefault('ADMIN_IDS', '12345')
os.environ.setdefault('LLM_TIMEOUT', '5')
os.environ.setdefault('LLM_RETRIES', '1')

import tempfile
import companion.config as cfg
from unittest.mock import patch

from companion.memory.store import MemoryStore
from companion.models import Fact

tmpdir = tempfile.mkdtemp()
cfg.DATA_DIR = tmpdir
cfg.SQLITE_PATH = os.path.join(tmpdir, 'test_rebuild2.db')

store = MemoryStore()
store.vector.embeddings_enabled = True

with patch("companion.memory.vector_index._embed_texts") as mock_embed:
    mock_embed.return_value = [[0.1] * 768]
    store.add_fact(Fact(
        id="f-emb-1", fact="Test embedding fact", date="2026-08-02",
        importance=5, confidence=0.9, source="test", status="active",
    ))

# Drop the embeddings table to simulate corruption
with store.db._conn() as conn:
    conn.execute("DROP TABLE IF EXISTS embeddings")
    conn.execute("DROP TABLE IF EXISTS embeddings_fts")
    conn.execute("DROP TABLE IF EXISTS embeddings_fts_data")
    conn.execute("DROP TABLE IF EXISTS embeddings_fts_idx")
    conn.execute("DROP TABLE IF EXISTS embeddings_fts_content")
    conn.execute("DROP TABLE IF EXISTS embeddings_fts_docsize")
    conn.execute("DROP TABLE IF EXISTS embeddings_fts_config")

store.db.set_meta("faiss_index_dirty", "1")

# Create new store
try:
    store2 = MemoryStore()
    store2.vector.embeddings_enabled = True
    print(f'hash_to_id count: {len(store2.vector.hash_to_id)}')
    print(f'hash_to_id: {store2.vector.hash_to_id}')
except Exception as e:
    print(f'Error: {type(e).__name__}: {e}')

store.db.close()
