from companion.memory.store import MemoryStore
store = MemoryStore()

# Search for the fact mentioning "2 ГБ" or "2 гб" or "2gb" or "2 gb"
facts = store.list_facts(status="active")
found = []
for f in facts:
    if "2 гб" in f.text.lower() or "интернет" in f.text.lower() or "2 gb" in f.text.lower() or "трафик" in f.text.lower():
        found.append(f)

for f in found:
    print(f"ID: {f.id} | Text: {f.text}")
