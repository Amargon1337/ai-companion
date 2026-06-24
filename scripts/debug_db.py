"""Debug database schema."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("API_TOKEN", "test:token")
os.environ.setdefault("GOOGLE_API_KEY", "test_key")
os.environ.setdefault("ADMIN_IDS", "12345")
os.environ.setdefault("LLM_TIMEOUT", "5")
os.environ.setdefault("LLM_RETRIES", "1")

import companion.config as cfg
import sqlite3

tmp = tempfile.mkdtemp()
cfg.DATA_DIR = tmp
cfg.SQLITE_PATH = os.path.join(tmp, "companion.db")
print(f"DB path: {cfg.SQLITE_PATH}")

# Init MemoryDatabase
from companion.storage.sqlite_db import MemoryDatabase
db = MemoryDatabase()

conn = sqlite3.connect(cfg.SQLITE_PATH)
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print("Tables after MemoryDatabase:", [t[0] for t in tables])
for t in tables:
    cols = conn.execute(f"PRAGMA table_info({t[0]})").fetchall()
    print(f"  {t[0]}: columns={[c[1] for c in cols]}")
conn.close()

# Init VectorIndex
from companion.memory.vector_index import VectorIndex
vi = VectorIndex()

conn = sqlite3.connect(cfg.SQLITE_PATH)
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print("Tables after VectorIndex:", [t[0] for t in tables])
for t in tables:
    cols = conn.execute(f"PRAGMA table_info({t[0]})").fetchall()
    print(f"  {t[0]}: columns={[c[1] for c in cols]}")
conn.close()

# Now memory_store
from companion.memory.store import MemoryStore
store = MemoryStore()

from companion.models import Fact
f = Fact(fact="test", date="2026-06-01", importance=5, confidence=0.8, source="test", source_type="test")
try:
    store.add_fact(f)
    print("add_fact SUCCESS")
except Exception as e:
    import traceback
    traceback.print_exc()
