import sqlite3
import os

db_path = r'C:\Games\companion\memory.db'
if not os.path.exists(db_path):
    print('DB not found')
    exit()

conn = sqlite3.connect(db_path)
c = conn.cursor()

try:
    c.execute("SELECT value FROM meta WHERE key = 'legacy_profile'")
    res = c.fetchone()
    legacy_profile = res[0] if res else ''
except:
    legacy_profile = ''

try:
    c.execute("SELECT text FROM messages WHERE importance >= 5 ORDER BY timestamp DESC LIMIT 30")
    msgs = [row[0] for row in c.fetchall()]
    history_text = '\n'.join(msgs)
except:
    history_text = ''

try:
    c.execute("SELECT content FROM facts WHERE status = 'active'")
    facts = '\n'.join([row[0] for row in c.fetchall()])
except:
    facts = ''

try:
    c.execute("SELECT value FROM meta WHERE key = 'master_summary'")
    res = c.fetchone()
    master_summary = res[0] if res else ''
except:
    master_summary = ''

total_chars = len(legacy_profile) + len(history_text) + len(facts) + len(master_summary)
tokens_approx = total_chars / 3.5

print(f'Legacy profile chars: {len(legacy_profile)}')
print(f'History chars (30 msgs): {len(history_text)}')
print(f'Facts chars: {len(facts)}')
print(f'Master summary chars: {len(master_summary)}')
print(f'Total approx chars: {total_chars}')
print(f'Total approx tokens (roughly): {int(tokens_approx)}')
