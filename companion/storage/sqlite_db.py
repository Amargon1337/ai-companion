"""SQLite backend — Phase 5, used as primary store with jsonl mirror."""
from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

def _configure_conn(conn: sqlite3.Connection) -> None:
  conn.execute("PRAGMA busy_timeout = 5000;")
  conn.execute("PRAGMA journal_mode = WAL;")
  conn.execute("PRAGMA foreign_keys = ON;")
class MemoryDatabase:
  def __init__(self, path: str | None = None) -> None:
    from companion.config import SQLITE_PATH as _SQLITE_PATH
    self.path = path if path is not None else _SQLITE_PATH
    self._init_schema()

  @contextmanager
  def _conn(self) -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(self.path)
    _configure_conn(conn)
    conn.row_factory = sqlite3.Row
    try:
      yield conn
      conn.commit()
    finally:
      conn.close()

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
          evidence TEXT DEFAULT '[]'
        );
        CREATE INDEX IF NOT EXISTS idx_facts_status ON facts(status);
        CREATE INDEX IF NOT EXISTS idx_facts_importance ON facts(importance);
        CREATE INDEX IF NOT EXISTS idx_facts_date ON facts(date);

        CREATE TABLE IF NOT EXISTS fact_relations (
          id TEXT PRIMARY KEY,
          from_id TEXT NOT NULL,
          to_id TEXT NOT NULL,
          relation TEXT NOT NULL,
          created_at TEXT,
          reason TEXT,
          confidence REAL DEFAULT 0.8
        );

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

        CREATE TABLE IF NOT EXISTS beliefs (
          id TEXT PRIMARY KEY,
          belief TEXT NOT NULL,
          based_on TEXT DEFAULT '[]',
          importance INTEGER DEFAULT 6,
          status TEXT DEFAULT 'active',
          created_at TEXT
        );

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
      except sqlite3.OperationalError:
        pass



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
      )
      for row in rows
    ]
    with self._conn() as conn:
      conn.executemany(
        """
        INSERT OR IGNORE INTO facts VALUES
        (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
        INSERT INTO facts VALUES
        (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
          facts_used_count=excluded.facts_used_count
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
      conn.execute(
        "INSERT OR IGNORE INTO beliefs VALUES (?,?,?,?,?,?)",
        (
          row["id"], row["belief"],
          json.dumps(row.get("based_on", []), ensure_ascii=False),
          row.get("importance", 6), row.get("status", "active"),
          row.get("created_at"),
        ),
      )

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
      conn.execute("UPDATE facts SET status=? WHERE id=?", (status, fact_id))

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

  def delete_session(self, user_id: int) -> None:
    with self._conn() as conn:
      conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))

  def count_sessions(self) -> int:
    with self._conn() as conn:
      return conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]

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
