import os
os.environ.setdefault('API_TOKEN', 'test:token')
os.environ.setdefault('GOOGLE_API_KEY', 'test_key')
os.environ.setdefault('ADMIN_IDS', '12345')
os.environ.setdefault('LLM_TIMEOUT', '5')
os.environ.setdefault('LLM_RETRIES', '1')

import tempfile, sqlite3
import companion.config as cfg

tmpdir = tempfile.mkdtemp()
db_path = os.path.join(tmpdir, 'test.db')
cfg.SQLITE_PATH = db_path

from companion.storage.sqlite_db import MemoryDatabase
db = MemoryDatabase(db_path)
db._init_schema()

# Drop embeddings table
with db._conn() as conn:
    conn.execute("DROP TABLE IF EXISTS embeddings")

# Try the query directly
try:
    with db._conn() as conn:
        rows = conn.execute("""
            SELECT fact AS content, embedding, 'fact' AS content_type
            FROM facts
            WHERE embedding IS NOT NULL AND status IN ('active', 'dormant')
            UNION ALL
            SELECT content, embedding, content_type
            FROM embeddings
            WHERE content_type NOT IN ('query', 'fact')
        """).fetchall()
    print(f'Rows: {len(rows)}')
except Exception as e:
    print(f'Error: {type(e).__name__}: {e}')

db.close()
