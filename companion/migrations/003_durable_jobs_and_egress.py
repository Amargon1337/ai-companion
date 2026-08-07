"""Migration 003: durable jobs and metadata-only LLM egress records."""
from __future__ import annotations
import sqlite3

version = 3
description = "Add durable job queue and LLM egress metadata"

def up(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS durable_jobs (
      job_id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL, job_type TEXT NOT NULL,
      payload_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','running','succeeded','failed','cancelled')),
      priority INTEGER NOT NULL DEFAULT 0, attempt_count INTEGER NOT NULL DEFAULT 0,
      max_attempts INTEGER NOT NULL DEFAULT 5, next_attempt_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      locked_at TEXT, locked_by TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, last_error TEXT,
      correlation_id TEXT NOT NULL DEFAULT '', idempotency_key TEXT NOT NULL UNIQUE
    );
    CREATE INDEX IF NOT EXISTS idx_durable_jobs_claim ON durable_jobs(status,next_attempt_at,priority DESC,created_at);
    CREATE INDEX IF NOT EXISTS idx_durable_jobs_owner ON durable_jobs(owner_id,status);
    CREATE TABLE IF NOT EXISTS job_attempts (
      attempt_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES durable_jobs(job_id) ON DELETE CASCADE,
      attempt_no INTEGER NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, outcome TEXT,
      error_class TEXT, error_message TEXT
    );
    CREATE TABLE IF NOT EXISTS llm_egress_log (
      event_id TEXT PRIMARY KEY, owner_id INTEGER, request_id TEXT, purpose TEXT NOT NULL,
      provider TEXT NOT NULL, model TEXT, data_classes TEXT NOT NULL, decision TEXT NOT NULL,
      redactions INTEGER NOT NULL DEFAULT 0, payload_size INTEGER NOT NULL, payload_hash TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_llm_egress_owner_time ON llm_egress_log(owner_id,created_at DESC);
    """)

def down(conn: sqlite3.Connection) -> None:
    raise RuntimeError("Migration 003 is intentionally irreversible; preserving governance audit history is required")
