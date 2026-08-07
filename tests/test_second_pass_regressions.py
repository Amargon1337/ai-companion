"""Regression coverage for second-pass integrity fixes."""
from __future__ import annotations

import ast
from pathlib import Path


def test_entity_resolver_uses_vector_index_public_search_signature() -> None:
    tree = ast.parse(Path("companion/memory/entity_resolver.py").read_text(encoding="utf-8"))
    invalid = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "search":
            if any(keyword.arg == "min_score" for keyword in node.keywords):
                invalid.append(node)
    assert not invalid


def test_subconscious_uses_persisted_prospective_task_api() -> None:
    source = Path("companion/proactive/subconscious.py").read_text(encoding="utf-8")
    assert "async_upsert_prospective_task(task_doc)" in source
    assert "async_insert_prospective_task" not in source


def test_episode_schema_has_occ_version() -> None:
    source = Path("companion/storage/sqlite_db.py").read_text(encoding="utf-8")
    episode_ddl = source[source.index("CREATE TABLE IF NOT EXISTS episodes"):source.index("CREATE INDEX IF NOT EXISTS idx_episodes_date")]
    assert "version INTEGER DEFAULT 1" in episode_ddl
