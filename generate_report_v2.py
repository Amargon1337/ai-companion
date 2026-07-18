def generate_report():
    md = """# PROJECT ARCHITECTURE & DEAD CODE AUDIT (REVISED)
**Role**: Senior Python Architect & Code Auditor  
**Goal**: Identify dead code, unused files, incomplete functions, legacy architecture, and technical debt.  
**Context**: Objective, evidence-based audit without premature architectural judgments.

---

## 1. DEPENDENCY MAP & FILE USAGE
Based on static analysis and deep manual grep-verification across the `companion/` directory.

| File | Status | Imports/Used By | Removable? |
| :--- | :--- | :--- | :--- |
| `bot_core.py` | Active | Main entrypoint | No |
| `main.py` | Active | Runner | No |
| `config.py` | Active | All files | No |
| `policy_layer.py` | Active | `bot_core.py` | No |
| `reasoning.py` | Active | `bot_core.py` | **Has dead subsystems** |
| `self_model.py` | Active | `bot_core.py` | No |
| `user_model.py` | Active | `bot_core.py` | No |
| `critique_manager.py` | Active | `bot_core.py` | No |
| `llm/client.py` | Active | `pipeline.py`, etc | No (Dataclasses are actively used in LLM outputs) |
| `llm/pipeline.py` | Active | `bot_core.py` | No |
| `llm/shadow_eval.py`| Suspicious | `user_model.py` | Needs check |
| `memory/store.py` | Active | Multiple | No |
| `memory/vector_index.py` | Active | `store.py` | No |
| `memory/retrieval.py` | Active | `bot_core.py` | No |
| `storage/jsonl.py` | **Legacy/Write-Only**| Multiple | **Yes (Write-only bloat)** |
| `storage/sqlite_db.py`| Active | Multiple | **Has dead schema parts** |

---

## 2. ARCHITECTURE REMNANTS (FAISS, JSONL, RAG, etc.)

### 2.1. JSONL Architecture (Write-Only Legacy)
- **Status**: The project has fully migrated to `sqlite_db.py` for reading data. JSONL is effectively a write-only log.
- **Evidence**: `read_jsonl` is defined but **never called anywhere in the codebase**. `append_jsonl` is called exactly 3 times (`user_model.py:436`, `self_model.py:102`, `policy_layer.py:220`) solely to append to `user_model_updates.jsonl`, `self_errors.jsonl`, and `policy_decisions.jsonl`. 
- **Verdict**: Since `read_jsonl` is dead, these files act as a pure append-only sink. This is legacy bloat that can be replaced with standard Python logging or removed entirely.

### 2.2. FAISS & Retrieval
- **Status**: **Active and functioning.** 
- **Evidence**: FAISS index is heavily integrated with the SQLite backend. `RetrievalBudgetManager` is actively instantiated and heavily utilized in `bot_core.py` alongside Gemini.
- **Verdict**: Not dead code. It is an integral part of the current architecture.

### 2.3. Prediction Engine
- **Status**: Started but abandoned.
- **Evidence**: 
  - `storage/sqlite_db.py` contains `async_delete_prediction`, `delete_prediction`.
  - `reasoning.py` contains `add_prediction`, `get_predictions_summary`, `Class Prediction`.
  - Manual grep confirms `add_prediction` and `delete_prediction` are never invoked in the main execution loops.
- **Verdict**: Fully dead code. Safe to prune to clean up the DB wrapper and reasoning modules.

---

## 3. FALSE POSITIVES (AST LIMITATIONS)
During the initial scan, several components were flagged as "dead" by AST analysis but manual verification proves they are actively used:

- **Pydantic DataClasses** (`UserMood`, `FactItem`, `ReflectionItem`, `PatternItem`, etc. in `llm/client.py`): Actively used via dynamic `response_model=` typings in the LLM pipeline.
- **Bot Commands** (`show_goals`, `reset_context`, `show_reasoning_state`, `show_selfmap`): Used as function references within a dictionary mapping in `bot_core.py:588` (e.g. `"show_goals": show_goals`). They are fully operational.
- **Pipeline Stages** (`_personality_critical_section` in `llm/pipeline.py`): Passed dynamically via `asyncio.to_thread(_personality_critical_section, ...)` on line 574.

---

## 4. SAFE TO DELETE

| Component / Function | File | Reason for Deletion |
| :--- | :--- | :--- |
| **Prediction Engine** | `reasoning.py`, `sqlite_db.py` | Classes and methods were prepared but the engine is never called by the decision loop. |
| **JSONL Mirroring** | `storage/jsonl.py` + all `append_jsonl` calls | The project uses SQLite Phase 5. JSONL operations are write-only (no reads), causing unnecessary I/O. |
| **Dead Math Utilities**| `memory/vector_index.py` | `cosine_similarity` exists but FAISS internal L2 distance is used instead. |
| **Commented Snippets** | `bot_core.py:1144` | Dead commented-out retrieval context injection logic. |

---

## 5. SUSPICIOUS (Requires Manual Check)

| Component / Function | File | Reason |
| :--- | :--- | :--- |
| **Shadow Eval** | `llm/shadow_eval.py` | Evaluates identity changes (`evaluate_identity_change`), imported in `user_model.py:250`, but its impact on the core loop seems disjointed. Needs architectural review. |
| **Proactive Telemetry** | `proactive/telemetry.py` | `record_ping_sent`, `record_ping_reply` might not be wired to the actual SQLite backend correctly, or might just log to void. |

---

## 6. UNFINISHED FEATURES

| Feature Subsystem | Files Involved | Status & Observations |
| :--- | :--- | :--- |
| **Prediction Engine** | `reasoning.py`, `sqlite_db.py` | Scaffolding is fully written (`Prediction` dataclass, `async_list_predictions`, `add_prediction`) but the bot loop never calls `add_prediction` based on LLM outputs. |
| **Pattern Engine (Partial)**| `memory/store.py` | `update_pattern` exists but is isolated. |
| **Life Transitions** | `llm/client.py`, `sqlite_db.py` | `LifeTransitionItem` and `delete_life_transition` exist, suggesting a feature to track long-term user life changes, but extraction/insertion logic is incomplete. |
| **Monthbook / Diary** | `storage/sqlite_db.py` | `save_monthbook`, `load_monthbook`. An attempt to create monthly summaries, never wired to proactive loops or chat handlers. |

"""
    with open('c:\\Games\\audit_dead_code.md', 'w', encoding='utf-8') as f:
        f.write(md)

if __name__ == "__main__":
    generate_report()
