"""Vector index — embedding caching and cosine similarity search via Gemini API."""
from __future__ import annotations

import logging
import math
import re
import sqlite3
import struct
from contextlib import closing, contextmanager
from collections.abc import Generator
from typing import Any

def _configure_conn(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")

logger = logging.getLogger(__name__)

from companion.config import EMBEDDING_MODEL as _EMBEDDING_MODEL
from companion.config import EMBEDDING_DIM as _EMBEDDING_DIM
from companion.config import FAISS_FLUSH_EVERY as _FAISS_FLUSH_EVERY


EMBEDDING_FAILURES = 0
ZERO_VECTOR_GENERATIONS = 0
_GENAI_CLIENT = None


def _get_genai_client():
    global _GENAI_CLIENT
    if _GENAI_CLIENT is None:
        from google import genai
        from companion.config import GOOGLE_API_KEY

        if not GOOGLE_API_KEY or "test" in GOOGLE_API_KEY.lower():
            raise ValueError("Invalid or test GOOGLE_API_KEY")
        _GENAI_CLIENT = genai.Client(api_key=GOOGLE_API_KEY)
    return _GENAI_CLIENT

def _embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts via Gemini API."""
    if not texts:
        return []
    try:
        logger.info(f"[VECTOR] Отправка запроса на получение эмбеддингов для {len(texts)} элементов...")
        from google.genai import types
        client = _get_genai_client()

        chunk_size = 90
        all_embeddings = []
        for i in range(0, len(texts), chunk_size):
            chunk = texts[i : i + chunk_size]
            batch_embed = getattr(client.models, "batch_embed_content", None)
            if batch_embed:
                try:
                    result = batch_embed(
                        model=_EMBEDDING_MODEL,
                        contents=chunk,
                        config=types.EmbedContentConfig(output_dimensionality=_EMBEDDING_DIM),
                    )
                except TypeError:
                    result = batch_embed(
                        model=_EMBEDDING_MODEL,
                        requests=[
                            {
                                "model": _EMBEDDING_MODEL,
                                "content": text,
                                "config": {"output_dimensionality": _EMBEDDING_DIM},
                            }
                            for text in chunk
                        ],
                    )
            else:
                result = client.models.embed_content(
                    model=_EMBEDDING_MODEL,
                    contents=chunk,
                    config=types.EmbedContentConfig(output_dimensionality=_EMBEDDING_DIM)
                )
            for embedding in result.embeddings:
                values = list(embedding.values)
                if values and not any(values):
                    global ZERO_VECTOR_GENERATIONS
                    ZERO_VECTOR_GENERATIONS += 1
                    logger.warning("Embedding API returned an all-zero vector")
                all_embeddings.append(values)

        return all_embeddings
    except Exception as exc:
        global EMBEDDING_FAILURES
        EMBEDDING_FAILURES += 1
        logger.warning("Embedding API call failed: %s. Total failures: %d", exc, EMBEDDING_FAILURES)
        raise exc

def get_embedding_stats() -> dict[str, int]:
    return {
        "failures": EMBEDDING_FAILURES,
        "zero_vectors_generated": ZERO_VECTOR_GENERATIONS,
    }


def _float_list_to_blob(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _blob_to_float_list(blob: bytes) -> list[float]:
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class VectorIndex:
    """Manages embedding storage in SQLite and provides vector search."""

    def __init__(self, path: str | None = None, db: Any = None) -> None:
        import os
        from companion.config import SQLITE_PATH as _SQLITE_PATH
        self.path = path if path is not None else _SQLITE_PATH
        self.db = db
        import threading
        self.lock = threading.RLock()
        self._init_table()
        
        self.index_path = os.path.join(os.path.dirname(self.path) if self.path else ".", "faiss_index.bin")
        
        self.id_to_content: dict[int, str] = {}
        self.id_to_hash: dict[int, str] = {}
        self.hash_to_id: dict[str, int] = {}
        self.id_to_type: dict[int, str] = {}
        self._next_id = 0
        self._deleted_ids: set[int] = set()
        
        self._is_initialized = False
        self._dirty_updates = 0
        self._flush_every = max(1, _FAISS_FLUSH_EVERY)
        self.embeddings_enabled = True
        
        # Load existing database embeddings into memory index at startup
        self._load_index()

    @property
    def content_list(self) -> list[str]:
        with self.lock:
            return list(self.id_to_content.values())

    def test_embeddings(self) -> bool:
        """Perform a test embedding request to validate API config."""
        try:
            vec = _embed_texts(["test validation"])
            if vec and any(v != 0.0 for v in vec[0]):
                return True
            return False
        except Exception as exc:
            logger.error("Embedding validation failed on startup: %s", exc)
            return False

    def _init_table(self) -> None:
        with closing(sqlite3.connect(self.path)) as conn:
            _configure_conn(conn)
            
            # Check if table exists
            table_exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='embeddings'"
            ).fetchone()
            
            if table_exists:
                cursor = conn.execute("PRAGMA table_info(embeddings)")
                cols = {row[1] for row in cursor.fetchall()}
                required_cols = {"content_hash", "content", "embedding", "content_type"}
                if not required_cols.issubset(cols):
                    logger.info("Embeddings table schema is outdated. Dropping and recreating table.")
                    conn.execute("DROP TABLE IF EXISTS embeddings")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS embeddings (
                    content_hash TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    embedding BLOB NOT NULL,
                    content_type TEXT DEFAULT 'fact',
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_type ON embeddings(content_type)")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS embeddings_fts USING fts5(
                        content,
                        content_hash UNINDEXED,
                        content_type UNINDEXED,
                        tokenize='unicode61'
                    )
                """)
                conn.execute("""
                    INSERT INTO embeddings_fts(content, content_hash, content_type)
                    SELECT content, content_hash, content_type
                    FROM embeddings
                    WHERE content_hash NOT IN (SELECT content_hash FROM embeddings_fts)
                """)
            except sqlite3.OperationalError as exc:
                logger.warning("FTS5 table initialization or sync failed: %s", exc)
            conn.commit()

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        if getattr(self, "db", None) is not None and hasattr(self.db, "conn"):
            with self.db._lock:
                try:
                    yield self.db.conn
                    if getattr(self.db._tx_state, "depth", 0) == 0:
                        self.db.conn.commit()
                except Exception:
                    if getattr(self.db._tx_state, "depth", 0) == 0:
                        self.db.conn.rollback()
                    raise
            return
        conn = sqlite3.connect(self.path)
        _configure_conn(conn)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @contextmanager
    def _locked(self) -> Generator[None, None, None]:
        """Acquire the DB lock BEFORE the in-memory index lock.

        Global lock order (MemoryDatabase._lock -> VectorIndex.lock) matches the
        atomic_memory_transaction path (db._lock held, then vector.lock inside).
        Any other order produces an ABBA deadlock between writers and searchers.
        """
        db = getattr(self, "db", None)
        if db is not None and hasattr(db, "conn"):
            with db._lock:
                with self.lock:
                    yield
        else:
            with self.lock:
                yield

    def _load_index(self) -> None:
        import os
        import json
        import faiss
        with self._locked():
            if os.path.exists(self.index_path):
                db = getattr(self, "db", None)
                should_close = False
                if db is None:
                    from companion.storage.sqlite_db import MemoryDatabase
                    db = MemoryDatabase(self.path)
                    should_close = True
                try:
                    data = db.get_state_model("faiss_mapping")
                    dirty = db.get_meta("faiss_index_dirty", "0") == "1"
                    if data and not dirty:
                        self.index = faiss.read_index(self.index_path)
                        self.id_to_content = {int(k): v for k, v in data.get("id_to_content", {}).items()}
                        self.id_to_hash = {int(k): v for k, v in data.get("id_to_hash", {}).items()}
                        self.hash_to_id = data.get("hash_to_id", {})
                        self.id_to_type = {int(k): v for k, v in data.get("id_to_type", {}).items()}
                        self._next_id = data.get("next_id", 0)
                        self._deleted_ids = set(int(k) for k in data.get("deleted_ids", []))
                        self._is_initialized = True
                        self._sync_fts()
                        return
                    if dirty:
                        logger.warning("FAISS index has unflushed changes; rebuilding from SQLite")
                except Exception as e:
                    logger.warning(f"Failed to load FAISS index from disk, rebuilding: {e}")
                finally:
                    if should_close:
                        db.close()
            
            self._rebuild_index()

    def save_index_to_disk(self):
        import faiss
        with self._locked():
            if self._is_initialized and hasattr(self, 'index') and self.index is not None:
                faiss.write_index(self.index, self.index_path)
                db = getattr(self, "db", None)
                should_close = False
                if db is None:
                    from companion.storage.sqlite_db import MemoryDatabase
                    db = MemoryDatabase(self.path)
                    should_close = True
                try:
                    db.save_state_model("faiss_mapping", {
                        "id_to_content": self.id_to_content,
                        "id_to_hash": self.id_to_hash,
                        "hash_to_id": self.hash_to_id,
                        "id_to_type": self.id_to_type,
                        "next_id": self._next_id,
                        "deleted_ids": list(self._deleted_ids)
                    })
                    db.set_meta("faiss_index_dirty", "0")
                    self._dirty_updates = 0
                finally:
                    if should_close:
                        db.close()

    def flush_index(self) -> None:
        if self._dirty_updates:
            self.save_index_to_disk()

    def _mark_dirty(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('faiss_index_dirty', '1') "
            "ON CONFLICT(key) DO UPDATE SET value='1'"
        )
        self._dirty_updates += 1

    def _rebuild_index(self) -> None:
        import faiss
        import numpy as np
        
        self.id_to_content = {}
        self.id_to_hash = {}
        self.hash_to_id = {}
        self.id_to_type = {}
        self._next_id = 0
        self._deleted_ids.clear()
        
        base_index = faiss.IndexHNSWFlat(_EMBEDDING_DIM, 32)
        self.index = faiss.IndexIDMap(base_index)
        
        seen_hashes: set[str] = set()

        with self._conn() as conn:
            cursor = conn.execute(
                """
                SELECT fact AS content, embedding, 'fact' AS content_type
                FROM facts
                WHERE embedding IS NOT NULL AND status IN ('active', 'dormant')
                UNION ALL
                SELECT content, embedding, content_type
                FROM embeddings
                WHERE content_type NOT IN ('query', 'fact')
                """
            )
            
            while True:
                rows = cursor.fetchmany(2000)
                if not rows:
                    break
                
                arr_vecs = []
                ids = []
                for content, blob, content_type in rows:
                    content_hash = self._content_hash(content)
                    if content_hash in seen_hashes:
                        continue
                    seen_hashes.add(content_hash)
                    
                    arr_vecs.append(_blob_to_float_list(blob))
                    current_id = self._next_id
                    self.id_to_content[current_id] = content
                    self.id_to_hash[current_id] = content_hash
                    self.hash_to_id[content_hash] = current_id
                    self.id_to_type[current_id] = content_type
                    ids.append(current_id)
                    self._next_id += 1
                
                if arr_vecs:
                    arr = np.array(arr_vecs, dtype=np.float32)
                    faiss.normalize_L2(arr)
                    ids_arr = np.array(ids, dtype=np.int64)
                    self.index.add_with_ids(arr, ids_arr)
                    
                    # explicit memory free for large batches
                    del arr
                    del ids_arr
                    del arr_vecs
                    del ids
            
        self._is_initialized = True
        self.save_index_to_disk()
        self._sync_fts()

    def _content_hash(self, text: str) -> str:
        import hashlib
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def upsert_embedding(
        self,
        text: str,
        embedding: list[float],
        content_type: str = "fact",
        fact_id: str | None = None,
    ) -> None:
        h = self._content_hash(text)
        blob = _float_list_to_blob(embedding)
        with self._locked():
            needs_index_add = content_type != "query" and self._is_initialized and h not in self.hash_to_id
            with self._conn() as conn:
                if content_type == "fact":
                    if fact_id:
                        conn.execute("UPDATE facts SET embedding=? WHERE id=?", (blob, fact_id))
                    else:
                        conn.execute("UPDATE facts SET embedding=? WHERE fact=?", (blob, text))
                else:
                    conn.execute(
                        """INSERT OR REPLACE INTO embeddings (content_hash, content, embedding, content_type)
                           VALUES (?, ?, ?, ?)""",
                        (h, text, blob, content_type),
                    )
                if needs_index_add:
                    self._mark_dirty(conn)
                    try:
                        conn.execute("DELETE FROM embeddings_fts WHERE content_hash=?", (h,))
                        conn.execute(
                            "INSERT INTO embeddings_fts (content, content_hash, content_type) VALUES (?, ?, ?)",
                            (text, h, content_type),
                        )
                    except sqlite3.OperationalError:
                        pass
                
            if content_type == "query":
                return
                
            if not self._is_initialized:
                self._load_index()
                return
                
            if h in self.hash_to_id:
                return
            import faiss
            import numpy as np
            
            current_id = self._next_id
            self.id_to_content[current_id] = text
            self.id_to_hash[current_id] = h
            self.hash_to_id[h] = current_id
            self.id_to_type[current_id] = content_type
            self._next_id += 1
            
            vec = np.array([embedding], dtype=np.float32)
            faiss.normalize_L2(vec)
            ids_arr = np.array([current_id], dtype=np.int64)
            self.index.add_with_ids(vec, ids_arr)
            if self._dirty_updates >= self._flush_every:
                self.save_index_to_disk()

    def get_embedding(self, text: str) -> list[float] | None:
        h = self._content_hash(text)
        with self._conn() as conn:
            # Prefer a live (active/dormant) fact's embedding for this text; fall
            # back to any row so a still-valid cached blob isn't reported missing
            # merely because the only holder just transitioned status.
            row = conn.execute(
                """
                SELECT embedding FROM facts
                WHERE fact=? AND embedding IS NOT NULL
                ORDER BY CASE WHEN status IN ('active','dormant') THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (text,),
            ).fetchone()
            if row:
                return _blob_to_float_list(row[0])
            row = conn.execute(
                "SELECT embedding FROM embeddings WHERE content_hash=?", (h,)
            ).fetchone()
        if row:
            return _blob_to_float_list(row[0])
        return None

    def embed_text_only(self, text: str) -> list[float] | None:
        """Compute or retrieve embedding vector WITHOUT mutating SQLite or holding transaction locks.

        Enables the 2-phase locking rule: LLM -> Lock -> SQLite.
        """
        if not self.embeddings_enabled or not text.strip():
            return None
        existing = self.get_embedding(text)
        if existing:
            return existing
        try:
            return _embed_texts([text])[0]
        except Exception:
            return None

    def compute_and_cache(
        self,
        text: str,
        content_type: str = "fact",
        fact_id: str | None = None,
    ) -> list[float] | None:
        if not self.embeddings_enabled:
            return None
        existing = self.get_embedding(text)
        if existing:
            if content_type == "fact" and fact_id:
                self.upsert_embedding(text, existing, content_type, fact_id=fact_id)
            return existing
        try:
            vec = _embed_texts([text])[0]
            self.upsert_embedding(text, vec, content_type, fact_id=fact_id)
            return vec
        except Exception:
            return None

    def compute_and_cache_batch(self, texts: list[str], content_type: str = "fact") -> None:
        if not self.embeddings_enabled:
            return
            
        valid_texts = [t for t in texts if t.strip()]
        if not valid_texts:
            return
            
        hashes = [self._content_hash(t) for t in valid_texts]
        hash_to_text = dict(zip(hashes, valid_texts))
        existing_by_hash: dict[str, list[float]] = {}
        
        with self._conn() as conn:
            if content_type == "fact":
                fact_rows = conn.execute(
                    f"SELECT fact, embedding FROM facts WHERE embedding IS NOT NULL AND fact IN ({','.join(['?'] * len(valid_texts))})",
                    valid_texts,
                ).fetchall()
                for fact, blob in fact_rows:
                    existing_by_hash[self._content_hash(fact)] = _blob_to_float_list(blob)
            query = f"SELECT content, content_hash, embedding FROM embeddings WHERE content_hash IN ({','.join(['?'] * len(hashes))})"
            rows = conn.execute(query, hashes).fetchall()
            for content, content_hash, blob in rows:
                existing_by_hash.setdefault(content_hash, _blob_to_float_list(blob))
            found_hashes = set(existing_by_hash)
            if content_type == "fact":
                conn.executemany(
                    "UPDATE facts SET embedding=? WHERE fact=? AND embedding IS NULL",
                    [(_float_list_to_blob(vec), hash_to_text[h]) for h, vec in existing_by_hash.items() if h in hash_to_text],
                )
            
        missing_texts = [hash_to_text[h] for h in hashes if h not in found_hashes]
        
        if not missing_texts:
            for text in valid_texts:
                vec = existing_by_hash.get(self._content_hash(text))
                if vec:
                    self.upsert_embedding(text, vec, content_type)
            self.flush_index()
            return
            
        try:
            vectors = _embed_texts(missing_texts)
        except Exception as exc:
            logger.error("Batch embedding failed: %s", exc)
            return
            
        for text, vec in zip(missing_texts, vectors):
            self.upsert_embedding(text, vec, content_type)
        self.flush_index()

    def _sync_fts(self) -> None:
        """Synchronize FTS5 virtual table with loaded index."""
        with self.lock:
            if not self._is_initialized:
                return
            items = [
                (self.id_to_content[idx], self.id_to_hash[idx], self.id_to_type.get(idx, "fact"))
                for idx in self.id_to_content
            ]
        with self._conn() as conn:
            try:
                conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS embeddings_fts USING fts5(
                        content,
                        content_hash UNINDEXED,
                        content_type UNINDEXED,
                        tokenize='unicode61'
                    )
                """)
                existing_hashes = {
                    row[0]
                    for row in conn.execute("SELECT content_hash FROM embeddings_fts").fetchall()
                }
                new_items = [item for item in items if item[1] not in existing_hashes]
                if new_items:
                    conn.executemany(
                        "INSERT INTO embeddings_fts (content, content_hash, content_type) VALUES (?, ?, ?)",
                        new_items,
                    )
                valid_hashes = {item[1] for item in items}
                to_delete = existing_hashes - valid_hashes
                if to_delete:
                    conn.executemany(
                        "DELETE FROM embeddings_fts WHERE content_hash=?",
                        [(h,) for h in to_delete],
                    )
            except sqlite3.OperationalError as exc:
                logger.warning("FTS5 table synchronization failed: %s", exc)

    def search_bm25(self, query: str, top_k: int = 10, content_type: str | None = None) -> list[dict[str, Any]]:
        """Search full-text SQLite FTS5 index using BM25 ranking."""
        terms = re.findall(r"[^\W\d_]+|\d+", query.lower(), re.UNICODE)
        terms = [t for t in terms if len(t) >= 2]
        if not terms:
            return []

        fts_query = " OR ".join(f'"{t}"' for t in set(terms))

        if not self._is_initialized:
            self._load_index()

        with self._conn() as conn:
            try:
                if content_type:
                    cursor = conn.execute(
                        """
                        SELECT content, content_hash, bm25(embeddings_fts) as bm25_score
                        FROM embeddings_fts
                        WHERE embeddings_fts MATCH ? AND content_type = ?
                        ORDER BY bm25_score ASC
                        LIMIT ?
                        """,
                        (fts_query, content_type, min(500, top_k * 5)),
                    )
                else:
                    cursor = conn.execute(
                        """
                        SELECT content, content_hash, bm25(embeddings_fts) as bm25_score
                        FROM embeddings_fts
                        WHERE embeddings_fts MATCH ? AND content_type != 'entity'
                        ORDER BY bm25_score ASC
                        LIMIT ?
                        """,
                        (fts_query, min(500, top_k * 5)),
                    )
                rows = cursor.fetchall()
                results = []
                for idx, (content, content_hash, bm25_score) in enumerate(rows, start=1):
                    results.append({
                        "content": content,
                        "content_hash": content_hash,
                        "bm25_score": float(bm25_score),
                        "bm25_rank": idx,
                    })
                return results[:top_k]
            except sqlite3.OperationalError as exc:
                logger.warning("FTS5 BM25 search failed: %s", exc)
                return []

    def search_hybrid(
        self,
        query: str,
        top_k: int = 10,
        content_type: str | None = None,
        rrf_k: int = 60,
        alpha: float = 0.5,
    ) -> list[dict[str, Any]]:
        """Combine FAISS vector search and SQLite FTS5 BM25 search via Reciprocal Rank Fusion (RRF)."""
        vec_results = self.search(query, top_k=max(30, top_k * 3), content_type=content_type, hybrid=False)
        bm25_results = self.search_bm25(query, top_k=max(30, top_k * 3), content_type=content_type)

        if not vec_results and not bm25_results:
            return []

        by_hash: dict[str, dict[str, Any]] = {}

        for rank, item in enumerate(vec_results, start=1):
            h = item["content_hash"]
            by_hash[h] = {
                "content": item["content"],
                "content_hash": h,
                "vector_score": float(item["score"]),
                "vec_rank": rank,
                "bm25_rank": None,
                "bm25_score": None,
            }

        for rank, item in enumerate(bm25_results, start=1):
            h = item["content_hash"]
            if h not in by_hash:
                by_hash[h] = {
                    "content": item["content"],
                    "content_hash": h,
                    "vector_score": 0.5,
                    "vec_rank": None,
                    "bm25_rank": rank,
                    "bm25_score": float(item["bm25_score"]),
                }
            else:
                by_hash[h]["bm25_rank"] = rank
                by_hash[h]["bm25_score"] = float(item["bm25_score"])

        combined: list[dict[str, Any]] = []
        max_possible_rrf = 2.0 / (rrf_k + 1)

        for h, data in by_hash.items():
            rrf_score = 0.0
            if data["vec_rank"] is not None:
                rrf_score += 1.0 / (rrf_k + data["vec_rank"])
            if data["bm25_rank"] is not None:
                rrf_score += 1.0 / (rrf_k + data["bm25_rank"])

            norm_rrf = min(1.0, rrf_score / max_possible_rrf)
            vec_score = data["vector_score"]
            combined_score = alpha * vec_score + (1.0 - alpha) * norm_rrf

            combined.append({
                "content": data["content"],
                "content_hash": h,
                "score": round(combined_score, 4),
                "vector_score": round(vec_score, 4),
                "rrf_score": round(rrf_score, 6),
                "bm25_rank": data["bm25_rank"],
            })

        combined.sort(key=lambda x: x["score"], reverse=True)
        return combined[:top_k]

    def search(
        self,
        query: str,
        top_k: int = 10,
        content_type: str | None = None,
        hybrid: bool = True,
    ) -> list[dict[str, Any]]:
        if not self.embeddings_enabled:
            return []
        if not self._is_initialized:
            self._load_index()

        if hybrid:
            return self.search_hybrid(query, top_k=top_k, content_type=content_type)

        qvec = self.compute_and_cache(query, content_type="query")
        if qvec is None:
            return []
        
        import faiss
        import numpy as np
        
        q = np.array([qvec], dtype=np.float32)
        faiss.normalize_L2(q)
        
        with self.lock:
            ntotal = self.index.ntotal
            if ntotal == 0:
                return []
            distances, indices = self.index.search(q, min(ntotal, (top_k + len(self._deleted_ids)) * 5))
            
            results = []
            for dist, idx in zip(distances[0], indices[0]):
                if idx != -1 and idx in self.id_to_content and idx not in self._deleted_ids:
                    item_type = self.id_to_type.get(idx)
                    if content_type and item_type != content_type:
                        continue
                    if not content_type and item_type == "entity":
                        continue
                    # Convert L2 distance squared of normalized vectors to Cosine Similarity
                    score = 1.0 - float(dist) / 2.0
                    if score > 0.3:
                        results.append({
                            "content": self.id_to_content[idx],
                            "content_hash": self.id_to_hash[idx],
                            "score": round(score, 4)
                        })
                        if len(results) >= top_k:
                            break
            return results

    def count(self, content_type: str | None = None) -> int:
        with self._conn() as conn:
            if content_type:
                return conn.execute(
                    "SELECT COUNT(*) FROM embeddings WHERE content_type=?", (content_type,)
                ).fetchone()[0]
            return conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]

    def delete_for_content(self, text: str, exclude_fact_id: str | None = None, prior_embedding: bytes | None = None) -> None:
        self.delete_for_content_batch(
            [text],
            exclude_fact_id=exclude_fact_id,
            prior_embedding=prior_embedding,
        )

    def delete_for_content_batch(self, texts: list[str], exclude_fact_id: str | None = None, prior_embedding: bytes | None = None) -> None:
        """Удалить несколько эмбеддингов и перестроить индекс ОДИН раз.
        Раньше delete_for_content делал _load_index() на каждый вызов →
        apply_importance_decay перестраивал весь HNSW N раз подряд."""
        if not texts:
            return
        hashes = [self._content_hash(t) for t in texts]
        with self._locked():
            needs_index_update = any(h in self.hash_to_id for h in hashes)
            with self._conn() as conn:
                if needs_index_update:
                    self._mark_dirty(conn)
                conn.executemany(
                    "DELETE FROM embeddings WHERE content_hash=?",
                    [(h,) for h in hashes],
                )
                for t in texts:
                    # A1: embedding ownership. facts.embedding holds the blob on
                    # whichever row add_fact/upsert wrote it to; sibling rows with
                    # the SAME text keep embedding=NULL. When the holder is removed,
                    # hand its blob to a surviving live sibling (same content should
                    # keep ONE shared vector); only NULL it when NO live fact still
                    # references the text — otherwise the survivor is orphaned from
                    # the index at the next rebuild. The caller that deletes the row
                    # passes its blob in via `prior_embedding`, because after the
                    # DELETE there is no in-DB source left to transfer from.
                    if exclude_fact_id:
                        row = conn.execute(
                            "SELECT id FROM facts WHERE fact=? AND status IN ('active','dormant') AND id != ? LIMIT 1",
                            (t, exclude_fact_id),
                        ).fetchone()
                    else:
                        row = conn.execute(
                            "SELECT id FROM facts WHERE fact=? AND status IN ('active','dormant') LIMIT 1",
                            (t,),
                        ).fetchone()
                    if row is None:
                        conn.execute("UPDATE facts SET embedding=NULL WHERE fact=?", (t,))
                    else:
                        blob: bytes | None = None
                        if prior_embedding is not None:
                            blob = bytes(prior_embedding)
                        else:
                            blob_row = conn.execute(
                                "SELECT embedding FROM facts WHERE fact=? AND embedding IS NOT NULL AND id != ? LIMIT 1",
                                (t, exclude_fact_id or ""),
                            ).fetchone()
                            if blob_row is not None:
                                blob = bytes(blob_row[0])
                        if blob is not None:
                            conn.execute(
                                "UPDATE facts SET embedding=? WHERE id=?",
                                (sqlite3.Binary(blob), row[0]),
                            )
                try:
                    conn.executemany(
                        "DELETE FROM embeddings_fts WHERE content_hash=?",
                        [(h,) for h in hashes],
                    )
                except sqlite3.OperationalError:
                    pass
            
            import numpy as np
            ids_to_remove = []
            for h in hashes:
                if h in self.hash_to_id:
                    del_id = self.hash_to_id.pop(h)
                    self.id_to_content.pop(del_id, None)
                    self.id_to_hash.pop(del_id, None)
                    self.id_to_type.pop(del_id, None)
                    ids_to_remove.append(del_id)
            if ids_to_remove:
                try:
                    self.index.remove_ids(np.array(ids_to_remove, dtype=np.int64))
                except RuntimeError:
                    # Index type (e.g. HNSW) doesn't support remove_ids.
                    # Use hybrid threshold: rebuild only if deleted > 1000 or > 10% of total.
                    for del_id in ids_to_remove:
                        self._deleted_ids.add(del_id)
                    total_vectors = getattr(self.index, "ntotal", 0)
                    if len(self._deleted_ids) > 1000 or (total_vectors > 0 and (len(self._deleted_ids) / total_vectors) > 0.10):
                        self._rebuild_index()
                    else:
                        self.save_index_to_disk()
                    return
                self.save_index_to_disk()
