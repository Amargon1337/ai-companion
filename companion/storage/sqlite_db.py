"""SQLite backend — Phase 5, used as primary store with jsonl mirror."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from companion.exceptions import ConcurrentModificationError

logger = logging.getLogger(__name__)


def _json(value: Any) -> str:
  return json.dumps(value if value is not None else [], ensure_ascii=False)


def _loads(value: str | None, default: Any = None) -> Any:
  if value is None:
    return [] if default is None else default
  try:
    return json.loads(value)
  except (TypeError, json.JSONDecodeError):
    return [] if default is None else default

def _configure_conn(conn: sqlite3.Connection) -> None:
  conn.execute("PRAGMA busy_timeout = 5000;")
  conn.execute("PRAGMA journal_mode = WAL;")
  conn.execute("PRAGMA foreign_keys = ON;")


# Phase C: one shared MemoryDatabase per SQLite path. The memory organism used
# to open a fresh connection per component (reasoning_engine singleton,
# user_model._save_model on EVERY save, self_model, telemetry per call) — each
# bypassing the RLock/tx-depth model and churning connections. get_shared_db()
# hands out a single instance per path, so all satellites serialize against
# the same lock. MemoryStore deliberately keeps its OWN instance (lifecycle
# ownership: close() drains the bus and closes the db), but everything else
# shares one connection. Path-keying preserves test isolation (each test
# monkeypatches SQLITE_PATH -> its own shared instance).
_shared_db: "MemoryDatabase | None" = None
_shared_db_lock = None


def get_shared_db() -> "MemoryDatabase":
  global _shared_db, _shared_db_lock
  import threading
  if _shared_db_lock is None:
    _shared_db_lock = threading.Lock()
  from companion.config import SQLITE_PATH
  with _shared_db_lock:
    if (_shared_db is None
        or _shared_db.path != SQLITE_PATH
        or _shared_db.conn is None):
      _shared_db = MemoryDatabase()
    return _shared_db


def reset_shared_db() -> None:
  """Drop the cached shared instance (tests that swap SQLITE_PATH)."""
  global _shared_db, _shared_db_lock
  import threading
  if _shared_db_lock is None:
    _shared_db_lock = threading.Lock()
  with _shared_db_lock:
    if _shared_db is not None:
      try:
        _shared_db.close()
      except Exception:
        pass
      _shared_db = None


class MemoryDatabase:
  def __init__(self, path: str | None = None) -> None:
    import threading
    self._lock = threading.RLock()
    self._tx_state = threading.local()
    self._path: str = ""
    self.conn: sqlite3.Connection | None = None
    from companion.config import SQLITE_PATH as _SQLITE_PATH
    self.path = path if path is not None else _SQLITE_PATH

  @property
  def path(self) -> str:
    return self._path

  @path.setter
  def path(self, value: str) -> None:
    if self._path == value and self.conn is not None:
      return
    if self.conn is not None:
      try:
        self.conn.close()
      except Exception:
        pass
    self._path = value
    self.conn = sqlite3.connect(value, check_same_thread=False)
    _configure_conn(self.conn)
    self.conn.row_factory = sqlite3.Row
    self._init_schema()

  def close(self) -> None:
    with self._lock:
      try:
        self.conn.execute("PRAGMA optimize;")
      except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Error on PRAGMA optimize: {e}")
      self.conn.close()

  def archive_audit_log(self, days: int = 30) -> int:
    """Archive audit_log records older than `days` into data/audit_archive.db."""
    archive_path = os.path.join(os.path.dirname(self.path), "audit_archive.db")
    with self._lock:
      # ATTACH is not allowed inside a transaction, and a leaked attachment
      # breaks every later call with "database archive is already in use".
      if getattr(self._tx_state, "depth", 0) > 0:
        raise sqlite3.OperationalError("archive_audit_log cannot run inside atomic_memory_transaction")
      self.conn.execute(f"ATTACH DATABASE '{archive_path}' AS archive;")
      try:
        self.conn.execute("""
          CREATE TABLE IF NOT EXISTS archive.audit_log (
            audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
            table_name TEXT NOT NULL,
            record_id TEXT NOT NULL,
            action TEXT NOT NULL,
            old_state TEXT,
            new_state TEXT,
            timestamp TEXT
          );
        """)
        res = self.conn.execute("""
          INSERT INTO archive.audit_log (table_name, record_id, action, old_state, new_state, timestamp)
          SELECT table_name, record_id, action, old_state, new_state, timestamp FROM main.audit_log
          WHERE timestamp < datetime('now', '-' || ? || ' days');
        """, (days,))
        moved = res.rowcount
        self.conn.execute("DELETE FROM main.audit_log WHERE timestamp < datetime('now', '-' || ? || ' days');", (days,))
        self.conn.commit()
      except Exception:
        self.conn.rollback()
        raise
      finally:
        # Always detach, otherwise every subsequent call fails.
        try:
          self.conn.execute("DETACH DATABASE archive;")
        except sqlite3.OperationalError as e:
          logger.error("Failed to DETACH audit archive: %s", e, exc_info=True)
      return moved

  @contextmanager
  def _conn(self) -> Generator[sqlite3.Connection, None, None]:
    with self._lock:
      try:
        yield self.conn
        if getattr(self._tx_state, "depth", 0) == 0:
          self.conn.commit()
      except Exception:
        if getattr(self._tx_state, "depth", 0) == 0:
          self.conn.rollback()
        raise

  @contextmanager
  def atomic_memory_transaction(self) -> Generator[sqlite3.Connection, None, None]:
    """Execute a block of memory operations inside a BEGIN IMMEDIATE transaction."""
    with self._lock:
      depth = getattr(self._tx_state, "depth", 0)
      is_outer = (depth == 0)
      if is_outer:
        self.conn.execute("BEGIN IMMEDIATE TRANSACTION;")
      self._tx_state.depth = depth + 1
      try:
        yield self.conn
        if is_outer:
          self.conn.commit()
      except Exception:
        if is_outer:
          self.conn.rollback()
        raise
      finally:
        self._tx_state.depth = max(0, getattr(self._tx_state, "depth", 1) - 1)

  def _init_schema(self) -> None:
    with self._conn() as conn:
      conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS facts (
          id TEXT PRIMARY KEY,
          fact TEXT NOT NULL,
          date TEXT,
          created_at TEXT,
          memory_kind TEXT DEFAULT 'event',
          importance INTEGER DEFAULT 5,
          confidence REAL DEFAULT 0.8,
          source TEXT,
          source_type TEXT,
          tags TEXT DEFAULT '[]',
          status TEXT DEFAULT 'active' CHECK(status IN ('quarantine', 'pending_embedding', 'active', 'dormant', 'pending_review', 'archived', 'superseded', 'purged')),
          valid_from TEXT,
          valid_until TEXT,
          schema_version INTEGER DEFAULT 1,
          evidence TEXT DEFAULT '[]',
          facts_sent_count INTEGER DEFAULT 0,
          facts_used_count INTEGER DEFAULT 0,
          embedding BLOB,
          category TEXT DEFAULT 'life',
          anchor_flag INTEGER DEFAULT 0,
          manual_lock INTEGER DEFAULT 0,
          archived INTEGER DEFAULT 0,
          updated_at TEXT,
          access_count INTEGER DEFAULT 0,
          decay_exempt INTEGER DEFAULT 0,
          version INTEGER DEFAULT 1,
          superseded_by TEXT DEFAULT '',
          domain TEXT DEFAULT 'user',
          meta TEXT DEFAULT '{}',
          last_accessed TEXT,
          last_retrieved_at TEXT,
          last_used_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_facts_status ON facts(status);
        CREATE INDEX IF NOT EXISTS idx_facts_importance ON facts(importance);
        CREATE INDEX IF NOT EXISTS idx_facts_date ON facts(date);
        CREATE INDEX IF NOT EXISTS idx_facts_composite ON facts(status, date DESC, created_at DESC);

        CREATE TABLE IF NOT EXISTS fact_relations (
          id TEXT PRIMARY KEY,
          from_id TEXT NOT NULL,
          to_id TEXT NOT NULL,
          relation TEXT NOT NULL,
          created_at TEXT,
          reason TEXT,
          confidence REAL DEFAULT 0.8
        );
        CREATE INDEX IF NOT EXISTS idx_fact_relations_from_id ON fact_relations(from_id);
        CREATE INDEX IF NOT EXISTS idx_fact_relations_to_id ON fact_relations(to_id);
        CREATE INDEX IF NOT EXISTS idx_fact_relations_composite ON fact_relations(from_id, to_id);

        CREATE TABLE IF NOT EXISTS messages (
          id TEXT PRIMARY KEY,
          ts TEXT,
          role TEXT,
          text TEXT,
          importance INTEGER DEFAULT 5,
          mode TEXT DEFAULT 'default',
          signals TEXT DEFAULT '[]',
          user_id INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts);
        CREATE INDEX IF NOT EXISTS idx_messages_importance ON messages(importance);

        CREATE TABLE IF NOT EXISTS reflections (
          id TEXT PRIMARY KEY,
          insight TEXT NOT NULL,
          based_on TEXT DEFAULT '[]',
          period TEXT,
          importance INTEGER DEFAULT 7,
          confidence REAL DEFAULT 0.8,
          status TEXT DEFAULT 'active',
          version INTEGER DEFAULT 1,
          created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_reflections_status_composite ON reflections(status, created_at DESC);

        CREATE TABLE IF NOT EXISTS beliefs (
          id TEXT PRIMARY KEY,
          belief TEXT NOT NULL,
          based_on TEXT DEFAULT '[]',
          importance INTEGER DEFAULT 6,
          status TEXT DEFAULT 'active',
          version INTEGER DEFAULT 1,
          created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_beliefs_status_composite ON beliefs(status, importance DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_beliefs_unique_text ON beliefs(belief);

        CREATE TABLE IF NOT EXISTS patterns (
          id TEXT PRIMARY KEY,
          pattern TEXT NOT NULL,
          category TEXT DEFAULT 'behavior',
          evidence TEXT DEFAULT '[]',
          importance INTEGER DEFAULT 6,
          confidence REAL DEFAULT 0.7,
          status TEXT DEFAULT 'active',
          created_at TEXT,
          updated_at TEXT,
          version INTEGER DEFAULT 1,
          superseded_by TEXT DEFAULT '',
          last_confirmed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_patterns_status ON patterns(status, importance);

        CREATE TABLE IF NOT EXISTS communication_prefs (
          id TEXT PRIMARY KEY,
          style TEXT DEFAULT '',
          formality TEXT DEFAULT '',
          humor TEXT DEFAULT '',
          language TEXT DEFAULT '',
          liked_topics TEXT DEFAULT '[]',
          avoided_topics TEXT DEFAULT '[]',
          updated_at TEXT,
          version INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS human_model (
          id TEXT PRIMARY KEY,
          goals TEXT DEFAULT '[]',
          fears TEXT DEFAULT '[]',
          strengths TEXT DEFAULT '[]',
          recurring_mistakes TEXT DEFAULT '[]',
          long_term_trends TEXT DEFAULT '[]',
          updated_at TEXT,
          version INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS life_transitions (
          id TEXT PRIMARY KEY,
          domain TEXT DEFAULT 'identity',
          from_state TEXT NOT NULL,
          to_state TEXT NOT NULL,
          explanation TEXT DEFAULT '',
          trigger_events TEXT DEFAULT '[]',
          confidence REAL DEFAULT 0.7,
          importance INTEGER DEFAULT 6,
          status TEXT DEFAULT 'active',
          created_at TEXT,
          last_confirmed_at TEXT,
          version INTEGER DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS idx_life_transitions_status ON life_transitions(status, importance);

        CREATE TABLE IF NOT EXISTS timeline (
          id TEXT PRIMARY KEY,
          date TEXT NOT NULL,
          event TEXT NOT NULL,
          importance INTEGER DEFAULT 5,
          description TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_timeline_date ON timeline(date);

        CREATE TABLE IF NOT EXISTS episodes (
          id TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          narrative TEXT NOT NULL,
          date TEXT,
          participants TEXT DEFAULT '[]',
          emotions TEXT DEFAULT '{}',
          lesson TEXT DEFAULT '',
          fact_ids TEXT DEFAULT '[]',
          fact_id TEXT DEFAULT '',
          importance INTEGER DEFAULT 7,
          confidence REAL DEFAULT 0.8,
          created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_episodes_date ON episodes(date);

        CREATE TABLE IF NOT EXISTS meta (
          key TEXT PRIMARY KEY,
          value TEXT
        );

        CREATE TABLE IF NOT EXISTS sessions (
          user_id INTEGER PRIMARY KEY,
          message_count INTEGER DEFAULT 0,
          last_active TEXT,
          created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS retrieval_metrics (
          message_id TEXT PRIMARY KEY,
          timestamp TEXT,
          facts_sent INTEGER,
          facts_used INTEGER,
          goals_sent INTEGER,
          goals_used INTEGER,
          reflections_sent INTEGER,
          reflections_used INTEGER
        );
        CREATE TABLE IF NOT EXISTS retrieval_replays (
          replay_id TEXT PRIMARY KEY,
          user_id INTEGER NOT NULL,
          created_at TEXT NOT NULL,
          payload TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_retrieval_replays_user ON retrieval_replays(user_id, created_at DESC);
        CREATE TABLE IF NOT EXISTS summaries (
          id TEXT PRIMARY KEY,
          content TEXT NOT NULL,
          created_at TEXT,
          embedding_hash TEXT,
          status TEXT DEFAULT 'active'
        );
        CREATE TABLE IF NOT EXISTS audit_log (
          audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
          table_name TEXT NOT NULL,
          record_id TEXT NOT NULL,
          action TEXT NOT NULL,
          old_state TEXT,
          new_state TEXT,
          timestamp TEXT DEFAULT (datetime('now', 'utc'))
        );
        CREATE TABLE IF NOT EXISTS proactive_events (
          id TEXT PRIMARY KEY,
          timestamp TEXT NOT NULL,
          reason TEXT NOT NULL,
          baseline_state TEXT,
          urgency INTEGER,
          message TEXT,
          sent BOOLEAN DEFAULT 1,
          user_replied BOOLEAN DEFAULT 0,
          reply_delay_hours REAL
        );
        CREATE TABLE IF NOT EXISTS goals (
          goal_id TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          priority INTEGER DEFAULT 5,
          status TEXT DEFAULT 'active',
          description TEXT DEFAULT '',
          blockers TEXT DEFAULT '[]',
          next_actions TEXT DEFAULT '[]',
          resources TEXT DEFAULT '[]',
          obstacles TEXT DEFAULT '[]',
          progress_markers TEXT DEFAULT '[]',
          version INTEGER DEFAULT 1,
          created_at TEXT,
          updated_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status);

        CREATE TABLE IF NOT EXISTS causal_links (
          link_id TEXT PRIMARY KEY,
          cause TEXT NOT NULL,
          effect TEXT NOT NULL,
          confidence REAL DEFAULT 0.5,
          evidence TEXT DEFAULT '[]',
          mechanism TEXT DEFAULT '',
          observed_count INTEGER DEFAULT 1,
          created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_causal_links_confidence ON causal_links(confidence);

        CREATE TABLE IF NOT EXISTS predictions (
          prediction_id TEXT PRIMARY KEY,
          hypothesis TEXT NOT NULL,
          confidence REAL DEFAULT 0.5,
          timeframe TEXT DEFAULT '',
          conditions TEXT DEFAULT '[]',
          based_on TEXT DEFAULT '[]',
          outcome TEXT DEFAULT 'pending',
          created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_predictions_outcome ON predictions(outcome);

        CREATE TABLE IF NOT EXISTS entities (
          entity_id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          type TEXT NOT NULL,
          importance REAL DEFAULT 0.5,
          version INTEGER DEFAULT 1,
          created_at TEXT,
          updated_at TEXT,
          last_mentioned_at TEXT,
          aliases TEXT DEFAULT '[]',
          summary TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
        CREATE INDEX IF NOT EXISTS idx_entities_importance ON entities(importance);

        CREATE TABLE IF NOT EXISTS entity_attributes (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          entity_id TEXT NOT NULL,
          attribute_key TEXT NOT NULL,
          attribute_value TEXT NOT NULL,
          confidence REAL DEFAULT 0.8,
          source_fact_id TEXT,
          created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_entity_attributes_entity_id ON entity_attributes(entity_id);

        CREATE TABLE IF NOT EXISTS entity_relations (
          relation_id TEXT PRIMARY KEY,
          from_entity_id TEXT NOT NULL,
          to_entity_id TEXT NOT NULL,
          relation_type TEXT NOT NULL,
          trust REAL DEFAULT 0.5,
          interaction_frequency REAL DEFAULT 0.0,
          sentiment REAL DEFAULT 0.0,
          relationship_strength REAL DEFAULT 0.5,
          version INTEGER DEFAULT 1,
          last_seen_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_entity_relations_from_to ON entity_relations(from_entity_id, to_entity_id);

        CREATE TABLE IF NOT EXISTS entity_mentions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          entity_id TEXT NOT NULL,
          fact_id TEXT NOT NULL,
          context_snippet TEXT DEFAULT '',
          created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_entity_mentions_fact ON entity_mentions(fact_id);
        CREATE INDEX IF NOT EXISTS idx_entity_mentions_entity ON entity_mentions(entity_id);

        CREATE TABLE IF NOT EXISTS monthbooks (
          ym TEXT PRIMARY KEY,
          content TEXT NOT NULL,
          updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS prospective_tasks (
          task_id TEXT PRIMARY KEY,
          text TEXT NOT NULL,
          due_ts REAL NOT NULL,
          status TEXT DEFAULT 'pending',
          source_message_id TEXT,
          created_at TEXT,
          triggered_at TEXT,
          metadata TEXT DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_prospective_due ON prospective_tasks(status, due_ts);

        CREATE TABLE IF NOT EXISTS temporal_counters (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          counter_name TEXT NOT NULL UNIQUE,
          description TEXT NOT NULL,
          start_date TEXT NOT NULL,
          timezone TEXT NOT NULL DEFAULT 'UTC',
          status TEXT NOT NULL DEFAULT 'active',
          archived INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          deleted_at TEXT,
          CHECK (status IN ('active', 'paused', 'stopped')),
          CHECK (archived IN (0, 1)),
          CHECK (length(start_date) = 10)
        );
        CREATE INDEX IF NOT EXISTS idx_temporal_counters_status ON temporal_counters(status, archived, deleted_at);

        CREATE TABLE IF NOT EXISTS temporal_counter_pauses (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          counter_id INTEGER NOT NULL,
          pause_start_date TEXT NOT NULL,
          pause_end_date TEXT,
          reason TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY (counter_id) REFERENCES temporal_counters(id) ON DELETE CASCADE,
          CHECK (length(pause_start_date) = 10),
          CHECK (pause_end_date IS NULL OR length(pause_end_date) = 10)
        );
        CREATE INDEX IF NOT EXISTS idx_temporal_counter_pauses_counter_id ON temporal_counter_pauses(counter_id);

        CREATE TABLE IF NOT EXISTS memory_access_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          fact_id TEXT NOT NULL,
          accessed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          query_hash TEXT,
          vector_score REAL,
          final_score REAL,
          source TEXT NOT NULL DEFAULT 'rag',
          retrieval_reason TEXT DEFAULT 'semantic',
          FOREIGN KEY (fact_id) REFERENCES facts(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_memory_access_log_fact_time ON memory_access_log(fact_id, accessed_at DESC);

        CREATE TABLE IF NOT EXISTS memory_mutation_log (
          id TEXT PRIMARY KEY,
          timestamp TEXT NOT NULL,
          entity_id TEXT NOT NULL,
          entity_type TEXT DEFAULT 'fact',
          action TEXT NOT NULL,
          reason TEXT NOT NULL,
          state_before TEXT NOT NULL,
          state_after TEXT NOT NULL,
          initiator TEXT DEFAULT 'governor'
        );
        CREATE INDEX IF NOT EXISTS idx_mutation_log_entity ON memory_mutation_log(entity_id);
        CREATE INDEX IF NOT EXISTS idx_mutation_log_timestamp ON memory_mutation_log(timestamp DESC);

        CREATE TABLE IF NOT EXISTS state_models (
          model_type TEXT PRIMARY KEY,
          payload_json TEXT NOT NULL,
          last_updated TEXT DEFAULT CURRENT_TIMESTAMP
        );
        
        CREATE TABLE IF NOT EXISTS shared_lore_candidates (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          candidate_phrase TEXT NOT NULL,
          context TEXT,
          timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
          status TEXT DEFAULT 'pending'
        );

        CREATE TABLE IF NOT EXISTS faiss_mapping (
          faiss_id INTEGER PRIMARY KEY,
          fact_id TEXT UNIQUE NOT NULL
        );
        """
      )
      
      # Try to create triggers separately to avoid syntax errors in older SQLite versions on combined executescript
      try:
          conn.execute('''
              CREATE TRIGGER IF NOT EXISTS audit_facts_insert
              AFTER INSERT ON facts
              BEGIN
                  INSERT INTO audit_log (table_name, record_id, action, new_state)
                  VALUES ('facts', NEW.id, 'INSERT', json_object('fact', NEW.fact, 'status', NEW.status, 'importance', NEW.importance, 'memory_kind', NEW.memory_kind));
              END;
          ''')
          conn.execute('''
              CREATE TRIGGER IF NOT EXISTS audit_facts_update
              AFTER UPDATE ON facts
              BEGIN
                  INSERT INTO audit_log (table_name, record_id, action, old_state, new_state)
                  VALUES ('facts', NEW.id, 'UPDATE', 
                          json_object('fact', OLD.fact, 'status', OLD.status, 'importance', OLD.importance, 'memory_kind', OLD.memory_kind),
                          json_object('fact', NEW.fact, 'status', NEW.status, 'importance', NEW.importance, 'memory_kind', NEW.memory_kind));
              END;
          ''')
          conn.execute('''
              CREATE TRIGGER IF NOT EXISTS audit_facts_delete
              AFTER DELETE ON facts
              BEGIN
                  INSERT INTO audit_log (table_name, record_id, action, old_state)
                  VALUES ('facts', OLD.id, 'DELETE', json_object('fact', OLD.fact, 'status', OLD.status, 'importance', OLD.importance, 'memory_kind', OLD.memory_kind));
              END;
          ''')
          conn.execute('''
              CREATE TRIGGER IF NOT EXISTS audit_entities_insert
              AFTER INSERT ON entities
              BEGIN
                  INSERT INTO audit_log (table_name, record_id, action, new_state)
                  VALUES ('entities', NEW.entity_id, 'INSERT', json_object('name', NEW.name, 'type', NEW.type, 'importance', NEW.importance));
              END;
          ''')
          conn.execute('''
              CREATE TRIGGER IF NOT EXISTS audit_entities_update
              AFTER UPDATE ON entities
              BEGIN
                  INSERT INTO audit_log (table_name, record_id, action, old_state, new_state)
                  VALUES ('entities', NEW.entity_id, 'UPDATE',
                          json_object('name', OLD.name, 'type', OLD.type, 'importance', OLD.importance),
                          json_object('name', NEW.name, 'type', NEW.type, 'importance', NEW.importance));
              END;
          ''')
          conn.execute('''
              CREATE TRIGGER IF NOT EXISTS audit_entity_attributes_insert
              AFTER INSERT ON entity_attributes
              BEGIN
                  INSERT INTO audit_log (table_name, record_id, action, new_state)
                  VALUES ('entity_attributes', NEW.entity_id || ':' || NEW.attribute_key, 'INSERT', json_object('val', NEW.attribute_value));
              END;
          ''')
          conn.execute('''
              CREATE TRIGGER IF NOT EXISTS audit_entity_relations_insert
              AFTER INSERT ON entity_relations
              BEGIN
                  INSERT INTO audit_log (table_name, record_id, action, new_state)
                  VALUES ('entity_relations', NEW.from_entity_id || ':' || NEW.to_entity_id || ':' || NEW.relation_type, 'INSERT', json_object('trust', NEW.trust));
              END;
          ''')
          conn.execute('''
              CREATE TRIGGER IF NOT EXISTS audit_entity_mentions_insert
              AFTER INSERT ON entity_mentions
              BEGIN
                  INSERT INTO audit_log (table_name, record_id, action, new_state)
                  VALUES ('entity_mentions', NEW.fact_id || ':' || NEW.entity_id, 'INSERT', json_object('snippet', NEW.context_snippet));
              END;
          ''')
      except sqlite3.OperationalError as e:
          logger.error("Audit trigger creation failed: %s", e, exc_info=True)
      try:
        cursor = conn.execute("PRAGMA table_info(entities)")
        e_cols = [row[1] for row in cursor.fetchall()]
        if "aliases" not in e_cols:
          conn.execute("ALTER TABLE entities ADD COLUMN aliases TEXT DEFAULT '[]'")
        if "summary" not in e_cols:
          conn.execute("ALTER TABLE entities ADD COLUMN summary TEXT DEFAULT ''")
      except sqlite3.OperationalError:
        pass
      try:
        cursor = conn.execute("PRAGMA table_info(facts)")
        cols = [row[1] for row in cursor.fetchall()]
        if "evidence" not in cols:
          conn.execute("ALTER TABLE facts ADD COLUMN evidence TEXT DEFAULT '[]'")
        if "facts_sent_count" not in cols:
          conn.execute("ALTER TABLE facts ADD COLUMN facts_sent_count INTEGER DEFAULT 0")
        if "facts_used_count" not in cols:
          conn.execute("ALTER TABLE facts ADD COLUMN facts_used_count INTEGER DEFAULT 0")
        if "embedding" not in cols:
          conn.execute("ALTER TABLE facts ADD COLUMN embedding BLOB")
        for col, ddl in {
          "category": "ALTER TABLE facts ADD COLUMN category TEXT DEFAULT 'life'",
          "anchor_flag": "ALTER TABLE facts ADD COLUMN anchor_flag INTEGER DEFAULT 0",
          "manual_lock": "ALTER TABLE facts ADD COLUMN manual_lock INTEGER DEFAULT 0",
          "archived": "ALTER TABLE facts ADD COLUMN archived INTEGER DEFAULT 0",
          "updated_at": "ALTER TABLE facts ADD COLUMN updated_at TEXT",
          "last_accessed": "ALTER TABLE facts ADD COLUMN last_accessed TEXT",
          "access_count": "ALTER TABLE facts ADD COLUMN access_count INTEGER DEFAULT 0",
          "decay_exempt": "ALTER TABLE facts ADD COLUMN decay_exempt INTEGER DEFAULT 0",
          "version": "ALTER TABLE facts ADD COLUMN version INTEGER DEFAULT 1",
          "superseded_by": "ALTER TABLE facts ADD COLUMN superseded_by TEXT DEFAULT ''",
          "domain": "ALTER TABLE facts ADD COLUMN domain TEXT DEFAULT 'user'",
          "meta": "ALTER TABLE facts ADD COLUMN meta TEXT DEFAULT '{}'",
          "last_retrieved_at": "ALTER TABLE facts ADD COLUMN last_retrieved_at TEXT",
          "last_used_at": "ALTER TABLE facts ADD COLUMN last_used_at TEXT",
        }.items():
          if col not in cols:
            conn.execute(ddl)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_retrieval_v2 ON facts(status, archived, anchor_flag, importance, last_accessed)")
        # Pattern freshness (Reliability Layer): дата последнего подтверждения.
        try:
          p_cols = [r[1] for r in conn.execute("PRAGMA table_info(patterns)").fetchall()]
          if "last_confirmed_at" not in p_cols:
            conn.execute("ALTER TABLE patterns ADD COLUMN last_confirmed_at TEXT")
            conn.execute("UPDATE patterns SET last_confirmed_at = created_at WHERE created_at IS NOT NULL")
        except sqlite3.OperationalError:
          pass
        try:
          mut_cols = [r[1] for r in conn.execute("PRAGMA table_info(memory_mutation_log)").fetchall()]
          if "initiator" not in mut_cols:
            conn.execute("ALTER TABLE memory_mutation_log ADD COLUMN initiator TEXT DEFAULT 'governor'")
        except sqlite3.OperationalError:
          pass
        try:
          acc_cols = [r[1] for r in conn.execute("PRAGMA table_info(memory_access_log)").fetchall()]
          if "retrieval_reason" not in acc_cols:
            conn.execute("ALTER TABLE memory_access_log ADD COLUMN retrieval_reason TEXT DEFAULT 'semantic'")
        except sqlite3.OperationalError:
          pass
        for table_name in ("beliefs", "goals", "reflections"):
          try:
            t_cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table_name})").fetchall()]
            if "version" not in t_cols:
              conn.execute(f"ALTER TABLE {table_name} ADD COLUMN version INTEGER DEFAULT 1")
          except sqlite3.OperationalError:
            pass
        conn.execute("UPDATE facts SET anchor_flag=1 WHERE anchor_flag=0 AND (tags LIKE '%anchor%' OR tags LIKE '%core_identity%' OR tags LIKE '%pinned%' OR memory_kind='permanent')")
        conn.execute("UPDATE facts SET archived=1 WHERE archived=0 AND status='archived'")
      except sqlite3.OperationalError as e:
        logger.error("Facts schema migration failed: %s", e, exc_info=True)

      self._migrate_jsonl_files(conn)
      self._migrate_cognitive_schema(conn)

      # Schema version marker. v2 = cognitive epistemic columns present. Set only
      # when the required facts columns are in place — a legacy DB whose ALTERs
      # failed stays at its prior version as a signal rather than silently ok.
      cols = {r[1] for r in conn.execute("PRAGMA table_info(facts)").fetchall()}
      required = {"version", "superseded_by", "domain", "meta", "last_accessed"}
      if required.issubset(cols):
        current_version = conn.execute("PRAGMA user_version").fetchone()[0]
        cognitive_ok = {"epistemic_class", "support_count", "contradiction_count"}.issubset(cols)
        target = 2 if cognitive_ok else 1
        if current_version < target:
          conn.execute(f"PRAGMA user_version = {target}")
      else:
        logger.error(
          "Schema incomplete, user_version left unchanged. Missing: %s", sorted(required - cols)
        )


  def _migrate_cognitive_schema(self, conn: sqlite3.Connection) -> None:
    """R1 (schema-only): epistemic typing columns + cognitive kernel tables.

    Every statement is idempotent (IF NOT EXISTS / guarded ALTERs) so rerunning
    `_init_schema` on a live database is a no-op. No logic changes ship in this
    migration — behavior lands in later roadmap steps. Cognitive function of each
    table is recorded in graphify-out/OMNI_COGNITIVE_BLUEPRINT.md (Phase 3).
    """
    # -- facts: epistemic typing + support/contradiction counters (kernel K2) --
    try:
      cols = {r[1] for r in conn.execute("PRAGMA table_info(facts)").fetchall()}
      fact_alters: dict[str, str] = {
        "epistemic_class": (
          "ALTER TABLE facts ADD COLUMN epistemic_class TEXT NOT NULL DEFAULT 'DIRECT_FACT' "
          "CHECK (epistemic_class IN ('DIRECT_FACT','HYPOTHESIS','LLM_INFERENCE','PREDICTION'))"
        ),
        "support_count": "ALTER TABLE facts ADD COLUMN support_count INTEGER NOT NULL DEFAULT 0 CHECK (support_count >= 0)",
        "contradiction_count": "ALTER TABLE facts ADD COLUMN contradiction_count INTEGER NOT NULL DEFAULT 0 CHECK (contradiction_count >= 0)",
      }
      for col, ddl in fact_alters.items():
        if col not in cols:
          conn.execute(ddl)
    except sqlite3.OperationalError as e:
      logger.error("Cognitive schema: facts alter failed: %s", e, exc_info=True)

    # -- causal_links: provenance columns (kernel K3) --
    try:
      cl_cols = {r[1] for r in conn.execute("PRAGMA table_info(causal_links)").fetchall()}
      if "derived_from" not in cl_cols:
        conn.execute("ALTER TABLE causal_links ADD COLUMN derived_from TEXT NOT NULL DEFAULT '[]'")
      if "method" not in cl_cols:
        conn.execute("ALTER TABLE causal_links ADD COLUMN method TEXT NOT NULL DEFAULT 'llm' CHECK (method IN ('llm','rule','human','compression'))")
    except sqlite3.OperationalError as e:
      logger.error("Cognitive schema: causal_links alter failed: %s", e, exc_info=True)

    # -- K4 memory_genome: long-run selection pressure on memories --
    conn.execute(
      """
      CREATE TABLE IF NOT EXISTS memory_genome (
        memory_id        TEXT PRIMARY KEY REFERENCES facts(id) ON UPDATE CASCADE,
        origin           TEXT NOT NULL DEFAULT 'unknown',
        generation       INTEGER NOT NULL DEFAULT 0 CHECK (generation >= 0),
        parent_memory_id TEXT REFERENCES facts(id),
        mutation_history TEXT NOT NULL DEFAULT '[]',
        adaptation_log   TEXT NOT NULL DEFAULT '[]',
        survival_score   REAL NOT NULL DEFAULT 0.5 CHECK (survival_score BETWEEN 0.0 AND 1.0),
        born_at          TEXT NOT NULL DEFAULT '',
        last_evaluated_at TEXT
      );
      """
    )
    # -- K5 cognitive_working_memory: bounded, TTL-expiring live context --
    conn.execute(
      """
      CREATE TABLE IF NOT EXISTS cognitive_working_memory (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL,
        slot_type  TEXT NOT NULL CHECK (slot_type IN
                   ('current_goal','active_identity','open_question','salient_fact','affective_state')),
        ref_kind   TEXT CHECK (ref_kind IN ('fact','goal','entity','none')),
        ref_id     TEXT,
        payload    TEXT NOT NULL DEFAULT '',
        salience   REAL NOT NULL DEFAULT 0.5 CHECK (salience BETWEEN 0.0 AND 1.0),
        entered_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
      );
      """
    )
    conn.execute(
      "CREATE INDEX IF NOT EXISTS idx_cwm_user_live ON cognitive_working_memory (user_id, expires_at);"
    )
    # Iron Law #5: working-memory housekeeping FLIPS a flag, it never DELETEs.
    # Slots that expire or are evicted by the per-user cap move to archived.
    try:
      cwm_cols = {r[1] for r in conn.execute("PRAGMA table_info(cognitive_working_memory)").fetchall()}
      if "archived" not in cwm_cols:
        conn.execute("ALTER TABLE cognitive_working_memory ADD COLUMN archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0,1))")
    except sqlite3.OperationalError as e:
      logger.error("Cognitive schema: cognitive_working_memory alter failed: %s", e, exc_info=True)
    # -- Theory of Mind (derived): layered social cognition; never DIRECT_FACT --
    conn.execute(
      """
      CREATE TABLE IF NOT EXISTS theory_of_mind (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        subject_entity_id TEXT NOT NULL REFERENCES entities(entity_id) ON UPDATE CASCADE,
        level       INTEGER NOT NULL CHECK (level IN (1,2,3)),
        claim       TEXT NOT NULL,
        epistemic_class TEXT NOT NULL DEFAULT 'LLM_INFERENCE'
                  CHECK (epistemic_class IN ('HYPOTHESIS','LLM_INFERENCE','PREDICTION')),
        confidence  REAL NOT NULL CHECK (confidence BETWEEN 0.0 AND 1.0),
        basis_ids   TEXT NOT NULL DEFAULT '[]',
        status      TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active','superseded','archived','refuted')),
        created_at  TEXT NOT NULL,
        superseded_by INTEGER REFERENCES theory_of_mind(id)
      );
      """
    )
    conn.execute(
      "CREATE INDEX IF NOT EXISTS idx_tom_subject ON theory_of_mind (subject_entity_id, level, status);"
    )
    # -- Council votes (derived): auditable multi-role evaluation of high-stakes mutations --
    conn.execute(
      """
      CREATE TABLE IF NOT EXISTS council_votes (
        vote_id      TEXT PRIMARY KEY,
        subject_kind TEXT NOT NULL CHECK (subject_kind IN ('fact','pattern','identity','belief','transition')),
        subject_id   TEXT NOT NULL,
        role         TEXT NOT NULL CHECK (role IN ('explorer','critic','historian','predictor','guardian')),
        verdict      TEXT NOT NULL CHECK (verdict IN ('accept','reject','abstain','quarantine')),
        rationale    TEXT NOT NULL DEFAULT '',
        created_at   TEXT NOT NULL
      );
      """
    )
    conn.execute(
      "CREATE INDEX IF NOT EXISTS idx_council_subject ON council_votes (subject_kind, subject_id);"
    )
    # -- Cognitive timeline (derived): reconstruct a turn's phase sequence --
    conn.execute(
      """
      CREATE TABLE IF NOT EXISTS cognitive_timeline (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        turn_id   TEXT NOT NULL,
        user_id   INTEGER NOT NULL,
        phase     TEXT NOT NULL CHECK (phase IN
                  ('perception','interpretation','reflection','decision','memory_update','action')),
        payload_hash TEXT NOT NULL DEFAULT '',
        payload   TEXT NOT NULL DEFAULT '',
        latency_ms REAL,
        created_at TEXT NOT NULL,
        archived  INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0,1))
      );
      """
    )
    conn.execute(
      "CREATE INDEX IF NOT EXISTS idx_ctimeline_turn ON cognitive_timeline (turn_id, id);"
    )
    # -- K8 homeostasis_metrics: drift time series for the meta-auditor --
    conn.execute(
      """
      CREATE TABLE IF NOT EXISTS homeostasis_metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        measured_at TEXT NOT NULL,
        contradiction_density REAL NOT NULL,
        stale_ratio REAL NOT NULL,
        null_embedding_ratio REAL NOT NULL,
        confidence_inflation REAL NOT NULL,
        quarantine_ratio REAL NOT NULL,
        entropy_score REAL NOT NULL
      );
      """
    )
    # -- Event journal (Phase B): durable record of published memory events.
    #    Crash-consistency bridge between SQLite commit and FAISS drain: an
    #    event is appended BEFORE the worker applies side effects; replay at
    #    startup re-applies anything still pending. Never deleted (Iron Law #5).
    conn.execute(
      """
      CREATE TABLE IF NOT EXISTS event_journal (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        payload TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        applied INTEGER NOT NULL DEFAULT 0 CHECK (applied IN (0,1)),
        applied_at TEXT
      );
      """
    )
    conn.execute(
      "CREATE INDEX IF NOT EXISTS idx_event_journal_pending ON event_journal (applied, id);"
    )


  def _migrate_jsonl_files(self, conn: sqlite3.Connection) -> None:
    data_dir = os.path.dirname(self.path) or "."
    migrations = [
      ("goals.jsonl", "migrated_goals_jsonl", self._upsert_goal_conn),
      ("causal_links.jsonl", "migrated_causal_links_jsonl", self._upsert_causal_link_conn),
      ("predictions.jsonl", "migrated_predictions_jsonl", self._upsert_prediction_conn),
      ("beliefs.jsonl", "migrated_beliefs_jsonl", self._upsert_belief_conn),
    ]
    for filename, meta_key, inserter in migrations:
      if conn.execute("SELECT value FROM meta WHERE key=?", (meta_key,)).fetchone():
        continue
      path = os.path.join(data_dir, filename)
      if not os.path.exists(path):
        conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (meta_key, "missing"))
        continue
      imported = 0
      with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
          if not line.strip():
            continue
          try:
            inserter(conn, json.loads(line))
            imported += 1
          except (json.JSONDecodeError, KeyError, TypeError, sqlite3.Error):
            continue
      conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (meta_key, str(imported)))

  def _upsert_goal_conn(self, conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
      """
      INSERT INTO goals (goal_id, title, priority, status, description, blockers,
                         next_actions, resources, obstacles, progress_markers,
                         created_at, updated_at, version)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(goal_id) DO UPDATE SET
        title=excluded.title, priority=excluded.priority, status=excluded.status,
        description=excluded.description, blockers=excluded.blockers,
        next_actions=excluded.next_actions, resources=excluded.resources,
        obstacles=excluded.obstacles, progress_markers=excluded.progress_markers,
        updated_at=excluded.updated_at, version=excluded.version
      """,
      (
        row["goal_id"], row["title"], row.get("priority", 5), row.get("status", "active"),
        row.get("description", ""), _json(row.get("blockers", [])), _json(row.get("next_actions", [])),
        _json(row.get("resources", [])), _json(row.get("obstacles", [])), _json(row.get("progress_markers", [])),
        row.get("created_at"), row.get("updated_at"), row.get("version", 1),
      ),
    )

  def _upsert_causal_link_conn(self, conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    # R2: name columns explicitly — the table gained provenance columns
    # (derived_from, method) in the cognitive schema, so positional VALUES would
    # silently corrupt or reject rows whenever they are populated.
    conn.execute(
      """
      INSERT INTO causal_links
        (link_id, cause, effect, confidence, evidence, mechanism, observed_count, created_at, derived_from, method)
      VALUES (?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(link_id) DO UPDATE SET
        cause=excluded.cause, effect=excluded.effect, confidence=excluded.confidence,
        evidence=excluded.evidence, mechanism=excluded.mechanism,
        observed_count=excluded.observed_count, derived_from=excluded.derived_from, method=excluded.method
      """,
      (
        row["link_id"], row["cause"], row["effect"], row.get("confidence", 0.5),
        _json(row.get("evidence", [])), row.get("mechanism", ""), row.get("observed_count", 1), row.get("created_at"),
        _json(row.get("derived_from", [])), row.get("method", "llm"),
      ),
    )

  def _upsert_prediction_conn(self, conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
      """
      INSERT INTO predictions VALUES (?,?,?,?,?,?,?,?)
      ON CONFLICT(prediction_id) DO UPDATE SET
        hypothesis=excluded.hypothesis, confidence=excluded.confidence,
        timeframe=excluded.timeframe, conditions=excluded.conditions,
        based_on=excluded.based_on, outcome=excluded.outcome
      """,
      (
        row["prediction_id"], row["hypothesis"], row.get("confidence", 0.5), row.get("timeframe", ""),
        _json(row.get("conditions", [])), _json(row.get("based_on", [])), row.get("outcome", "pending"), row.get("created_at"),
      ),
    )

  def upsert_prediction(self, row: dict[str, Any]) -> None:
    with self._conn() as conn:
      self._upsert_prediction_conn(conn, row)

  async def async_upsert_prediction(self, row: dict[str, Any]) -> None:
    await asyncio.to_thread(self.upsert_prediction, row)

  def list_predictions(self, outcome: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    with self._conn() as conn:
      if outcome:
        rows = conn.execute("SELECT * FROM predictions WHERE outcome=? ORDER BY created_at DESC LIMIT ?", (outcome, limit)).fetchall()
      else:
        rows = conn.execute("SELECT * FROM predictions ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
      return [self._row_prediction(r) for r in rows]

  def get_prediction(self, prediction_id: str) -> dict[str, Any] | None:
    with self._conn() as conn:
      row = conn.execute("SELECT * FROM predictions WHERE prediction_id=?", (prediction_id,)).fetchone()
      return self._row_prediction(row) if row else None

  def _upsert_belief_conn(self, conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    belief_id = row.get("id") or f"belief_{hashlib.sha1(str(row.get('belief', '')).encode('utf-8')).hexdigest()[:10]}"
    conn.execute(
      """
      INSERT INTO beliefs (id, belief, based_on, importance, status, created_at)
      VALUES (?,?,?,?,?,?)
      ON CONFLICT(id) DO UPDATE SET
        belief=excluded.belief, based_on=excluded.based_on,
        importance=excluded.importance, status=excluded.status
      """,
      (belief_id, row["belief"], _json(row.get("based_on", [])), row.get("importance", 6), row.get("status", "active"), row.get("created_at")),
    )


  def batch_insert_facts(self, rows: list[dict[str, Any]]) -> None:
    if not rows:
      return
    tuples = [
      (
        row["id"], row["fact"], row.get("date"), row.get("created_at"),
        row.get("memory_kind", "event"), row.get("importance", 5),
        row.get("confidence", 0.8), row.get("source"), row.get("source_type"),
        json.dumps(row.get("tags", []), ensure_ascii=False),
        row.get("status", "active"), row.get("valid_from"),
        row.get("valid_until"), row.get("schema_version", 1),
        json.dumps(row.get("evidence", []), ensure_ascii=False),
        row.get("facts_sent_count", 0), row.get("facts_used_count", 0),
        row.get("embedding"),
        row.get("domain") or "user",
        json.dumps(row.get("meta") or {}, ensure_ascii=False),
        row.get("last_retrieved_at"), row.get("last_used_at"),
      )
      for row in rows
    ]
    with self._conn() as conn:
      conn.executemany(
        """
        INSERT OR IGNORE INTO facts (
          id, fact, date, created_at, memory_kind, importance, confidence,
          source, source_type, tags, status, valid_from, valid_until,
          schema_version, evidence, facts_sent_count, facts_used_count, embedding,
          domain, meta, last_retrieved_at, last_used_at
        ) VALUES
        (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        tuples,
      )

  def batch_insert_relations(self, rows: list[dict[str, Any]]) -> None:
    if not rows:
      return
    tuples = [
      (
        row["id"], row["from_id"], row["to_id"], row["relation"],
        row.get("created_at"), row.get("reason", ""), row.get("confidence", 0.8),
      )
      for row in rows
    ]
    with self._conn() as conn:
      conn.executemany(
        "INSERT OR IGNORE INTO fact_relations VALUES (?,?,?,?,?,?,?)",
        tuples,
      )

  def batch_insert_messages(self, rows: list[dict[str, Any]]) -> None:
    if not rows:
      return
    tuples = [
      (
        row["id"], row.get("ts"), row.get("role"), row.get("text"),
        row.get("importance", 5), row.get("mode", "default"),
        json.dumps(row.get("signals", []), ensure_ascii=False),
        row.get("user_id"),
      )
      for row in rows
    ]
    with self._conn() as conn:
      conn.executemany(
        "INSERT OR IGNORE INTO messages VALUES (?,?,?,?,?,?,?,?)",
        tuples,
      )

  def batch_insert_reflections(self, rows: list[dict[str, Any]]) -> None:
    if not rows:
      return
    tuples = [
      (
        row["id"], row["insight"],
        json.dumps(row.get("based_on", []), ensure_ascii=False),
        row.get("period"), row.get("importance", 7),
        row.get("confidence", 0.8), row.get("status", "active"),
        row.get("created_at"),
      )
      for row in rows
    ]
    with self._conn() as conn:
      conn.executemany(
        "INSERT OR IGNORE INTO reflections (id, insight, based_on, period, importance, confidence, status, created_at) VALUES (?,?,?,?,?,?,?,?)",
        tuples,
      )

  def batch_insert_beliefs(self, rows: list[dict[str, Any]]) -> None:
    if not rows:
      return
    tuples = [
      (
        row["id"], row["belief"],
        json.dumps(row.get("based_on", []), ensure_ascii=False),
        row.get("importance", 6), row.get("status", "active"),
        row.get("created_at"),
      )
      for row in rows
    ]
    with self._conn() as conn:
      conn.executemany(
        "INSERT OR IGNORE INTO beliefs (id, belief, based_on, importance, status, created_at) VALUES (?,?,?,?,?,?)",
        tuples,
      )

  def _insert_fact(self, row: dict[str, Any]) -> None:
    # ON CONFLICT DO UPDATE вместо INSERT OR IGNORE: иначе при совпадении id
    # (например, повторный импорт/миграция) обновления полей молча терялись.
    with self._conn() as conn:
      conn.execute(
        """
        INSERT INTO facts (
          id, fact, date, created_at, memory_kind, importance, confidence,
          source, source_type, tags, status, valid_from, valid_until,
          schema_version, evidence, facts_sent_count, facts_used_count, embedding,
          category, anchor_flag, manual_lock, archived, updated_at,
          last_accessed, access_count, decay_exempt, domain, meta,
          last_retrieved_at, last_used_at, superseded_by,
          epistemic_class, support_count, contradiction_count
        ) VALUES
        (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
          fact=excluded.fact,
          date=excluded.date,
          memory_kind=excluded.memory_kind,
          importance=excluded.importance,
          confidence=excluded.confidence,
          source=excluded.source,
          source_type=excluded.source_type,
          tags=excluded.tags,
          status=excluded.status,
          valid_from=excluded.valid_from,
          valid_until=excluded.valid_until,
          evidence=excluded.evidence,
          facts_sent_count=excluded.facts_sent_count,
          facts_used_count=excluded.facts_used_count,
          embedding=COALESCE(excluded.embedding, facts.embedding),
          category=COALESCE(excluded.category, facts.category),
          anchor_flag=excluded.anchor_flag,
          manual_lock=excluded.manual_lock,
          archived=excluded.archived,
          updated_at=excluded.updated_at,
          last_accessed=COALESCE(excluded.last_accessed, facts.last_accessed),
          access_count=excluded.access_count,
          decay_exempt=excluded.decay_exempt,
          domain=excluded.domain,
          meta=excluded.meta,
          last_retrieved_at=COALESCE(excluded.last_retrieved_at, facts.last_retrieved_at),
          last_used_at=COALESCE(excluded.last_used_at, facts.last_used_at),
          superseded_by=excluded.superseded_by,
          epistemic_class=COALESCE(excluded.epistemic_class, facts.epistemic_class),
          support_count=excluded.support_count,
          contradiction_count=excluded.contradiction_count
        """,
        (
          row["id"], row["fact"], row.get("date"), row.get("created_at"),
          row.get("memory_kind", "event"), row.get("importance", 5),
          row.get("confidence", 0.8), row.get("source"), row.get("source_type"),
          json.dumps(row.get("tags", []), ensure_ascii=False),
          row.get("status", "active"), row.get("valid_from"),
          row.get("valid_until"), row.get("schema_version", 1),
          json.dumps(row.get("evidence", []), ensure_ascii=False),
          row.get("facts_sent_count", 0), row.get("facts_used_count", 0),
          row.get("embedding"),
          row.get("category", "life"), row.get("anchor_flag", 1 if any(str(t).lower() in {"anchor", "core_identity", "pinned"} for t in row.get("tags", [])) or row.get("memory_kind") == "permanent" else 0),
          row.get("manual_lock", 0), row.get("archived", 1 if row.get("status") == "archived" else 0),
          row.get("updated_at") or row.get("created_at"), row.get("last_accessed"), row.get("access_count", 0), row.get("decay_exempt", 0),
          row.get("domain") or "user",
          json.dumps(row.get("meta") or {}, ensure_ascii=False),
          row.get("last_retrieved_at"), row.get("last_used_at"),
          row.get("superseded_by") or "",
          row.get("epistemic_class", "DIRECT_FACT"),
          row.get("support_count", 0),
          row.get("contradiction_count", 0),
        ),
      )

  def _insert_relation(self, row: dict[str, Any]) -> None:
    with self._conn() as conn:
      conn.execute(
        "INSERT OR IGNORE INTO fact_relations VALUES (?,?,?,?,?,?,?)",
        (
          row["id"], row["from_id"], row["to_id"], row["relation"],
          row.get("created_at"), row.get("reason", ""), row.get("confidence", 0.8),
        ),
      )

  def _insert_message(self, row: dict[str, Any]) -> None:
    with self._conn() as conn:
      conn.execute(
        "INSERT OR IGNORE INTO messages VALUES (?,?,?,?,?,?,?,?)",
        (
          row["id"], row.get("ts"), row.get("role"), row.get("text"),
          row.get("importance", 5), row.get("mode", "default"),
          json.dumps(row.get("signals", []), ensure_ascii=False),
          row.get("user_id"),
        ),
      )

  def _insert_reflection(self, row: dict[str, Any]) -> None:
    with self._conn() as conn:
      conn.execute(
        "INSERT OR IGNORE INTO reflections (id, insight, based_on, period, importance, confidence, status, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (
          row["id"], row["insight"],
          json.dumps(row.get("based_on", []), ensure_ascii=False),
          row.get("period"), row.get("importance", 7),
          row.get("confidence", 0.8), row.get("status", "active"),
          row.get("created_at"),
        ),
      )

  def _insert_belief(self, row: dict[str, Any]) -> None:
    with self._conn() as conn:
      self._upsert_belief_conn(conn, row)

  async def async_insert_belief(self, row: dict[str, Any]) -> None:
    await asyncio.to_thread(self._insert_belief, row)

  def upsert_goal(self, row: dict[str, Any]) -> None:
    with self._conn() as conn:
      self._upsert_goal_conn(conn, row)

  async def async_upsert_goal(self, row: dict[str, Any]) -> None:
    await asyncio.to_thread(self.upsert_goal, row)

  def list_goals(self, status: str | None = None) -> list[dict[str, Any]]:
    with self._conn() as conn:
      if status is None:
        rows = conn.execute("SELECT * FROM goals ORDER BY status='active' DESC, priority DESC, created_at ASC").fetchall()
      else:
        rows = conn.execute(
          "SELECT * FROM goals WHERE status=? ORDER BY priority DESC, created_at ASC",
          (status,),
        ).fetchall()
    return [self._row_goal(r) for r in rows]

  async def async_list_goals(self, status: str | None = None) -> list[dict[str, Any]]:
    return await asyncio.to_thread(self.list_goals, status)

  def update_goal(self, goal_id: str, updates: dict[str, Any], expected_version: int | None = None) -> bool:
    allowed = {
      "title", "priority", "status", "description", "blockers", "next_actions",
      "resources", "obstacles", "progress_markers", "updated_at", "version",
    }
    values = {k: v for k, v in updates.items() if k in allowed}
    if not values:
      return False
    if "updated_at" not in values:
      from datetime import datetime
      values["updated_at"] = datetime.now().isoformat()
    json_cols = {"blockers", "next_actions", "resources", "obstacles", "progress_markers"}
    values.pop("version", None)
    assignments = ", ".join(f"{k}=?" for k in values) + ", version=version+1"
    params = [_json(v) if k in json_cols else v for k, v in values.items()]
    params.append(goal_id)
    with self._conn() as conn:
      if expected_version is not None:
        params.append(expected_version)
        cur = conn.execute(f"UPDATE goals SET {assignments} WHERE goal_id=? AND version=?", params)
        if cur.rowcount == 0:
          row = conn.execute("SELECT version FROM goals WHERE goal_id=?", (goal_id,)).fetchone()
          actual_ver = row[0] if row else None
          raise ConcurrentModificationError(
            f"Concurrent modification on goal {goal_id}: expected version {expected_version}, actual {actual_ver}",
            record_id=goal_id,
            expected_version=expected_version,
            actual_version=actual_ver,
          )
        return True
      else:
        cur = conn.execute(f"UPDATE goals SET {assignments} WHERE goal_id=?", params)
        return cur.rowcount > 0

  async def async_update_goal(self, goal_id: str, updates: dict[str, Any]) -> bool:
    return await asyncio.to_thread(self.update_goal, goal_id, updates)

  def delete_goal(self, goal_id: str) -> bool:
    with self._conn() as conn:
      cur = conn.execute("DELETE FROM goals WHERE goal_id=?", (goal_id,))
      return cur.rowcount > 0

  async def async_delete_goal(self, goal_id: str) -> bool:
    return await asyncio.to_thread(self.delete_goal, goal_id)

  def upsert_causal_link(self, row: dict[str, Any]) -> None:
    with self._conn() as conn:
      self._upsert_causal_link_conn(conn, row)

  async def async_upsert_causal_link(self, row: dict[str, Any]) -> None:
    await asyncio.to_thread(self.upsert_causal_link, row)

  def list_causal_links(self, min_confidence: float = 0.5) -> list[dict[str, Any]]:
    with self._conn() as conn:
      rows = conn.execute(
        "SELECT * FROM causal_links WHERE confidence>=? ORDER BY confidence DESC, created_at DESC",
        (min_confidence,),
      ).fetchall()
    return [self._row_causal_link(r) for r in rows]

  async def async_list_causal_links(self, min_confidence: float = 0.5) -> list[dict[str, Any]]:
    return await asyncio.to_thread(self.list_causal_links, min_confidence)

  def delete_causal_link(self, link_id: str) -> bool:
    with self._conn() as conn:
      cur = conn.execute("DELETE FROM causal_links WHERE link_id=?", (link_id,))
      return cur.rowcount > 0

  async def async_delete_causal_link(self, link_id: str) -> bool:
    return await asyncio.to_thread(self.delete_causal_link, link_id)


  def _row_goal(self, row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for key in ("blockers", "next_actions", "resources", "obstacles", "progress_markers"):
      d[key] = _loads(d.get(key))
    return d

  def _row_causal_link(self, row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["evidence"] = _loads(d.get("evidence"))
    d["derived_from"] = _loads(d.get("derived_from"))
    return d

  def _row_prediction(self, row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for key in ("conditions", "based_on"):
      d[key] = _loads(d.get(key))
    return d

  # --- Memory Genome (K4) -------------------------------------------------

  def upsert_memory_genome(self, row: dict[str, Any]) -> None:
    with self._conn() as conn:
      conn.execute(
        """
        INSERT INTO memory_genome
          (memory_id, origin, generation, parent_memory_id, mutation_history,
           adaptation_log, survival_score, born_at, last_evaluated_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(memory_id) DO UPDATE SET
          generation=excluded.generation,
          parent_memory_id=COALESCE(memory_genome.parent_memory_id, excluded.parent_memory_id),
          mutation_history=excluded.mutation_history,
          survival_score=excluded.survival_score,
          last_evaluated_at=excluded.last_evaluated_at
        """,
        (
          row["memory_id"], row["origin"], row.get("generation", 0),
          row.get("parent_memory_id"), _json(row.get("mutation_history", [])),
          _json(row.get("adaptation_log", [])), float(row.get("survival_score", 0.5)),
          row.get("born_at", ""), row.get("last_evaluated_at"),
        ),
      )

  def get_memory_genome(self, memory_id: str) -> dict[str, Any] | None:
    with self._conn() as conn:
      row = conn.execute("SELECT * FROM memory_genome WHERE memory_id=?", (memory_id,)).fetchone()
      if not row:
        return None
      d = dict(row)
      d["mutation_history"] = _loads(d.get("mutation_history"))
      d["adaptation_log"] = _loads(d.get("adaptation_log"))
      return d

  def count_memory_genome(self) -> int:
    with self._conn() as conn:
      return conn.execute("SELECT COUNT(*) FROM memory_genome").fetchone()[0]

  def facts_missing_genome(self, limit: int = 500) -> list[str]:
    """Backfill hook: active/dormant facts lacking a genome row (1:1 invariant)."""
    with self._conn() as conn:
      rows = conn.execute(
        """
        SELECT f.id FROM facts f
        LEFT JOIN memory_genome g ON g.memory_id = f.id
        WHERE g.memory_id IS NULL AND f.status IN ('active','dormant')
        LIMIT ?
        """,
        (limit,),
      ).fetchall()
      return [r[0] for r in rows]

  # --- Homeostasis metrics (K8) ------------------------------------------

  def insert_homeostasis_metrics(self, *, contradiction_density: float,
                                 stale_ratio: float, null_embedding_ratio: float,
                                 confidence_inflation: float, quarantine_ratio: float,
                                 entropy_score: float) -> None:
    from datetime import datetime
    with self._conn() as conn:
      conn.execute(
        """
        INSERT INTO homeostasis_metrics
          (measured_at, contradiction_density, stale_ratio, null_embedding_ratio,
           confidence_inflation, quarantine_ratio, entropy_score)
        VALUES (?,?,?,?,?,?,?)
        """,
        (
          datetime.now().isoformat(), contradiction_density, stale_ratio,
          null_embedding_ratio, confidence_inflation, quarantine_ratio, entropy_score,
        ),
      )

  def list_homeostasis_metrics(self, limit: int = 10) -> list[dict[str, Any]]:
    with self._conn() as conn:
      rows = conn.execute(
        "SELECT * FROM homeostasis_metrics ORDER BY id DESC LIMIT ?",
        (max(1, int(limit)),),
      ).fetchall()
      return [dict(r) for r in rows]

  # --- Cognitive working memory (K5) ---------------------------------------

  def upsert_working_memory_slot(self, *, user_id: int, slot_type: str,
                                 ref_kind: str, ref_id: str, payload: str,
                                 salience: float, expires_at: str) -> int:
    """Upsert one working-memory slot keyed by (user_id, slot_type, ref_id).

    Refreshing an existing slot renews its freshness (entered_at/expires_at)
    instead of creating a duplicate — the same slot re-mentioning a goal keeps
    one row. Returns the row id.
    """
    from datetime import datetime
    now = datetime.now().isoformat()
    with self._conn() as conn:
      existing = conn.execute(
        """
        SELECT id FROM cognitive_working_memory
        WHERE user_id=? AND slot_type=? AND ref_id=? AND archived=0
        ORDER BY id DESC LIMIT 1
        """,
        (user_id, slot_type, ref_id),
      ).fetchone()
      if existing is not None:
        conn.execute(
          """
          UPDATE cognitive_working_memory
          SET payload=?, salience=?, entered_at=?, expires_at=?
          WHERE id=?
          """,
          (payload, salience, now, expires_at, existing[0]),
        )
        return int(existing[0])
      cur = conn.execute(
        """
        INSERT INTO cognitive_working_memory
          (user_id, slot_type, ref_kind, ref_id, payload, salience, entered_at, expires_at, archived)
        VALUES (?,?,?,?,?,?,?,?,0)
        """,
        (user_id, slot_type, ref_kind, ref_id, payload, salience, now, expires_at),
      )
      return int(cur.lastrowid or 0)

  def archive_expired_working_memory(self) -> int:
    """Flip archived=1 on slots past their expiry. Never deletes (Iron Law #5)."""
    from datetime import datetime
    now = datetime.now().isoformat()
    with self._conn() as conn:
      cur = conn.execute(
        "UPDATE cognitive_working_memory SET archived=1 WHERE archived=0 AND expires_at <= ?",
        (now,),
      )
      return cur.rowcount

  def list_live_working_memory_slots(self, user_id: int, limit: int = 50) -> list[dict[str, Any]]:
    """Live (unexpired, unarchived) slots for a user, highest salience first."""
    from datetime import datetime
    now = datetime.now().isoformat()
    with self._conn() as conn:
      rows = conn.execute(
        """
        SELECT * FROM cognitive_working_memory
        WHERE user_id=? AND archived=0 AND expires_at > ?
        ORDER BY salience DESC, id ASC
        LIMIT ?
        """,
        (user_id, now, max(1, int(limit))),
      ).fetchall()
      return [dict(r) for r in rows]

  def count_live_working_memory_slots(self, user_id: int) -> int:
    from datetime import datetime
    now = datetime.now().isoformat()
    with self._conn() as conn:
      row = conn.execute(
        "SELECT COUNT(*) c FROM cognitive_working_memory WHERE user_id=? AND archived=0 AND expires_at > ?",
        (user_id, now),
      ).fetchone()
      return int(row[0] if row else 0)

  def evict_working_memory_slots(self, user_id: int, keep: int = 50) -> int:
    """Enforce the per-user live-slot cap: archive the lowest-salience overflow.

    Bounded memory (K5): the working set must stay small under 8GB RAM. Called
    by the writer after each turn; only the overflow rows are flipped.
    """
    from datetime import datetime
    now = datetime.now().isoformat()
    with self._conn() as conn:
      overflow = conn.execute(
        """
        SELECT id FROM cognitive_working_memory
        WHERE user_id=? AND archived=0 AND expires_at > ?
        ORDER BY salience ASC, id ASC
        LIMIT -1 OFFSET ?
        """,
        (user_id, now, max(0, int(keep))),
      ).fetchall()
      if not overflow:
        return 0
      conn.executemany(
        "UPDATE cognitive_working_memory SET archived=1 WHERE id=?",
        [(r[0],) for r in overflow],
      )
      return len(overflow)

  # --- Event journal (Phase B) ---------------------------------------------

  def insert_event_journal(self, event_type: str, payload: str) -> int:
    """Append an event to the journal BEFORE side effects are applied.

    The journal is the crash-consistency bridge: publish() writes the row
    first; the bus worker applies the FAISS/SQLite side effects and marks it
    applied. A crash in between leaves the row pending for replay at startup.
    """
    from datetime import datetime
    with self._conn() as conn:
      cur = conn.execute(
        """
        INSERT INTO event_journal (event_type, payload, created_at, applied)
        VALUES (?,?,?,0)
        """,
        (event_type, payload, datetime.now().isoformat()),
      )
      return int(cur.lastrowid or 0)

  def list_pending_event_journal(self, limit: int = 2000) -> list[dict[str, Any]]:
    with self._conn() as conn:
      rows = conn.execute(
        "SELECT id, event_type, payload, created_at FROM event_journal "
        "WHERE applied=0 ORDER BY id ASC LIMIT ?",
        (max(1, int(limit)),),
      ).fetchall()
      return [dict(r) for r in rows]

  def mark_event_journal_applied(self, journal_id: int) -> None:
    from datetime import datetime
    with self._conn() as conn:
      conn.execute(
        "UPDATE event_journal SET applied=1, applied_at=? WHERE id=? AND applied=0",
        (datetime.now().isoformat(), int(journal_id)),
      )

  # --- Council votes (R5: Internal Dialogue consensus) ----------------------

  def insert_council_vote(self, row: dict[str, Any]) -> None:
    with self._conn() as conn:
      conn.execute(
        """
        INSERT INTO council_votes (vote_id, subject_kind, subject_id, role,
                                   verdict, rationale, created_at)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(vote_id) DO NOTHING
        """,
        (
          row["vote_id"], row["subject_kind"], row["subject_id"],
          row["role"], row["verdict"], row.get("rationale", ""),
          row.get("created_at", datetime.now().isoformat()),
        ),
      )

  def list_council_votes(self, subject_kind: str, subject_id: str,
                         limit: int = 20) -> list[dict[str, Any]]:
    with self._conn() as conn:
      rows = conn.execute(
        "SELECT * FROM council_votes WHERE subject_kind=? AND subject_id=? "
        "ORDER BY created_at DESC LIMIT ?",
        (subject_kind, subject_id, max(1, int(limit))),
      ).fetchall()
      return [dict(r) for r in rows]

  # --- Theory of Mind (R6: layered social cognition read-model) -------------

  def insert_tom_claim(self, row: dict[str, Any]) -> int:
    with self._conn() as conn:
      cur = conn.execute(
        """
        INSERT INTO theory_of_mind
          (subject_entity_id, level, claim, epistemic_class, confidence,
           basis_ids, status, created_at, superseded_by)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
          row["subject_entity_id"], int(row["level"]), row["claim"],
          row.get("epistemic_class", "LLM_INFERENCE"),
          float(row.get("confidence", 0.5)),
          _json(row.get("basis_ids", [])),
          row.get("status", "active"),
          row.get("created_at", datetime.now().isoformat()),
          row.get("superseded_by"),
        ),
      )
      return int(cur.lastrowid or 0)

  def list_tom_claims(self, subject_entity_id: str | None = None,
                      level: int | None = None,
                      status: str = "active",
                      limit: int = 100) -> list[dict[str, Any]]:
    query = "SELECT * FROM theory_of_mind"
    conds: list[str] = []
    params: list[Any] = []
    if subject_entity_id:
      conds.append("subject_entity_id=?")
      params.append(subject_entity_id)
    if level is not None:
      conds.append("level=?")
      params.append(int(level))
    if status:
      conds.append("status=?")
      params.append(status)
    if conds:
      query += " WHERE " + " AND ".join(conds)
    query += " ORDER BY confidence DESC, id DESC LIMIT ?"
    params.append(max(1, int(limit)))
    with self._conn() as conn:
      rows = conn.execute(query, params).fetchall()
    res = []
    for r in rows:
      d = dict(r)
      d["basis_ids"] = _loads(d.get("basis_ids"))
      res.append(d)
    return res

  def update_tom_status(self, claim_id: int, status: str,
                        superseded_by: int | None = None) -> None:
    from datetime import datetime
    with self._conn() as conn:
      if superseded_by is not None:
        conn.execute(
          "UPDATE theory_of_mind SET status=?, superseded_by=? WHERE id=?",
          (status, superseded_by, int(claim_id)),
        )
      else:
        conn.execute(
          "UPDATE theory_of_mind SET status=? WHERE id=?",
          (status, int(claim_id)),
        )

  # --- Cognitive timeline (R7: read-model over event_journal) --------------

  def list_journal_after(self, journal_id: int, limit: int = 2000) -> list[dict[str, Any]]:
    """Journal rows strictly above the watermark (timeline materialization)."""
    with self._conn() as conn:
      rows = conn.execute(
        "SELECT id, event_type, payload, created_at FROM event_journal "
        "WHERE id > ? AND applied=1 ORDER BY id ASC LIMIT ?",
        (max(0, int(journal_id)), max(1, int(limit))),
      ).fetchall()
      return [dict(r) for r in rows]

  def insert_timeline_tick(self, *, turn_id: str, user_id: int, phase: str,
                           payload_hash: str, payload: str,
                           created_at: str) -> int:
    with self._conn() as conn:
      cur = conn.execute(
        """
        INSERT INTO cognitive_timeline
          (turn_id, user_id, phase, payload_hash, payload, created_at, archived)
        VALUES (?,?,?,?,?,?,0)
        """,
        (turn_id, int(user_id or 0), phase, payload_hash, payload, created_at),
      )
      return int(cur.lastrowid or 0)

  def archive_timeline_before(self, cutoff_iso: str) -> int:
    """Flag ticks older than cutoff as archived (bounded table, never deletes)."""
    with self._conn() as conn:
      cur = conn.execute(
        "UPDATE cognitive_timeline SET archived=1 WHERE archived=0 AND created_at <= ?",
        (cutoff_iso,),
      )
      return cur.rowcount

  def list_timeline_ticks(self, limit: int = 50,
                          phase: str | None = None) -> list[dict[str, Any]]:
    query = "SELECT * FROM cognitive_timeline WHERE archived=0"
    params: list[Any] = []
    if phase:
      query += " AND phase=?"
      params.append(phase)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, int(limit)))
    with self._conn() as conn:
      rows = conn.execute(query, params).fetchall()
      return [dict(r) for r in rows]



  def get_meta(self, key: str, default: str = "0") -> str:
    with self._conn() as conn:
      row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
      return row["value"] if row else default

  def set_meta(self, key: str, value: str) -> None:
    with self._conn() as conn:
      conn.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
      )
      conn.commit()

  def increment_meta(self, key: str, amount: int = 1) -> int:
    with self._conn() as conn:
      row = conn.execute(
        """
        INSERT INTO meta(key, value) VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value = CAST(CAST(meta.value AS INTEGER) + ? AS TEXT)
        RETURNING value
        """,
        (key, str(amount), amount),
      ).fetchone()
      conn.commit()
      return int(row["value"]) if row else 0

  def list_facts(self, status: str | None = "active") -> list[dict[str, Any]]:
    with self._conn() as conn:
      if status:
        rows = conn.execute(
          "SELECT * FROM facts WHERE status=? ORDER BY date DESC, created_at DESC",
          (status,),
        ).fetchall()
      else:
        rows = conn.execute(
          "SELECT * FROM facts ORDER BY date DESC, created_at DESC"
        ).fetchall()
    return [self._row_fact(r) for r in rows]

  def list_all_facts(self) -> list[dict[str, Any]]:
    return self.list_facts(status=None)  # type: ignore[arg-type]

  def get_fact(self, fact_id: str) -> dict[str, Any] | None:
    with self._conn() as conn:
      row = conn.execute("SELECT * FROM facts WHERE id=?", (fact_id,)).fetchone()
      return self._row_fact(row) if row else None

  def update_fact_status(self, fact_id: str, status: str, expected_version: int | None = None) -> None:
    self.update_fact_fields(fact_id, {"status": status}, expected_version=expected_version)

  def update_fact_fields(self, fact_id: str, fields: dict[str, Any], expected_version: int | None = None) -> None:
    """Update a subset of mutable fact columns atomically."""
    fields = dict(fields)
    if "retrieved_count" in fields and "facts_sent_count" not in fields:
      fields["facts_sent_count"] = fields.pop("retrieved_count")
    if "used_count" in fields and "facts_used_count" not in fields:
      fields["facts_used_count"] = fields.pop("used_count")
    allowed = {
      "fact", "date", "importance", "confidence", "tags", "status",
      "valid_from", "valid_until", "evidence", "version", "superseded_by",
      "memory_kind", "source", "source_type", "anchor_flag", "manual_lock",
      "domain", "meta", "archived", "facts_sent_count", "facts_used_count",
      "epistemic_class", "support_count", "contradiction_count",
    }
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
      return
    if "tags" in sets and not isinstance(sets["tags"], str):
      sets["tags"] = json.dumps(sets["tags"], ensure_ascii=False)
    if "evidence" in sets and not isinstance(sets["evidence"], str):
      sets["evidence"] = json.dumps(sets["evidence"], ensure_ascii=False)
    if "meta" in sets and not isinstance(sets["meta"], str):
      sets["meta"] = json.dumps(sets["meta"] or {}, ensure_ascii=False)
    sets["updated_at"] = fields.get("updated_at") or datetime.now().isoformat()
    if "version" not in sets:
      assignments = ", ".join(f"{k}=?" for k in sets) + ", version=version+1"
    else:
      assignments = ", ".join(f"{k}=?" for k in sets)
    params = list(sets.values()) + [fact_id]
    with self._conn() as conn:
      if "status" in sets:
        row = conn.execute("SELECT status FROM facts WHERE id=?", (fact_id,)).fetchone()
        if row is not None:
          from companion.memory.lifecycle import validate_transition

          validate_transition(str(row["status"]), str(sets["status"]))
      if expected_version is not None:
        params.append(expected_version)
        cursor = conn.execute(f"UPDATE facts SET {assignments} WHERE id=? AND version=?", params)
        if cursor.rowcount == 0:
          row = conn.execute("SELECT version FROM facts WHERE id=?", (fact_id,)).fetchone()
          actual_ver = row[0] if row else None
          raise ConcurrentModificationError(
            f"Concurrent modification on fact {fact_id}: expected version {expected_version}, actual {actual_ver}",
            record_id=fact_id,
            expected_version=expected_version,
            actual_version=actual_ver,
          )
      else:
        conn.execute(f"UPDATE facts SET {assignments} WHERE id=?", params)

  def get_belief(self, belief_id: str) -> dict[str, Any] | None:
    with self._conn() as conn:
      row = conn.execute("SELECT * FROM beliefs WHERE id=?", (belief_id,)).fetchone()
      if not row:
        return None
      d = dict(row)
      d["based_on"] = json.loads(d.get("based_on") or "[]")
      return d

  def get_goal(self, goal_id: str) -> dict[str, Any] | None:
    with self._conn() as conn:
      row = conn.execute("SELECT * FROM goals WHERE goal_id=?", (goal_id,)).fetchone()
      return self._row_goal(row) if row else None

  def get_reflection(self, reflection_id: str) -> dict[str, Any] | None:
    with self._conn() as conn:
      row = conn.execute("SELECT * FROM reflections WHERE id=?", (reflection_id,)).fetchone()
      if not row:
        return None
      d = dict(row)
      d["based_on"] = json.loads(d.get("based_on") or "[]")
      return d

  def get_entity(self, entity_type: str, entity_id: str) -> dict[str, Any] | None:
    et = entity_type.lower()
    if et in ("fact", "facts"):
      return self.get_fact(entity_id)
    elif et in ("belief", "beliefs"):
      return self.get_belief(entity_id)
    elif et in ("goal", "goals"):
      return self.get_goal(entity_id)
    elif et in ("reflection", "reflections"):
      return self.get_reflection(entity_id)
    elif et in ("episode", "episodes"):
      return self.get_episode(entity_id)
    elif et in ("entity", "entities", "world_entity"):
      return self.get_world_entity(entity_id)
    else:
      return self.get_fact(entity_id)

  def _update_generic_entity(
      self,
      table: str,
      id_col: str,
      entity_id: str,
      updates: dict[str, Any],
      allowed: set[str],
      json_cols: set[str],
      expected_version: int | None = None,
  ) -> bool:
    values = {k: v for k, v in updates.items() if k in allowed}
    if not values:
      return False
    values.pop("version", None)
    assignments = ", ".join(f"{k}=?" for k in values) + ", version=version+1"
    params = [json.dumps(v, ensure_ascii=False) if k in json_cols and not isinstance(v, str) else v for k, v in values.items()]
    params.append(entity_id)
    with self._conn() as conn:
      if expected_version is not None:
        params.append(expected_version)
        cur = conn.execute(f"UPDATE {table} SET {assignments} WHERE {id_col}=? AND version=?", params)
        if cur.rowcount == 0:
          row = conn.execute(f"SELECT version FROM {table} WHERE {id_col}=?", (entity_id,)).fetchone()
          actual_ver = row[0] if row else None
          raise ConcurrentModificationError(
            f"Concurrent modification on {table} {entity_id}: expected version {expected_version}, actual {actual_ver}",
            record_id=entity_id,
            expected_version=expected_version,
            actual_version=actual_ver,
          )
        return True
      else:
        cur = conn.execute(f"UPDATE {table} SET {assignments} WHERE {id_col}=?", params)
        return cur.rowcount > 0

  def update_belief(self, belief_id: str, updates: dict[str, Any], expected_version: int | None = None) -> bool:
    allowed = {"belief", "based_on", "importance", "status", "created_at"}
    json_cols = {"based_on"}
    return self._update_generic_entity("beliefs", "id", belief_id, updates, allowed, json_cols, expected_version)

  def update_reflection(self, reflection_id: str, updates: dict[str, Any], expected_version: int | None = None) -> bool:
    allowed = {"insight", "based_on", "period", "importance", "confidence", "status", "created_at"}
    json_cols = {"based_on"}
    return self._update_generic_entity("reflections", "id", reflection_id, updates, allowed, json_cols, expected_version)

  def update_episode(self, episode_id: str, updates: dict[str, Any], expected_version: int | None = None) -> bool:
    allowed = {"title", "narrative", "date", "participants", "emotions", "lesson", "fact_ids", "fact_id", "importance", "confidence"}
    json_cols = {"participants", "emotions", "fact_ids"}
    return self._update_generic_entity("episodes", "id", episode_id, updates, allowed, json_cols, expected_version)

  def update_entity_fields(
      self,
      entity_type: str,
      entity_id: str,
      fields: dict[str, Any],
      expected_version: int | None = None,
  ) -> None:
    et = entity_type.lower()
    if et in ("fact", "facts"):
      self.update_fact_fields(entity_id, fields, expected_version=expected_version)
    elif et in ("goal", "goals"):
      self.update_goal(entity_id, fields, expected_version=expected_version)
    elif et in ("belief", "beliefs"):
      self.update_belief(entity_id, fields, expected_version=expected_version)
    elif et in ("reflection", "reflections"):
      self.update_reflection(entity_id, fields, expected_version=expected_version)
    elif et in ("episode", "episodes"):
      self.update_episode(entity_id, fields, expected_version=expected_version)
    else:
      self.update_fact_fields(entity_id, fields, expected_version=expected_version)

  def get_fact_relations(self, fact_id: str) -> list[dict[str, Any]]:
    with self._conn() as conn:
      rows = conn.execute(
        "SELECT * FROM fact_relations WHERE from_id=? OR to_id=? ORDER BY created_at ASC",
        (fact_id, fact_id),
      ).fetchall()
    return [dict(r) for r in rows]

  def delete_fact(self, fact_id: str) -> bool:
    with self._conn() as conn:
      # Delete the genome row first: it FK-references facts(id), and a hard
      # delete of the fact would otherwise violate the FK. Genome is 1:1 with
      # the fact, so removing it here is the delete-path equivalent of the
      # relation cleanup below.
      conn.execute("DELETE FROM memory_genome WHERE memory_id=?", (fact_id,))
      cur = conn.execute("DELETE FROM facts WHERE id=?", (fact_id,))
      conn.execute("DELETE FROM fact_relations WHERE from_id=? OR to_id=?", (fact_id, fact_id))
      return cur.rowcount > 0

  def _row_fact(self, row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["tags"] = json.loads(d.get("tags") or "[]")
    d["evidence"] = json.loads(d.get("evidence") or "[]")
    d["meta"] = _loads(d.get("meta"), default={})
    if not isinstance(d["meta"], dict):
      d["meta"] = {}
    if not d.get("domain"):
      d["domain"] = "user"
    return d

  def list_messages(
    self, min_importance: int = 0, limit: int | None = None
  ) -> list[dict[str, Any]]:
    # Все user-данные — только через ? плейсхолдеры (кавычки, апострофы, SQL в тексте).
    q = "SELECT * FROM messages WHERE importance>=? ORDER BY ts DESC"
    params: list[Any] = [min_importance]
    if limit is not None:
      q += " LIMIT ?"
      params.append(max(0, int(limit)))
    with self._conn() as conn:
      rows = conn.execute(q, tuple(params)).fetchall()
    result = []
    for r in rows:
      d = dict(r)
      d["signals"] = json.loads(d.get("signals") or "[]")
      result.append(d)
    return result

  def list_reflections(self, status: str = "active") -> list[dict[str, Any]]:
    with self._conn() as conn:
      rows = conn.execute(
        "SELECT * FROM reflections WHERE status=? ORDER BY created_at DESC",
        (status,),
      ).fetchall()
    result = []
    for r in rows:
      d = dict(r)
      d["based_on"] = json.loads(d.get("based_on") or "[]")
      result.append(d)
    return result

  def list_patterns(self, status: str = "active") -> list[dict[str, Any]]:
    with self._conn() as conn:
      if status is None:
        rows = conn.execute(
          "SELECT * FROM patterns ORDER BY importance DESC, created_at DESC"
        ).fetchall()
      else:
        rows = conn.execute(
          "SELECT * FROM patterns WHERE status=? ORDER BY importance DESC, created_at DESC",
          (status,),
        ).fetchall()
    result = []
    for r in rows:
      d = dict(r)
      d["evidence"] = json.loads(d.get("evidence") or "[]")
      result.append(d)
    return result

  def add_pattern(self, row: dict[str, Any]) -> None:
    with self._conn() as conn:
      conn.execute(
        """INSERT OR IGNORE INTO patterns
           (id, pattern, category, evidence, importance, confidence, status, created_at, version, superseded_by, last_confirmed_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
          row["id"], row.get("pattern"), row.get("category", "behavior"),
          json.dumps(row.get("evidence", []), ensure_ascii=False),
          row.get("importance", 6), row.get("confidence", 0.7),
          row.get("status", "active"), row.get("created_at"),
          row.get("version", 1), row.get("superseded_by", ""),
          row.get("last_confirmed_at") or row.get("created_at"),
        ),
      )

  def update_pattern_status(self, pattern_id: str, status: str) -> None:
    with self._conn() as conn:
      conn.execute("UPDATE patterns SET status=? WHERE id=?", (status, pattern_id))

  def update_pattern_fields(self, pattern_id: str, fields: dict[str, Any]) -> None:
    allowed = {"pattern", "category", "importance", "confidence", "status", "evidence", "version", "superseded_by", "last_confirmed_at"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
      return
    if "evidence" in sets:
      sets["evidence"] = json.dumps(sets["evidence"], ensure_ascii=False)
    sets["updated_at"] = datetime.now().isoformat()
    assignments = ", ".join(f"{k}=?" for k in sets)
    params = list(sets.values()) + [pattern_id]
    with self._conn() as conn:
      conn.execute(f"UPDATE patterns SET {assignments} WHERE id=?", params)

  def delete_pattern(self, pattern_id: str) -> bool:
    with self._conn() as conn:
      cur = conn.execute("DELETE FROM patterns WHERE id=?", (pattern_id,))
      return cur.rowcount > 0

  # ── Communication prefs (Уровень 4) ──────────────────────────────

  def get_comm_pref(self, pref_id: str = "global") -> dict[str, Any] | None:
    with self._conn() as conn:
      row = conn.execute(
        "SELECT * FROM communication_prefs WHERE id=?", (pref_id,)
      ).fetchone()
    if row is None:
      return None
    d = dict(row)
    d["liked_topics"] = json.loads(d.get("liked_topics") or "[]")
    d["avoided_topics"] = json.loads(d.get("avoided_topics") or "[]")
    return d

  def upsert_comm_pref(self, row: dict[str, Any]) -> None:
    liked = row.get("liked_topics", [])
    avoided = row.get("avoided_topics", [])
    liked_json = json.dumps(liked, ensure_ascii=False) if not isinstance(liked, str) else liked
    avoided_json = json.dumps(avoided, ensure_ascii=False) if not isinstance(avoided, str) else avoided
    with self._conn() as conn:
      conn.execute(
        """
        INSERT INTO communication_prefs
          (id, style, formality, humor, language, liked_topics, avoided_topics, updated_at, version)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
          style=excluded.style, formality=excluded.formality, humor=excluded.humor,
          language=excluded.language, liked_topics=excluded.liked_topics,
          avoided_topics=excluded.avoided_topics, updated_at=excluded.updated_at,
          version=excluded.version
        """,
        (
          row.get("id", "global"),
          row.get("style", ""),
          row.get("formality", ""),
          row.get("humor", ""),
          row.get("language", ""),
          liked_json,
          avoided_json,
          row.get("updated_at"),
          int(row.get("version", 1)),
        ),
      )

  # ── Human model (Уровень 6) ────────────────────────────────────

  def get_human_model(self, model_id: str = "global") -> dict[str, Any] | None:
    with self._conn() as conn:
      row = conn.execute(
        "SELECT * FROM human_model WHERE id=?", (model_id,)
      ).fetchone()
    if row is None:
      return None
    d = dict(row)
    for k in ("goals", "fears", "strengths", "recurring_mistakes", "long_term_trends"):
      d[k] = json.loads(d.get(k) or "[]")
    return d

  def upsert_human_model(self, row: dict[str, Any]) -> None:
    _js = lambda v: json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    with self._conn() as conn:
      conn.execute(
        """
        INSERT INTO human_model
          (id, goals, fears, strengths, recurring_mistakes, long_term_trends, updated_at, version)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
          goals=excluded.goals, fears=excluded.fears, strengths=excluded.strengths,
          recurring_mistakes=excluded.recurring_mistakes, long_term_trends=excluded.long_term_trends,
          updated_at=excluded.updated_at, version=excluded.version
        """,
        (
          row.get("id", "global"),
          _js(row.get("goals", [])),
          _js(row.get("fears", [])),
          _js(row.get("strengths", [])),
          _js(row.get("recurring_mistakes", [])),
          _js(row.get("long_term_trends", [])),
          row.get("updated_at"),
          int(row.get("version", 1)),
        ),
      )

  # ── Life Continuity Engine (LCE) ────────────────────────────────

  def add_life_transition(self, row: dict[str, Any]) -> None:
    _js = lambda v: json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    with self._conn() as conn:
      conn.execute(
        """
        INSERT OR IGNORE INTO life_transitions
          (id, domain, from_state, to_state, explanation, trigger_events,
           confidence, importance, status, created_at, last_confirmed_at, version)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
          row.get("id"),
          row.get("domain", "identity"),
          row.get("from_state", ""),
          row.get("to_state", ""),
          row.get("explanation", ""),
          _js(row.get("trigger_events", [])),
          float(row.get("confidence", 0.7)),
          int(row.get("importance", 6)),
          row.get("status", "active"),
          row.get("created_at"),
          row.get("last_confirmed_at"),
          int(row.get("version", 1)),
        ),
      )

  def update_life_transition(self, transition_id: str, fields: dict[str, Any]) -> None:
    allowed = {
      "domain", "from_state", "to_state", "explanation", "trigger_events",
      "confidence", "importance", "status", "last_confirmed_at", "version",
    }
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
      return
    if "trigger_events" in sets and not isinstance(sets["trigger_events"], str):
      sets["trigger_events"] = json.dumps(sets["trigger_events"], ensure_ascii=False)
    assignments = ", ".join(f"{k}=?" for k in sets)
    params = list(sets.values()) + [transition_id]
    with self._conn() as conn:
      conn.execute(f"UPDATE life_transitions SET {assignments} WHERE id=?", params)

  def get_life_transition(self, transition_id: str) -> dict[str, Any] | None:
    with self._conn() as conn:
      row = conn.execute(
        "SELECT * FROM life_transitions WHERE id=?", (transition_id,)
      ).fetchone()
    if row is None:
      return None
    d = dict(row)
    d["trigger_events"] = json.loads(d.get("trigger_events") or "[]")
    return d

  def list_life_transitions(self, status: str | None = None) -> list[dict[str, Any]]:
    with self._conn() as conn:
      if status is None:
        rows = conn.execute(
          "SELECT * FROM life_transitions ORDER BY importance DESC, created_at DESC"
        ).fetchall()
      else:
        rows = conn.execute(
          "SELECT * FROM life_transitions WHERE status=? ORDER BY importance DESC, created_at DESC",
          (status,),
        ).fetchall()
    result = []
    for r in rows:
      d = dict(r)
      d["trigger_events"] = json.loads(d.get("trigger_events") or "[]")
      result.append(d)
    return result

  def delete_life_transition(self, transition_id: str) -> bool:
    with self._conn() as conn:
      cur = conn.execute("DELETE FROM life_transitions WHERE id=?", (transition_id,))
      return cur.rowcount > 0

  def list_beliefs(self, status: str = "active") -> list[dict[str, Any]]:
    with self._conn() as conn:
      rows = conn.execute(
        "SELECT * FROM beliefs WHERE status=? ORDER BY importance DESC",
        (status,),
      ).fetchall()
    result = []
    for r in rows:
      d = dict(r)
      d["based_on"] = json.loads(d.get("based_on") or "[]")
      result.append(d)
    return result

  def count_messages(self) -> int:
    with self._conn() as conn:
      return conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

  # ── Session persistence ────────────────────────────────────────────

  def save_session(self, user_id: int, message_count: int) -> None:
    from datetime import datetime
    with self._conn() as conn:
      conn.execute(
        """INSERT INTO sessions (user_id, message_count, last_active)
           VALUES (?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
             message_count=excluded.message_count,
             last_active=excluded.last_active""",
        (user_id, message_count, datetime.now().isoformat()),
      )

  def load_sessions(self) -> dict[int, int]:
    with self._conn() as conn:
      rows = conn.execute(
        "SELECT user_id, message_count FROM sessions ORDER BY last_active DESC"
      ).fetchall()
    return {r["user_id"]: r["message_count"] for r in rows}

  def count_facts(self, status: str | None = None) -> int:
    with self._conn() as conn:
      if status:
        return conn.execute(
          "SELECT COUNT(*) FROM facts WHERE status=?", (status,)
        ).fetchone()[0]
      return conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]

  def insert_retrieval_metrics(
    self,
    message_id: str,
    facts_sent: int,
    facts_used: int,
    goals_sent: int,
    goals_used: int,
    reflections_sent: int,
    reflections_used: int,
  ) -> None:
    from datetime import datetime
    with self._conn() as conn:
      conn.execute(
        """
        INSERT OR REPLACE INTO retrieval_metrics 
        (message_id, timestamp, facts_sent, facts_used, goals_sent, goals_used, reflections_sent, reflections_used)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (
          message_id,
          datetime.now().isoformat(),
          facts_sent,
          facts_used,
          goals_sent,
          goals_used,
          reflections_sent,
          reflections_used,
        ),
      )

  def save_retrieval_replay(self, replay_id: str, user_id: int, payload: str) -> None:
    with self._conn() as conn:
      conn.execute(
        "INSERT OR REPLACE INTO retrieval_replays(replay_id, user_id, created_at, payload) "
        "VALUES (?, ?, datetime('now'), ?)",
        (replay_id, user_id, payload),
      )

  def get_retrieval_replay(self, replay_id: str) -> dict[str, Any] | None:
    with self._conn() as conn:
      row = conn.execute(
        "SELECT replay_id, user_id, created_at, payload FROM retrieval_replays WHERE replay_id=?",
        (replay_id,),
      ).fetchone()
    return dict(row) if row else None

  def list_retrieval_replays(self, user_id: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
    with self._conn() as conn:
      if user_id is None:
        rows = conn.execute(
          "SELECT replay_id, user_id, created_at, payload FROM retrieval_replays "
          "ORDER BY created_at DESC LIMIT ?",
          (max(1, int(limit)),),
        ).fetchall()
      else:
        rows = conn.execute(
          "SELECT replay_id, user_id, created_at, payload FROM retrieval_replays "
          "WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
          (user_id, max(1, int(limit))),
        ).fetchall()
    return [dict(row) for row in rows]

  def update_retrieval_replay_payload(self, replay_id: str, payload: str) -> bool:
    with self._conn() as conn:
      cursor = conn.execute(
        "UPDATE retrieval_replays SET payload=? WHERE replay_id=?",
        (payload, replay_id),
      )
    return cursor.rowcount > 0

  def save_event(self, id_: str, date: str, event: str, importance: int, description: str) -> None:
    with self._conn() as conn:
      conn.execute(
        "INSERT OR IGNORE INTO timeline (id, date, event, importance, description) VALUES (?,?,?,?,?)",
        (id_, date, event, importance, description),
      )

  def load_events(self, year: int | None = None) -> list[dict[str, Any]]:
    with self._conn() as conn:
      if year is not None:
        rows = conn.execute(
          "SELECT * FROM timeline WHERE date LIKE ? ORDER BY date ASC",
          (f"{year}%",),
        ).fetchall()
      else:
        rows = conn.execute(
          "SELECT * FROM timeline ORDER BY date ASC"
        ).fetchall()
    return [dict(r) for r in rows]

  # ── Episodes (эпизодическая память) ───────────────────────────────

  def upsert_episode(self, row: dict[str, Any]) -> None:
    with self._conn() as conn:
      conn.execute(
        """
        INSERT INTO episodes (id, title, narrative, date, participants, emotions,
                              lesson, fact_ids, fact_id, importance, confidence, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
          title=excluded.title, narrative=excluded.narrative, date=excluded.date,
          participants=excluded.participants, emotions=excluded.emotions,
          lesson=excluded.lesson, fact_ids=excluded.fact_ids, fact_id=excluded.fact_id,
          importance=excluded.importance, confidence=excluded.confidence
        """,
        (
          row["id"], row["title"], row["narrative"], row.get("date"),
          _json(row.get("participants", [])), _json(row.get("emotions", {})),
          row.get("lesson", ""), _json(row.get("fact_ids", [])),
          row.get("fact_id", ""), row.get("importance", 7),
          row.get("confidence", 0.8), row.get("created_at"),
        ),
      )

  def _row_episode(self, row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["participants"] = _loads(d.get("participants"))
    d["emotions"] = _loads(d.get("emotions"), default={})
    d["fact_ids"] = _loads(d.get("fact_ids"))
    return d

  def list_episodes(self, limit: int | None = None) -> list[dict[str, Any]]:
    q = "SELECT * FROM episodes ORDER BY date DESC, created_at DESC"
    params: tuple = ()
    if limit is not None:
      q += " LIMIT ?"
      params = (max(0, int(limit)),)
    with self._conn() as conn:
      rows = conn.execute(q, params).fetchall()
    return [self._row_episode(r) for r in rows]

  def get_episode(self, episode_id: str) -> dict[str, Any] | None:
    with self._conn() as conn:
      row = conn.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
    return self._row_episode(row) if row else None

  def increment_fact_usage(self, fact_id: str, used: bool = False) -> None:
    now_iso = datetime.now().isoformat()
    with self._conn() as conn:
      if used:
        conn.execute(
          "UPDATE facts SET facts_sent_count = facts_sent_count + 1, facts_used_count = facts_used_count + 1, last_retrieved_at = ?, last_used_at = ? WHERE id=?",
          (now_iso, now_iso, fact_id),
        )
      else:
        conn.execute(
          "UPDATE facts SET facts_sent_count = facts_sent_count + 1, last_retrieved_at = ? WHERE id=?",
          (now_iso, fact_id),
        )

  def increment_fact_usage_batch(self, sent_ids: list[str], used_ids: list[str]) -> None:
    if not sent_ids and not used_ids:
      return
    now_iso = datetime.now().isoformat()
    with self._conn() as conn:
      if sent_ids:
        sent_only = [i for i in sent_ids if i not in used_ids]
        if sent_only:
          conn.executemany(
            "UPDATE facts SET facts_sent_count = facts_sent_count + 1, last_retrieved_at = ? WHERE id=?",
            [(now_iso, i) for i in sent_only],
          )
      if used_ids:
        conn.executemany(
          "UPDATE facts SET facts_sent_count = facts_sent_count + 1, facts_used_count = facts_used_count + 1, last_retrieved_at = ?, last_used_at = ? WHERE id=?",
          [(now_iso, now_iso, i) for i in used_ids],
        )

  def hydrate_fact_metadata(self, fact_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not fact_ids:
      return {}
    unique_ids = list(dict.fromkeys(fact_ids))
    result: dict[str, dict[str, Any]] = {}
    # R7: chunk the IN clause — SQLite's variable limit (32766) caps how many
    # ids can ride in a single statement; chunk at 500 for safety.
    _CHUNK = 500
    with self._conn() as conn:
      for start in range(0, len(unique_ids), _CHUNK):
        chunk = unique_ids[start:start + _CHUNK]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
          f"""
          SELECT id, importance, category, anchor_flag, manual_lock, archived,
                 created_at, date, updated_at, last_accessed, access_count, decay_exempt,
                 memory_kind, tags, status, facts_sent_count
          FROM facts
          WHERE id IN ({placeholders})
          """,
          chunk,
        ).fetchall()
        for r in rows:
          result[r["id"]] = self._row_fact(r)
    return result

  def record_fact_access_batch(self, fact_scores: list[tuple[str, float, float]], query_hash: str | None = None, retrieval_reason: str = "semantic") -> None:
    if not fact_scores:
      return
    with self._conn() as conn:
      conn.executemany(
        """
        UPDATE facts
        SET access_count = COALESCE(access_count, 0) + 1,
            last_accessed = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE id=? AND COALESCE(archived, 0)=0
        """,
        [(fact_id,) for fact_id, _, _ in fact_scores],
      )
      conn.executemany(
        """
        INSERT INTO memory_access_log(fact_id, query_hash, vector_score, final_score, source, retrieval_reason)
        VALUES(?,?,?,?, 'rag', ?)
        """,
        [(fact_id, query_hash, vector_score, final_score, retrieval_reason) for fact_id, vector_score, final_score in fact_scores],
      )

  def create_temporal_counter(self, counter_name: str, description: str, start_date: str, timezone: str) -> int:
    with self._conn() as conn:
      row = conn.execute(
        """
        INSERT INTO temporal_counters(counter_name, description, start_date, timezone, status)
        VALUES(?,?,?,?, 'active')
        ON CONFLICT(counter_name) DO UPDATE SET
          description=excluded.description,
          start_date=excluded.start_date,
          timezone=excluded.timezone,
          status='active',
          archived=0,
          deleted_at=NULL,
          updated_at=CURRENT_TIMESTAMP
        RETURNING id
        """,
        (counter_name, description, start_date, timezone),
      ).fetchone()
      return int(row["id"])

  def update_temporal_counter(self, counter_name: str, *, description: str | None = None, status: str | None = None, archived: bool | None = None) -> bool:
    updates: list[str] = []
    params: list[Any] = []
    if description is not None:
      updates.append("description=?")
      params.append(description)
    if status is not None:
      updates.append("status=?")
      params.append(status)
    if archived is not None:
      updates.append("archived=?")
      params.append(1 if archived else 0)
    if not updates:
      return False
    updates.append("updated_at=CURRENT_TIMESTAMP")
    params.append(counter_name)
    with self._conn() as conn:
      cur = conn.execute(f"UPDATE temporal_counters SET {', '.join(updates)} WHERE counter_name=? AND deleted_at IS NULL", params)
      return cur.rowcount > 0

  def delete_temporal_counter(self, counter_name: str) -> bool:
    with self._conn() as conn:
      cur = conn.execute(
        "UPDATE temporal_counters SET deleted_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE counter_name=?",
        (counter_name,),
      )
      return cur.rowcount > 0

  def pause_temporal_counter(self, counter_name: str, pause_date: str, reason: str | None = None) -> None:
    with self._conn() as conn:
      row = conn.execute("SELECT id FROM temporal_counters WHERE counter_name=? AND deleted_at IS NULL", (counter_name,)).fetchone()
      if not row:
        raise KeyError(f"counter not found: {counter_name}")
      counter_id = int(row["id"])
      if conn.execute("SELECT 1 FROM temporal_counter_pauses WHERE counter_id=? AND pause_end_date IS NULL", (counter_id,)).fetchone():
        raise ValueError("counter is already paused")
      conn.execute(
        "INSERT INTO temporal_counter_pauses(counter_id, pause_start_date, reason) VALUES(?,?,?)",
        (counter_id, pause_date, reason),
      )
      conn.execute("UPDATE temporal_counters SET status='paused', updated_at=CURRENT_TIMESTAMP WHERE id=?", (counter_id,))

  def resume_temporal_counter(self, counter_name: str, resume_date: str) -> None:
    with self._conn() as conn:
      row = conn.execute("SELECT id FROM temporal_counters WHERE counter_name=? AND deleted_at IS NULL", (counter_name,)).fetchone()
      if not row:
        raise KeyError(f"counter not found: {counter_name}")
      cur = conn.execute(
        "UPDATE temporal_counter_pauses SET pause_end_date=? WHERE counter_id=? AND pause_end_date IS NULL",
        (resume_date, int(row["id"])),
      )
      if cur.rowcount == 0:
        raise ValueError("counter is not paused")
      conn.execute("UPDATE temporal_counters SET status='active', updated_at=CURRENT_TIMESTAMP WHERE id=?", (int(row["id"]),))

  def list_temporal_counters(self) -> list[dict[str, Any]]:
    with self._conn() as conn:
      rows = conn.execute(
        """
        SELECT * FROM temporal_counters
        WHERE deleted_at IS NULL AND archived=0 AND status IN ('active', 'paused')
        ORDER BY counter_name ASC
        """
      ).fetchall()
    return [dict(r) for r in rows]

  def list_temporal_counter_pauses(self, counter_id: int) -> list[dict[str, Any]]:
    with self._conn() as conn:
      rows = conn.execute(
        "SELECT * FROM temporal_counter_pauses WHERE counter_id=? ORDER BY pause_start_date ASC",
        (counter_id,),
      ).fetchall()
    return [dict(r) for r in rows]

  def _insert_summary(self, row: dict[str, Any]) -> None:
    with self._conn() as conn:
      conn.execute(
        "INSERT OR IGNORE INTO summaries (id, content, created_at, embedding_hash, status) VALUES (?,?,?,?,?)",
        (row["id"], row["content"], row.get("created_at"), row.get("embedding_hash"), row.get("status", "active")),
      )

  def list_summaries(self, status: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    with self._conn() as conn:
      q = "SELECT * FROM summaries"
      params = []
      if status:
        q += " WHERE status=?"
        params.append(status)
      q += " ORDER BY created_at DESC"
      if limit:
        q += " LIMIT ?"
        params.append(limit)
      rows = conn.execute(q, tuple(params)).fetchall()
    return [dict(r) for r in rows]
    
  def update_summary_status(self, summary_id: str, status: str) -> None:
    with self._conn() as conn:
      conn.execute("UPDATE summaries SET status=? WHERE id=?", (status, summary_id))

  def list_permanent_notes(self) -> list[str]:
    with self._conn() as conn:
      rows = conn.execute(
        "SELECT fact FROM facts WHERE memory_kind='permanent' AND status='active' ORDER BY created_at ASC"
      ).fetchall()
    return [r["fact"] for r in rows]

  async def async_list_permanent_notes(self) -> list[str]:
    return await asyncio.to_thread(self.list_permanent_notes)


  def save_monthbook(self, ym: str, content: str) -> None:
    from datetime import datetime
    with self._conn() as conn:
      conn.execute(
        "INSERT INTO monthbooks(ym,content,updated_at) VALUES(?,?,?) ON CONFLICT(ym) DO UPDATE SET content=excluded.content, updated_at=excluded.updated_at",
        (ym, content, datetime.now().isoformat()),
      )

  async def async_save_monthbook(self, ym: str, content: str) -> None:
    await asyncio.to_thread(self.save_monthbook, ym, content)

  def load_monthbook(self, ym: str) -> str:
    with self._conn() as conn:
      row = conn.execute("SELECT content FROM monthbooks WHERE ym=?", (ym,)).fetchone()
      return row["content"] if row else ""

  async def async_load_monthbook(self, ym: str) -> str:
    return await asyncio.to_thread(self.load_monthbook, ym)

  def upsert_prospective_task(self, row: dict[str, Any]) -> None:
    with self._conn() as conn:
      conn.execute(
        """
        INSERT INTO prospective_tasks(task_id,text,due_ts,status,source_message_id,created_at,triggered_at,metadata)
        VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(task_id) DO UPDATE SET
          text=excluded.text, due_ts=excluded.due_ts, status=excluded.status,
          source_message_id=excluded.source_message_id, metadata=excluded.metadata
        """,
        (
          row["task_id"], row["text"], row["due_ts"], row.get("status", "pending"),
          row.get("source_message_id"), row.get("created_at"), row.get("triggered_at"),
          _json(row.get("metadata", {})),
        ),
      )

  async def async_upsert_prospective_task(self, row: dict[str, Any]) -> None:
    await asyncio.to_thread(self.upsert_prospective_task, row)

  def due_prospective_tasks(self, now_ts: float, limit: int = 5) -> list[dict[str, Any]]:
    with self._conn() as conn:
      rows = conn.execute(
        "SELECT * FROM prospective_tasks WHERE status='pending' AND due_ts<=? ORDER BY due_ts ASC LIMIT ?",
        (now_ts, limit),
      ).fetchall()
    return [self._row_prospective_task(r) for r in rows]

  async def async_due_prospective_tasks(self, now_ts: float, limit: int = 5) -> list[dict[str, Any]]:
    return await asyncio.to_thread(self.due_prospective_tasks, now_ts, limit)

  def mark_prospective_task_triggered(self, task_id: str) -> bool:
    from datetime import datetime
    with self._conn() as conn:
      cur = conn.execute(
        "UPDATE prospective_tasks SET status='triggered', triggered_at=? WHERE task_id=? AND status='pending'",
        (datetime.now().isoformat(), task_id),
      )
      return cur.rowcount > 0

  async def async_mark_prospective_task_triggered(self, task_id: str) -> bool:
    return await asyncio.to_thread(self.mark_prospective_task_triggered, task_id)

  def _row_prospective_task(self, row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["metadata"] = _loads(d.get("metadata"), {})
    return d

  async def async_batch_insert_facts(self, rows: list[dict[str, Any]]) -> None:
    await asyncio.to_thread(self.batch_insert_facts, rows)

  async def async_batch_insert_relations(self, rows: list[dict[str, Any]]) -> None:
    await asyncio.to_thread(self.batch_insert_relations, rows)

  async def async_batch_insert_messages(self, rows: list[dict[str, Any]]) -> None:
    await asyncio.to_thread(self.batch_insert_messages, rows)

  async def async_batch_insert_reflections(self, rows: list[dict[str, Any]]) -> None:
    await asyncio.to_thread(self.batch_insert_reflections, rows)

  async def async_batch_insert_beliefs(self, rows: list[dict[str, Any]]) -> None:
    await asyncio.to_thread(self.batch_insert_beliefs, rows)

  # --- MIGRATION REPOSITORY METHODS ---

  def get_state_model(self, model_type: str) -> dict[str, Any]:
    with self._conn() as conn:
      row = conn.execute("SELECT payload_json FROM state_models WHERE model_type=?", (model_type,)).fetchone()
      if row:
        return _loads(row["payload_json"], {})
      return {}

  async def async_get_state_model(self, model_type: str) -> dict[str, Any]:
    return await asyncio.to_thread(self.get_state_model, model_type)

  def save_state_model(self, model_type: str, data: dict[str, Any]) -> None:
    with self._conn() as conn:
      conn.execute(
        "INSERT INTO state_models(model_type, payload_json) VALUES(?, ?) ON CONFLICT(model_type) DO UPDATE SET payload_json=excluded.payload_json, last_updated=CURRENT_TIMESTAMP",
        (model_type, _json(data))
      )

  async def async_save_state_model(self, model_type: str, data: dict[str, Any]) -> None:
    await asyncio.to_thread(self.save_state_model, model_type, data)

  def get_meta(self, key: str, default: str = "") -> str:
    with self._conn() as conn:
      row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
      if row:
        return row["value"]
      return default

  async def async_get_meta(self, key: str, default: str = "") -> str:
    return await asyncio.to_thread(self.get_meta, key, default)

  def set_meta(self, key: str, value: str) -> None:
    with self._conn() as conn:
      conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value)
      )

  async def async_set_meta(self, key: str, value: str) -> None:
    await asyncio.to_thread(self.set_meta, key, value)

  def add_shared_lore_candidate(self, cand: dict[str, Any]) -> None:
    with self._conn() as conn:
      conn.execute(
        "INSERT INTO shared_lore_candidates(candidate_phrase, context) VALUES(?, ?)",
        (cand.get("candidate_phrase", ""), _json(cand))
      )

  async def async_add_shared_lore_candidate(self, cand: dict[str, Any]) -> None:
    await asyncio.to_thread(self.add_shared_lore_candidate, cand)

  def get_faiss_mapping(self) -> dict[str, str]:
    with self._conn() as conn:
      rows = conn.execute("SELECT faiss_id, fact_id FROM faiss_mapping").fetchall()
      return {str(r["faiss_id"]): r["fact_id"] for r in rows}

  async def async_get_faiss_mapping(self) -> dict[str, str]:
    return await asyncio.to_thread(self.get_faiss_mapping)

  def save_faiss_mapping(self, mapping: dict[str, str]) -> None:
    tuples = [(int(k), v) for k, v in mapping.items()]
    with self._conn() as conn:
      conn.execute("DELETE FROM faiss_mapping")
      if tuples:
        conn.executemany("INSERT INTO faiss_mapping(faiss_id, fact_id) VALUES(?, ?)", tuples)

  async def async_save_faiss_mapping(self, mapping: dict[str, str]) -> None:
    await asyncio.to_thread(self.save_faiss_mapping, mapping)

  def log_mutation(
    self,
    entity_id: str,
    action: str,
    reason: str,
    state_before: dict[str, Any] | str,
    state_after: dict[str, Any] | str,
    entity_type: str = "fact",
    initiator: str = "governor",
  ) -> str:
    """Record a structural mutation in memory_mutation_log."""
    mut_id = f"mut_{uuid.uuid4().hex[:12]}"
    now_iso = datetime.now().isoformat()
    before_str = json.dumps(state_before, ensure_ascii=False) if isinstance(state_before, dict) else str(state_before)
    after_str = json.dumps(state_after, ensure_ascii=False) if isinstance(state_after, dict) else str(state_after)
    with self._conn() as conn:
      conn.execute(
        """
        INSERT INTO memory_mutation_log (
          id, timestamp, entity_id, entity_type, action, reason, state_before, state_after, initiator
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (mut_id, now_iso, entity_id, entity_type, action, reason, before_str, after_str, initiator),
      )
    return mut_id

  def list_mutations(
    self,
    entity_id: str | None = None,
    limit: int = 100,
  ) -> list[dict[str, Any]]:
    """List memory mutations ordered by timestamp DESC."""
    query = "SELECT * FROM memory_mutation_log"
    params: list[Any] = []
    if entity_id:
      query += " WHERE entity_id = ?"
      params.append(entity_id)
    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)
    with self._conn() as conn:
      rows = conn.execute(query, params).fetchall()
      res = []
      for r in rows:
        d = dict(r)
        d["state_before"] = _loads(d.get("state_before"), default=d.get("state_before"))
        d["state_after"] = _loads(d.get("state_after"), default=d.get("state_after"))
        res.append(d)
      return res

  # --- World Model (Entity Graph & Relationship Layer) CRUD ---

  def upsert_world_entity(self, entity: dict[str, Any], expected_version: int | None = None) -> str:
    entity_id = str(entity.get("entity_id") or "")
    if not entity_id:
      raise ValueError("entity_id is required for upsert_world_entity")
    now_iso = datetime.now().isoformat()
    _js = lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else str(v)
    existing = self.get_world_entity(entity_id)
    if existing is None:
      version = int(entity.get("version", 1))
      with self._conn() as conn:
        conn.execute(
          """
          INSERT INTO entities (
            entity_id, name, type, importance, version, created_at, updated_at, last_mentioned_at, aliases, summary
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
          """,
          (
            entity_id,
            str(entity.get("name", "")),
            str(entity.get("type", "concept")),
            float(entity.get("importance", 0.5)),
            version,
            str(entity.get("created_at") or now_iso),
            now_iso,
            str(entity.get("last_mentioned_at") or now_iso),
            _js(entity.get("aliases", [])),
            str(entity.get("summary", "")),
          ),
        )
      self.log_mutation(
        entity_id=entity_id,
        action="CREATE",
        reason="Entity creation in World Model",
        state_before={},
        state_after=entity,
        entity_type="entity",
        initiator="world_model",
      )
      return entity_id
    else:
      ver_to_check = expected_version if expected_version is not None else entity.get("version")
      if ver_to_check is not None and int(existing["version"]) != int(ver_to_check):
        raise ConcurrentModificationError(
          f"Concurrent modification on entity {entity_id}: expected version {ver_to_check}, actual {existing['version']}",
          record_id=entity_id,
          expected_version=int(ver_to_check),
          actual_version=int(existing["version"]),
        )
      new_ver = int(existing["version"]) + 1
      name = str(entity.get("name", existing["name"]))
      type_val = str(entity.get("type", existing["type"]))
      importance = float(entity.get("importance", existing["importance"]))
      last_mentioned = str(entity.get("last_mentioned_at", existing.get("last_mentioned_at") or now_iso))
      aliases_val = _js(entity.get("aliases", existing.get("aliases", [])))
      summary_val = str(entity.get("summary", existing.get("summary", "")))
      with self._conn() as conn:
        conn.execute(
          """
          UPDATE entities
          SET name=?, type=?, importance=?, version=?, updated_at=?, last_mentioned_at=?, aliases=?, summary=?
          WHERE entity_id=?
          """,
          (name, type_val, importance, new_ver, now_iso, last_mentioned, aliases_val, summary_val, entity_id),
        )
      updated_state = dict(existing)
      updated_state.update({"name": name, "type": type_val, "importance": importance, "version": new_ver, "updated_at": now_iso, "last_mentioned_at": last_mentioned, "aliases": aliases_val, "summary": summary_val})
      self.log_mutation(
        entity_id=entity_id,
        action="UPDATE",
        reason="Entity update in World Model",
        state_before=existing,
        state_after=updated_state,
        entity_type="entity",
        initiator="world_model",
      )
      return entity_id

  def get_world_entity(self, entity_id: str) -> dict[str, Any] | None:
    with self._conn() as conn:
      row = conn.execute("SELECT * FROM entities WHERE entity_id=?", (entity_id,)).fetchone()
    if not row:
      return None
    d = dict(row)
    if isinstance(d.get("aliases"), str):
      try:
        d["aliases"] = json.loads(d["aliases"])
      except Exception:
        d["aliases"] = []
    return d

  def list_world_entities(self, entity_type: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    query = "SELECT * FROM entities"
    params: list[Any] = []
    if entity_type:
      query += " WHERE type=?"
      params.append(entity_type)
    query += " ORDER BY importance DESC, last_mentioned_at DESC LIMIT ?"
    params.append(limit)
    with self._conn() as conn:
      rows = conn.execute(query, params).fetchall()
    res = []
    for r in rows:
      d = dict(r)
      if isinstance(d.get("aliases"), str):
        try:
          d["aliases"] = json.loads(d["aliases"])
        except Exception:
          d["aliases"] = []
      res.append(d)
    return res

  def search_world_entities_by_name(self, name_query: str) -> list[dict[str, Any]]:
    with self._conn() as conn:
      rows = conn.execute(
        "SELECT * FROM entities WHERE name LIKE ? OR aliases LIKE ? ORDER BY importance DESC",
        (f"%{name_query}%", f"%{name_query}%"),
      ).fetchall()
    res = []
    for r in rows:
      d = dict(r)
      if isinstance(d.get("aliases"), str):
        try:
          d["aliases"] = json.loads(d["aliases"])
        except Exception:
          d["aliases"] = []
      res.append(d)
    return res

  def add_entity_attribute(self, attr: dict[str, Any]) -> int:
    entity_id = str(attr.get("entity_id") or "")
    key = str(attr.get("attribute_key") or "")
    val = str(attr.get("attribute_value") or "")
    conf = float(attr.get("confidence", 0.8))
    fact_id = str(attr.get("source_fact_id") or "")
    now_iso = str(attr.get("created_at") or datetime.now().isoformat())
    with self._conn() as conn:
      cur = conn.execute(
        """
        INSERT INTO entity_attributes (
          entity_id, attribute_key, attribute_value, confidence, source_fact_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (entity_id, key, val, conf, fact_id, now_iso),
      )
      return int(cur.lastrowid or 0)

  def get_entity_attributes(self, entity_id: str) -> list[dict[str, Any]]:
    with self._conn() as conn:
      rows = conn.execute(
        "SELECT * FROM entity_attributes WHERE entity_id=? ORDER BY confidence DESC, id DESC",
        (entity_id,),
      ).fetchall()
    return [dict(r) for r in rows]

  def upsert_entity_relation(self, rel: dict[str, Any], expected_version: int | None = None) -> str:
    relation_id = str(rel.get("relation_id") or "")
    from_id = str(rel.get("from_entity_id") or "")
    to_id = str(rel.get("to_entity_id") or "")
    rel_type = str(rel.get("relation_type") or "")
    if not (from_id and to_id and rel_type):
      raise ValueError("from_entity_id, to_entity_id, and relation_type are required")
    now_iso = datetime.now().isoformat()
    existing: dict[str, Any] | None = None
    if relation_id:
      existing = self.get_entity_relation(relation_id)
    else:
      with self._conn() as conn:
        row = conn.execute(
          "SELECT * FROM entity_relations WHERE from_entity_id=? AND to_entity_id=? AND relation_type=?",
          (from_id, to_id, rel_type),
        ).fetchone()
        if row:
          existing = dict(row)
          relation_id = str(existing["relation_id"])
        else:
          relation_id = f"erel_{uuid.uuid4().hex[:12]}"

    if existing is None:
      version = int(rel.get("version", 1))
      with self._conn() as conn:
        conn.execute(
          """
          INSERT INTO entity_relations (
            relation_id, from_entity_id, to_entity_id, relation_type, trust,
            interaction_frequency, sentiment, relationship_strength, version, last_seen_at
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
          """,
          (
            relation_id,
            from_id,
            to_id,
            rel_type,
            float(rel.get("trust", 0.5)),
            float(rel.get("interaction_frequency", 0.0)),
            float(rel.get("sentiment", 0.0)),
            float(rel.get("relationship_strength", 0.5)),
            version,
            str(rel.get("last_seen_at") or now_iso),
          ),
        )
      self.log_mutation(
        entity_id=relation_id,
        action="CREATE",
        reason="Relationship creation in World Model",
        state_before={},
        state_after=rel,
        entity_type="entity_relation",
        initiator="world_model",
      )
      return relation_id
    else:
      if expected_version is not None and int(existing["version"]) != int(expected_version):
        raise ConcurrentModificationError(
          f"Concurrent modification on entity_relation {relation_id}: expected version {expected_version}, actual {existing['version']}",
          record_id=relation_id,
          expected_version=expected_version,
          actual_version=existing["version"],
        )
      new_ver = int(existing["version"]) + 1
      trust = float(rel.get("trust", existing["trust"]))
      freq = float(rel.get("interaction_frequency", existing["interaction_frequency"]))
      sent = float(rel.get("sentiment", existing["sentiment"]))
      strength = float(rel.get("relationship_strength", existing["relationship_strength"]))
      last_seen = str(rel.get("last_seen_at", existing.get("last_seen_at") or now_iso))
      with self._conn() as conn:
        conn.execute(
          """
          UPDATE entity_relations
          SET trust=?, interaction_frequency=?, sentiment=?, relationship_strength=?, version=?, last_seen_at=?
          WHERE relation_id=?
          """,
          (trust, freq, sent, strength, new_ver, last_seen, relation_id),
        )
      updated_state = dict(existing)
      updated_state.update({"trust": trust, "interaction_frequency": freq, "sentiment": sent, "relationship_strength": strength, "version": new_ver, "last_seen_at": last_seen})
      self.log_mutation(
        entity_id=relation_id,
        action="UPDATE",
        reason="Relationship update in World Model",
        state_before=existing,
        state_after=updated_state,
        entity_type="entity_relation",
        initiator="world_model",
      )
      return relation_id

  def get_entity_relation(self, relation_id: str) -> dict[str, Any] | None:
    with self._conn() as conn:
      row = conn.execute("SELECT * FROM entity_relations WHERE relation_id=?", (relation_id,)).fetchone()
    return dict(row) if row else None

  def list_entity_relations(
    self,
    from_entity_id: str | None = None,
    to_entity_id: str | None = None,
    entity_id: str | None = None,
    min_trust: float = 0.0,
  ) -> list[dict[str, Any]]:
    query = "SELECT * FROM entity_relations WHERE trust >= ?"
    params: list[Any] = [min_trust]
    if entity_id:
      query += " AND (from_entity_id = ? OR to_entity_id = ?)"
      params.append(entity_id)
      params.append(entity_id)
    if from_entity_id:
      query += " AND from_entity_id = ?"
      params.append(from_entity_id)
    if to_entity_id:
      query += " AND to_entity_id = ?"
      params.append(to_entity_id)
    query += " ORDER BY trust DESC, relationship_strength DESC"
    with self._conn() as conn:
      rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]

  def add_entity_mention(self, mention: dict[str, Any]) -> int:
    entity_id = str(mention.get("entity_id") or "")
    fact_id = str(mention.get("fact_id") or "")
    snippet = str(mention.get("context_snippet") or "")
    now_iso = str(mention.get("created_at") or datetime.now().isoformat())
    with self._conn() as conn:
      cur = conn.execute(
        "INSERT INTO entity_mentions (entity_id, fact_id, context_snippet, created_at) VALUES (?, ?, ?, ?)",
        (entity_id, fact_id, snippet, now_iso),
      )
      return int(cur.lastrowid or 0)

  def get_mentions_for_fact(self, fact_id: str) -> list[dict[str, Any]]:
    with self._conn() as conn:
      rows = conn.execute("SELECT * FROM entity_mentions WHERE fact_id=?", (fact_id,)).fetchall()
    return [dict(r) for r in rows]

  def get_mentions_for_entity(self, entity_id: str) -> list[dict[str, Any]]:
    with self._conn() as conn:
      rows = conn.execute("SELECT * FROM entity_mentions WHERE entity_id=? ORDER BY id DESC", (entity_id,)).fetchall()
    return [dict(r) for r in rows]



