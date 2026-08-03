import os
os.environ['API_TOKEN'] = 'test'
os.environ['GOOGLE_API_KEY'] = 'test_key'
os.environ['ADMIN_IDS'] = '12345'
os.environ['LLM_TIMEOUT'] = '5'
os.environ['LLM_RETRIES'] = '1'

import tempfile, threading, time
import companion.config as cfg

from companion.memory.store import MemoryStore
from companion.models import Fact
from companion.exceptions import ConcurrentModificationError

tmpdir = tempfile.mkdtemp()
cfg.DATA_DIR = tmpdir
cfg.SQLITE_PATH = os.path.join(tmpdir, 'test.db')

store = MemoryStore()
store.vector.embeddings_enabled = False

store.add_fact(Fact(
    id="f-occ-1", fact="Test fact for OCC archive",
    date="2026-08-02", importance=5, confidence=0.9,
    source="test", status="active",
))

# Simulate: thread A reads fact (version=1), thread B updates (version=2), 
# thread A tries to archive with stale version=1
f = store.get_fact("f-occ-1")
stale_version = f.version  # 1
print(f'Read stale version: {stale_version}')

# Thread B updates the fact
store.db.update_fact_fields("f-occ-1", {"importance": 8}, expected_version=1)
print(f'Updated fact to version 2')

# Manually simulate archive_fact with stale version
try:
    store.db.update_fact_fields("f-occ-1", {"status": "archived", "archived": 1}, expected_version=stale_version)
    print('Update succeeded (BAD - should have failed)')
except ConcurrentModificationError as e:
    print(f'ConcurrentModificationError raised (GOOD): {e}')

store.db.close()
