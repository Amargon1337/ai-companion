import json

discoveries = 0
confirmations = 0
falsifications = 0
total_updates = 0

try:
    with open(r"c:\Games\data\user_model_updates.jsonl", "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip(): continue
            try:
                data = json.loads(line)
                discoveries += len(data.get("discoveries", []))
                confirmations += len(data.get("confirmations", []))
                falsifications += len(data.get("falsifications", []))
                total_updates += 1
            except json.JSONDecodeError:
                pass

    print(f"Total Updates: {total_updates}")
    print(f"Discoveries: {discoveries}")
    print(f"Confirmations: {confirmations}")
    print(f"Falsifications: {falsifications}")
except Exception as e:
    print(e)
