"""Append-only JSONL utilities."""
from __future__ import annotations

import json
import os
from typing import Any


def read_jsonl(path: str) -> list[dict[str, Any]]:
    if not os.path.exists(path):
        return []
    items: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return items


def append_jsonl(path: str, item: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


def rotate_jsonl(path: str, max_bytes: int = 50 * 1024 * 1024, max_lines: int = 500_000, keep: int = 2) -> None:
    """Rotate JSONL file if it exceeds size or line count. Archive old file(s)."""
    if not os.path.exists(path):
        return

    # Check size
    size = os.path.getsize(path)
    if size < max_bytes:
        # Check line count
        try:
            with open(path, encoding="utf-8") as f:
                line_count = sum(1 for _ in f)
            if line_count < max_lines:
                return
        except Exception:
            return

    import logging
    import shutil
    logger = logging.getLogger(__name__)
    logger.info("Rotating %s (size=%d, max_bytes=%d)", path, size, max_bytes)

    base, ext = os.path.splitext(path)
    # Shift backups: remove oldest
    oldest = os.path.join(f"{base}.{keep}{ext}")
    if os.path.exists(oldest):
        try:
            os.remove(oldest)
        except OSError:
            pass
    for i in range(keep - 1, 0, -1):
        src = os.path.join(f"{base}.{i}{ext}")
        dst = os.path.join(f"{base}.{i + 1}{ext}")
        if os.path.exists(src):
            try:
                shutil.move(src, dst)
            except OSError:
                pass
    # Rotate current → .1
    try:
        shutil.move(path, os.path.join(f"{base}.1{ext}"))
    except OSError:
        pass


