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

# Verify it's gone
with store.db._conn() as conn:
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='embeddings'").fetchall()
    print(f'embeddings table exists: {len(tables) > 0}')

# Check what tables exist
with store.db._conn() as conn:
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    print(f'All tables: {[t[0] for t in tables]}')

# Mark dirty
store.db.set_meta("faiss_index_dirty", "1")

# Create new store - debug _rebuild_index
store2 = MemoryStore()
store2.vector.embeddings_enabled = True

# Check VectorIndex state
print(f'store2.vector.hash_to_id: {store2.vector.hash_to_id}')
print(f'store2.vector._is_initialized: {store2.vector._is_initialized}')

# Check DB state of store2
with store2.db._conn() as conn:
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    print(f'store2 tables: {[t[0] for t in tables]}')
    
    # Try the rebuild query
    try:
        rows = conn.execute("""
            SELECT fact AS content, embedding, 'fact' AS content_type
            FROM facts
            WHERE embedding IS NOT NULL AND status IN ('active', 'dormant')
            UNION ALL
            SELECT content, embedding, content_type
            FROM embeddings
            WHERE content_type NOT IN ('query', 'fact')
        """).fetchall()
        print(f'Rebuild query rows: {len(rows)}')
        for r in rows:
            print(f'  content={r[0][:50] if r[0] else None}, emb={r[1] is not None}')
    except Exception as e:
        print(f'Rebuild query error: {e}')

store.db.close()
store2.db.close()
