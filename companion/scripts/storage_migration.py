import os
import json
import sys

# Добавляем корневую директорию проекта в sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from companion.config import BASE_DIR, DATA_DIR
from companion.storage.sqlite_db import MemoryDatabase

def migrate_data():
    db = MemoryDatabase()
    report = ["# Migration Report\n"]
    
    # 1. State Models (user, self, world)
    for model_name, filename in [("user", "user_model.json"), ("self", "self_model.json"), ("world", "world_model.json")]:
        path = os.path.join(DATA_DIR, filename)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                db.save_state_model(model_name, data)
                report.append(f"- ✅ Migrated {filename} into state_models ('{model_name}')")
            except Exception as e:
                report.append(f"- ❌ Error migrating {filename}: {e}")
        else:
            report.append(f"- ⏭️ Skipped {filename} (not found)")

    # 2. System Meta (personality, master_summary)
    pers_path = os.path.join(DATA_DIR, "personality.json")
    if os.path.exists(pers_path):
        try:
            with open(pers_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            db.set_meta("personality", json.dumps(data, ensure_ascii=False))
            report.append("- ✅ Migrated personality.json into system_meta")
        except Exception as e:
            report.append(f"- ❌ Error migrating personality.json: {e}")
    else:
        report.append("- ⏭️ Skipped personality.json (not found)")

    master_path = os.path.join(BASE_DIR, "master_summary.txt")
    if os.path.exists(master_path):
        try:
            with open(master_path, "r", encoding="utf-8") as f:
                data = f.read().strip()
            db.set_meta("master_summary", data)
            report.append("- ✅ Migrated master_summary.txt into system_meta")
        except Exception as e:
            report.append(f"- ❌ Error migrating master_summary.txt: {e}")
    else:
        report.append("- ⏭️ Skipped master_summary.txt (not found)")

    # 3. FAISS Mapping
    faiss_path = os.path.join(DATA_DIR, "faiss_mapping.json")
    if os.path.exists(faiss_path):
        try:
            with open(faiss_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            db.save_state_model("faiss_mapping", data)
            report.append("- ✅ Migrated faiss_mapping.json into state_models ('faiss_mapping')")
        except Exception as e:
            report.append(f"- ❌ Error migrating faiss_mapping.json: {e}")
    else:
        report.append("- ⏭️ Skipped faiss_mapping.json (not found)")

    # 4. Shared Lore Candidates
    lore_path = os.path.join(DATA_DIR, "shared_lore_candidates.jsonl")
    if os.path.exists(lore_path):
        try:
            count = 0
            with open(lore_path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip(): continue
                    cand = json.loads(line)
                    # Use execute directly or through db methods. Our db doesn't have add_shared_lore_candidate exposed yet?
                    # Let's check if the db table is there. 
                    with db._conn() as conn:
                        conn.execute(
                            "INSERT INTO shared_lore_candidates (candidate_phrase, context, status) VALUES (?, ?, ?)",
                            (cand.get("phrase", ""), cand.get("context", ""), cand.get("status", "pending"))
                        )
                    count += 1
            report.append(f"- ✅ Migrated {count} rows from shared_lore_candidates.jsonl")
        except Exception as e:
            report.append(f"- ❌ Error migrating shared_lore_candidates.jsonl: {e}")
    else:
        report.append("- ⏭️ Skipped shared_lore_candidates.jsonl (not found)")

    # 5. Move Logs
    logs_dir = os.path.join(DATA_DIR, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    
    log_moves = [
        ("policy_decisions.jsonl", "policy.jsonl"),
        ("self_errors.jsonl", "errors.jsonl"),
        ("user_model_updates.jsonl", "updates.jsonl")
    ]
    
    for old_name, new_name in log_moves:
        old_path = os.path.join(DATA_DIR, old_name)
        new_path = os.path.join(logs_dir, new_name)
        if os.path.exists(old_path):
            try:
                # We can append contents if the new file already exists, or just move it if it doesn't.
                if os.path.exists(new_path):
                    with open(old_path, 'r', encoding='utf-8') as src:
                        with open(new_path, 'a', encoding='utf-8') as dst:
                            dst.write(src.read())
                    os.remove(old_path)
                else:
                    os.rename(old_path, new_path)
                report.append(f"- ✅ Moved {old_name} to logs/{new_name}")
            except Exception as e:
                report.append(f"- ❌ Error moving {old_name}: {e}")
        else:
            report.append(f"- ⏭️ Skipped {old_name} (not found)")

    report_text = "\n".join(report)
    report_path = os.path.join(BASE_DIR, "migration_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text)
    
    print(report_text)

if __name__ == "__main__":
    migrate_data()
