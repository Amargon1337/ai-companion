"""Tests for Phase C1.7 Memory Audit CLI."""
from __future__ import annotations

import os
import uuid
import pytest
from companion.memory.store import MemoryStore
from companion.memory.audit import run_memory_audit
from companion.models import Fact, MemoryOrigin, IdentityLayer


@pytest.fixture
def memory_store(tmp_path):
    old_db = os.environ.get("SQLITE_PATH")
    os.environ["SQLITE_PATH"] = str(tmp_path / "test_audit_cli.db")

    store = MemoryStore()
    yield store

    if old_db:
        os.environ["SQLITE_PATH"] = old_db
    else:
        os.environ.pop("SQLITE_PATH", None)


def test_memory_audit_cli(memory_store, capsys):
    fid = str(uuid.uuid4())
    fact = Fact(
        id=fid,
        fact="User tests Memory Audit CLI",
        date="2026-07-31",
        importance=8,
        confidence=0.9,
        source="test",
        origin=MemoryOrigin.USER_STATEMENT,
        identity_layer=IdentityLayer.PREFERENCE,
    )
    memory_store.add_fact(fact, actor="TEST")

    passed = run_memory_audit(memory_store)
    assert passed is True

    captured = capsys.readouterr()
    assert "Memory OS Integrity Report" in captured.out
    assert "total:       1" in captured.out
    assert "PASS [OK]" in captured.out
