def generate_report():
    md = """# PROJECT ARCHITECTURE & DEAD CODE AUDIT
**Role**: Senior Python Architect & Code Auditor  
**Goal**: Identify dead code, unused files, incomplete functions, legacy architecture, and technical debt.  
**Context**: Preparation for migration to a local LLM. Aggressive but evidenced-based pruning.

---

## 1. DEPENDENCY MAP & FILE USAGE
Based on AST parsing and static analysis across the `companion/` directory.

| File | Status | Imports/Used By | Removable? |
| :--- | :--- | :--- | :--- |
| `bot_core.py` | Active | Main entrypoint | No |
| `main.py` | Active | Runner | No |
| `config.py` | Active | All files | No |
| `policy_layer.py` | Active | `bot_core.py` | No |
| `reasoning.py` | **Partial** | `bot_core.py` | **Has dead subsystems** |
| `self_model.py` | Active | `bot_core.py` | No |
| `user_model.py` | Active | `bot_core.py` | No |
| `critique_manager.py` | Active | `bot_core.py` | No |
| `llm/client.py` | Active | `pipeline.py`, etc | **Contains dead models** |
| `llm/pipeline.py` | Active | `bot_core.py` | **Contains unused phases** |
| `llm/shadow_eval.py`| Suspicious | `user_model.py` | Needs check |
| `memory/store.py` | Active | Multiple | **Contains dead RAG logic** |
| `memory/vector_index.py` | Active | `store.py` | Yes (if FAISS dropped) |
| `memory/retrieval.py` | Active | `bot_core.py` | Yes (if RAG dropped) |
| `storage/jsonl.py` | **Legacy** | `storage/__init__.py`, etc | **Yes** |
| `storage/sqlite_db.py`| Active | Multiple | **Has dead schema parts** |
| `proactive/*.py` | Active | `background_scheduler.py` | **Some parts unused** |
| `handlers/*.py` | Active | `main.py` (via AIOGram) | No (False Positives) |

---

## 2. ARCHITECTURE REMNANTS (FAISS, JSONL, RAG, etc.)

### 2.1. JSONL Architecture (Legacy Memory)
- **Status**: The project has fully migrated to `sqlite_db.py` ("SQLite backend — Phase 5"). 
- **Evidence**: `storage/sqlite_db.py` has a method `_migrate_jsonl_files(self, conn)`. The file `storage/jsonl.py` only serves as a legacy mirror (`append_jsonl`, `rotate_jsonl`).
- **Verdict**: `jsonl.py` and all its imports (`self_model.py`, `policy_layer.py`, `main.py`) are legacy overhead.

### 2.2. FAISS & Vector Embeddings
- **Status**: FAISS is actively used in `vector_index.py` and `retrieval.py`, but represents a heavy dependency. 
- **Evidence**: `cosine_similarity` in `memory/vector_index.py` is defined but unused (AST shows 0 references). `bot_core.py` contains commented out or partially disabled FAISS mechanisms. If moving to local LLMs with larger contexts, the chunked FAISS Retrieval (`RetrievalBudgetManager`) can be eliminated to save memory.
- **Verdict**: Mark `vector_index.py` and `retrieval.py` as architectural candidates for deletion upon Local LLM migration.

### 2.3. Prediction Engine & Pattern Engine
- **Status**: Started but abandoned.
- **Evidence**: 
  - `storage/sqlite_db.py` contains `async_list_predictions`, `delete_prediction` (unused).
  - `reasoning.py` contains `add_prediction`, `get_predictions_summary`, `Class Prediction` (unused).
  - `memory/store.py` contains `update_pattern` (unused). `Class PatternItem` is unused.
- **Verdict**: Fully dead code.

### 2.4. Reflection & Personality Pipelines
- **Status**: Partially detached.
- **Evidence**: `_personality_critical_section` in `llm/pipeline.py` is completely unreferenced. `generate_reflections` is unreferenced. `Class PersonalityPipelineResult` and `Class ReflectionItem` are dead.
- **Verdict**: Unfinished feature stubs.

---

## 3. DEAD CODE & UNREACHABLE CODE DETAILED ANALYSIS

| File | Line | Issue | Confidence | Why | Consequence of Removal |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `reasoning.py` | Various | `Prediction` class, `add_prediction` | 100% | Never called across the entire AST. | Clean up of Prediction Engine bloat. |
| `memory/store.py` | Various | `update_pattern`, `async_add_reflection` | 100% | Methods defined but never invoked by the main bot loop. | Pattern Engine bloat removed. |
| `storage/sqlite_db.py` | Various | `async_list_predictions`, `delete_prediction` | 100% | No calling functions. | DB wrapper becomes cleaner. |
| `llm/client.py` | Various | `UserMood`, `FactItem`, `PatternItem`, `ReflectionItem` | 95% | DataClasses defined for pipeline structures that are never instanced. | Minor line savings, cleaner models. |
| `storage/jsonl.py` | All | `read_jsonl`, `append_jsonl`, `rotate_jsonl` | 90% | Phase 5 migration explicitly obsoletes them. | Removes disk I/O mirroring overhead. |
| `bot_core.py` | 1144 | `REMOVED: Do not inject retrieval_context...` | 100% | Commented out logic due to "context limit violation". | Removes misleading commented code. |
| `llm/pipeline.py` | Various | `generate_reflections`, `_personality_critical_section` | 95% | Functions exist but are not part of the active pipeline sequence. | Removes unused LLM prompt generation. |
| `memory/vector_index.py` | Various | `cosine_similarity` | 100% | Replaced by FAISS internal L2 distance, custom function is orphaned. | Removes unused math logic. |

---

## 4. SAFE TO DELETE

| Component / Function | File | Reason for Deletion |
| :--- | :--- | :--- |
| **Prediction Engine** | `reasoning.py`, `sqlite_db.py` | Classes and methods were prepared but the engine is never called by the decision loop. |
| **JSONL Mirroring** | `storage/jsonl.py` + all `append_jsonl` calls | The project uses SQLite Phase 5. JSONL causes unnecessary I/O and code bloat. |
| **Orphan DataClasses** | `llm/client.py` | `Prediction`, `ReflectionItem`, `PatternItem`, `CommPrefItem`, etc. are Pydantic/dataclass models never used for parsing. |
| **Personality Phase Stub** | `llm/pipeline.py` | `_personality_critical_section` exists but isn't integrated in `_sync_stages`. |
| **Unused Handlers** | `bot_core.py` | `reset_context`, `show_goals`, `show_reasoning_state`, `show_selfmap`. Unused debugging utilities. |

---

## 5. SUSPICIOUS (Requires Manual Check)

| Component / Function | File | Reason |
| :--- | :--- | :--- |
| **Handlers/Commands** | `handlers/chat.py`, `handlers/commands.py` | AST marks `cmd_start`, `multimodal_handler` as unused. Likely they are loaded dynamically via aiogram decorators, but we must verify if any handler is truly unregistered. |
| **Shadow Eval** | `llm/shadow_eval.py` | Evaluates identity changes, but seems completely disjointed from the core LLM execution loop. AST shows low usage. |
| **Proactive Telemetry** | `proactive/telemetry.py` | `record_ping_sent`, `record_ping_reply` might not be wired to the actual SQLite backend correctly, or might just log to void. |
| **FAISS vs Local LLM** | `memory/vector_index.py`, `retrieval.py` | Actively used in code, but if moving to Local LLM with high context window, the 50_000 char budget retrieval is a massive unnecessary bottleneck. |

---

## 6. UNFINISHED FEATURES

| Feature Subsystem | Files Involved | Status & Observations |
| :--- | :--- | :--- |
| **Prediction Engine** | `reasoning.py`, `sqlite_db.py` | Scaffolding is fully written (`Prediction` dataclass, `async_list_predictions`, `add_prediction`) but the bot loop never calls `add_prediction` based on LLM outputs. |
| **Pattern Engine** | `memory/store.py` | `update_pattern` and `async_search_patterns` exist. Seems like an attempt to build a global behavior graph, abandoned midway. |
| **Reflection Engine** | `llm/pipeline.py`, `memory/store.py` | `generate_reflections` attempts to analyze the day/session, but is not connected to a background task or the `_sync_stages` pipeline. |
| **Life Transitions** | `llm/client.py`, `sqlite_db.py` | `LifeTransitionItem` and `delete_life_transition` exist, suggesting a feature to track long-term user life changes, but no insertion or LLM extraction logic is wired. |
| **Monthbook / Diary** | `storage/sqlite_db.py` | `save_monthbook`, `load_monthbook`. An attempt to create monthly summaries, never wired to proactive loops or chat handlers. |

"""
    with open('c:\\Games\\audit_dead_code.md', 'w', encoding='utf-8') as f:
        f.write(md)

if __name__ == "__main__":
    generate_report()
