"""SQLite backend — Phase 5, used as primary store with jsonl mirror."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from typing import Any


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
class MemoryDatabase:
  def __init__(self, path: str | None = None) -> None:
    from companion.config import SQLITE_PATH as _SQLITE_PATH
    self.path = path if path is not None else _SQLITE_PATH
    import threading
    self._lock = threading.RLock()
    self.conn = sqlite3.connect(self.path, check_same_thread=False)
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

  @contextmanager
  def _conn(self) -> Generator[sqlite3.Connection, None, None]:
    with self._lock:
      try:
        yield self.conn
        self.conn.commit()
      except Exception:
        self.conn.rollback()
        raise

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
          status TEXT DEFAULT 'active',
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
          last_accessed TEXT,
          access_count INTEGER DEFAULT 0,
          decay_exempt INTEGER DEFAULT 0
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
          created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_reflections_status_composite ON reflections(status, created_at DESC);

        CREATE TABLE IF NOT EXISTS beliefs (
          id TEXT PRIMARY KEY,
          belief TEXT NOT NULL,
          based_on TEXT DEFAULT '[]',
          importance INTEGER DEFAULT 6,
          status TEXT DEFAULT 'active',
          created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_beliefs_status_composite ON beliefs(status, importance DESC);

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

        CREATE TABLE IF NOT EXISTS todos (
          id TEXT PRIMARY KEY,
          text TEXT NOT NULL,
          done INTEGER DEFAULT 0,
          created_at TEXT,
          completed_at TEXT
        );

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
          FOREIGN KEY (fact_id) REFERENCES facts(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_memory_access_log_fact_time ON memory_access_log(fact_id, accessed_at DESC);
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
        conn.execute("UPDATE facts SET anchor_flag=1 WHERE anchor_flag=0 AND (tags LIKE '%anchor%' OR tags LIKE '%core_identity%' OR tags LIKE '%pinned%' OR memory_kind='permanent')")
        conn.execute("UPDATE facts SET archived=1 WHERE archived=0 AND status='archived'")
      except sqlite3.OperationalError:
        pass

      self._migrate_jsonl_files(conn)


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
      INSERT INTO goals VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(goal_id) DO UPDATE SET
        title=excluded.title, priority=excluded.priority, status=excluded.status,
        description=excluded.description, blockers=excluded.blockers,
        next_actions=excluded.next_actions, resources=excluded.resources,
        obstacles=excluded.obstacles, progress_markers=excluded.progress_markers,
        updated_at=excluded.updated_at
      """,
      (
        row["goal_id"], row["title"], row.get("priority", 5), row.get("status", "active"),
        row.get("description", ""), _json(row.get("blockers", [])), _json(row.get("next_actions", [])),
        _json(row.get("resources", [])), _json(row.get("obstacles", [])), _json(row.get("progress_markers", [])),
        row.get("created_at"), row.get("updated_at"),
      ),
    )

  def _upsert_causal_link_conn(self, conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
      """
      INSERT INTO causal_links VALUES (?,?,?,?,?,?,?,?)
      ON CONFLICT(link_id) DO UPDATE SET
        cause=excluded.cause, effect=excluded.effect, confidence=excluded.confidence,
        evidence=excluded.evidence, mechanism=excluded.mechanism,
        observed_count=excluded.observed_count
      """,
      (
        row["link_id"], row["cause"], row["effect"], row.get("confidence", 0.5),
        _json(row.get("evidence", [])), row.get("mechanism", ""), row.get("observed_count", 1), row.get("created_at"),
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
      )
      for row in rows
    ]
    with self._conn() as conn:
      conn.executemany(
        """
        INSERT OR IGNORE INTO facts (
          id, fact, date, created_at, memory_kind, importance, confidence,
          source, source_type, tags, status, valid_from, valid_until,
          schema_version, evidence, facts_sent_count, facts_used_count, embedding
        ) VALUES
        (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
        "INSERT OR IGNORE INTO reflections VALUES (?,?,?,?,?,?,?,?)",
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
        "INSERT OR IGNORE INTO beliefs VALUES (?,?,?,?,?,?)",
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
          last_accessed, access_count, decay_exempt
        ) VALUES
        (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
          decay_exempt=excluded.decay_exempt
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
        "INSERT OR IGNORE INTO reflections VALUES (?,?,?,?,?,?,?,?)",
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

  def update_goal(self, goal_id: str, updates: dict[str, Any]) -> bool:
    allowed = {
      "title", "priority", "status", "description", "blockers", "next_actions",
      "resources", "obstacles", "progress_markers", "updated_at",
    }
    values = {k: v for k, v in updates.items() if k in allowed}
    if not values:
      return False
    if "updated_at" not in values:
      from datetime import datetime
      values["updated_at"] = datetime.now().isoformat()
    json_cols = {"blockers", "next_actions", "resources", "obstacles", "progress_markers"}
    assignments = ", ".join(f"{k}=?" for k in values)
    params = [_json(v) if k in json_cols else v for k, v in values.items()]
    params.append(goal_id)
    with self._conn() as conn:
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

  def upsert_prediction(self, row: dict[str, Any]) -> None:
    with self._conn() as conn:
      self._upsert_prediction_conn(conn, row)

  async def async_upsert_prediction(self, row: dict[str, Any]) -> None:
    await asyncio.to_thread(self.upsert_prediction, row)

  def list_predictions(self, outcome: str | None = None) -> list[dict[str, Any]]:
    with self._conn() as conn:
      if outcome is None:
        rows = conn.execute("SELECT * FROM predictions ORDER BY created_at DESC").fetchall()
      else:
        rows = conn.execute(
          "SELECT * FROM predictions WHERE outcome=? ORDER BY created_at DESC",
          (outcome,),
        ).fetchall()
    return [self._row_prediction(r) for r in rows]

  async def async_list_predictions(self, outcome: str | None = None) -> list[dict[str, Any]]:
    return await asyncio.to_thread(self.list_predictions, outcome)

  def delete_prediction(self, prediction_id: str) -> bool:
    with self._conn() as conn:
      cur = conn.execute("DELETE FROM predictions WHERE prediction_id=?", (prediction_id,))
      return cur.rowcount > 0

  async def async_delete_prediction(self, prediction_id: str) -> bool:
    return await asyncio.to_thread(self.delete_prediction, prediction_id)

  def _row_goal(self, row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for key in ("blockers", "next_actions", "resources", "obstacles", "progress_markers"):
      d[key] = _loads(d.get(key))
    return d

  def _row_causal_link(self, row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["evidence"] = _loads(d.get("evidence"))
    return d

  def _row_prediction(self, row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["conditions"] = _loads(d.get("conditions"))
    d["based_on"] = _loads(d.get("based_on"))
    return d

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

  def update_fact_status(self, fact_id: str, status: str) -> None:
    with self._conn() as conn:
      conn.execute("UPDATE facts SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, fact_id))

  def update_fact_fields(self, fact_id: str, fields: dict[str, Any]) -> None:
    """Update a subset of mutable fact columns atomically."""
    allowed = {
      "fact", "date", "importance", "confidence", "tags", "status",
      "valid_from", "valid_until", "evidence", "version", "superseded_by",
      "memory_kind", "source", "source_type", "anchor_flag", "manual_lock",
    }
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
      return
    if "tags" in sets:
      sets["tags"] = json.dumps(sets["tags"], ensure_ascii=False)
    if "evidence" in sets:
      sets["evidence"] = json.dumps(sets["evidence"], ensure_ascii=False)
    sets["updated_at"] = fields.get("updated_at") or datetime.now().isoformat()
    assignments = ", ".join(f"{k}=?" for k in sets)
    params = list(sets.values()) + [fact_id]
    with self._conn() as conn:
      conn.execute(f"UPDATE facts SET {assignments} WHERE id=?", params)

  def get_fact_relations(self, fact_id: str) -> list[dict[str, Any]]:
    with self._conn() as conn:
      rows = conn.execute(
        "SELECT * FROM fact_relations WHERE from_id=? OR to_id=? ORDER BY created_at ASC",
        (fact_id, fact_id),
      ).fetchall()
    return [dict(r) for r in rows]

  def delete_fact(self, fact_id: str) -> bool:
    with self._conn() as conn:
      cur = conn.execute("DELETE FROM facts WHERE id=?", (fact_id,))
      conn.execute("DELETE FROM fact_relations WHERE from_id=? OR to_id=?", (fact_id, fact_id))
      return cur.rowcount > 0

  def _row_fact(self, row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["tags"] = json.loads(d.get("tags") or "[]")
    d["evidence"] = json.loads(d.get("evidence") or "[]")
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

  def increment_fact_usage(self, fact_id: str, used: bool = False) -> None:
    with self._conn() as conn:
      if used:
        conn.execute("UPDATE facts SET facts_sent_count = facts_sent_count + 1, facts_used_count = facts_used_count + 1 WHERE id=?", (fact_id,))
      else:
        conn.execute("UPDATE facts SET facts_sent_count = facts_sent_count + 1 WHERE id=?", (fact_id,))

  def increment_fact_usage_batch(self, sent_ids: list[str], used_ids: list[str]) -> None:
    if not sent_ids and not used_ids:
      return
    with self._conn() as conn:
      if sent_ids:
        sent_only = [i for i in sent_ids if i not in used_ids]
        if sent_only:
          conn.executemany("UPDATE facts SET facts_sent_count = facts_sent_count + 1 WHERE id=?", [(i,) for i in sent_only])
      if used_ids:
        conn.executemany("UPDATE facts SET facts_sent_count = facts_sent_count + 1, facts_used_count = facts_used_count + 1 WHERE id=?", [(i,) for i in used_ids])

  def hydrate_fact_metadata(self, fact_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not fact_ids:
      return {}
    unique_ids = list(dict.fromkeys(fact_ids))
    placeholders = ",".join("?" for _ in unique_ids)
    with self._conn() as conn:
      rows = conn.execute(
        f"""
        SELECT id, importance, category, anchor_flag, manual_lock, archived,
               created_at, date, updated_at, last_accessed, access_count, decay_exempt,
               memory_kind, tags, status
        FROM facts
        WHERE id IN ({placeholders})
        """,
        unique_ids,
      ).fetchall()
    return {r["id"]: self._row_fact(r) for r in rows}

  def record_fact_access_batch(self, fact_scores: list[tuple[str, float, float]], query_hash: str | None = None) -> None:
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
        INSERT INTO memory_access_log(fact_id, query_hash, vector_score, final_score, source)
        VALUES(?,?,?,?, 'rag')
        """,
        [(fact_id, query_hash, vector_score, final_score) for fact_id, vector_score, final_score in fact_scores],
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

  def save_todo(self, task_id: str, text: str, done: bool = False, created_at: str | None = None) -> None:
    from datetime import datetime
    created_at = created_at or datetime.now().isoformat()
    with self._conn() as conn:
      conn.execute(
        "INSERT INTO todos(id,text,done,created_at) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET text=excluded.text, done=excluded.done",
        (task_id, text, int(done), created_at),
      )

  async def async_save_todo(self, task_id: str, text: str, done: bool = False) -> None:
    await asyncio.to_thread(self.save_todo, task_id, text, done)

  def list_todos(self, include_done: bool = True) -> list[dict[str, Any]]:
    with self._conn() as conn:
      if include_done:
        rows = conn.execute("SELECT * FROM todos ORDER BY done ASC, created_at ASC").fetchall()
      else:
        rows = conn.execute("SELECT * FROM todos WHERE done=0 ORDER BY created_at ASC").fetchall()
    return [dict(r) for r in rows]

  async def async_list_todos(self, include_done: bool = True) -> list[dict[str, Any]]:
    return await asyncio.to_thread(self.list_todos, include_done)

  def complete_todo(self, task_id: str) -> bool:
    from datetime import datetime
    with self._conn() as conn:
      cur = conn.execute(
        "UPDATE todos SET done=1, completed_at=? WHERE id=?",
        (datetime.now().isoformat(), task_id),
      )
      return cur.rowcount > 0

  async def async_complete_todo(self, task_id: str) -> bool:
    return await asyncio.to_thread(self.complete_todo, task_id)

  def delete_todo(self, task_id: str) -> bool:
    with self._conn() as conn:
      cur = conn.execute("DELETE FROM todos WHERE id=?", (task_id,))
      return cur.rowcount > 0

  async def async_delete_todo(self, task_id: str) -> bool:
    return await asyncio.to_thread(self.delete_todo, task_id)

  def clear_done_todos(self) -> int:
    with self._conn() as conn:
      cur = conn.execute("DELETE FROM todos WHERE done=1")
      return cur.rowcount

  async def async_clear_done_todos(self) -> int:
    return await asyncio.to_thread(self.clear_done_todos)

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
