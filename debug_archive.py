import os
os.environ['API_TOKEN'] = 'test'
os.environ['GOOGLE_API_KEY'] = 'test_key'
os.environ['ADMIN_IDS'] = '12345'
os.environ['LLM_TIMEOUT'] = '5'
os.environ['LLM_RETRIES'] = '1'

import tempfile
import companion.config as cfg

from companion.memory.store import MemoryStore
from companion.models import Fact

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

# Check version after insert
f = store.get_fact("f-occ-1")
print(f'After add_fact: version={f.version}, status={f.status}')

# Simulate another thread updating the fact
store.db.update_fact_fields("f-occ-1", {"importance": 8}, expected_version=1)

# Check version after update
f2 = store.get_fact("f-occ-1")
print(f'After update: version={f2.version}, importance={f2.importance}')

# Now try archive_fact - old.version should be 2, but DB version is also 2
# So OCC should succeed! The stale read is the issue.
try:
    result = store.archive_fact("f-occ-1", reason="test")
    print(f'archive_fact succeeded: {result}')
    f3 = store.get_fact("f-occ-1")
    print(f'After archive: version={f3.version}, status={f3.status}')
except Exception as e:
    print(f'Error: {type(e).__name__}: {e}')

store.db.close()
