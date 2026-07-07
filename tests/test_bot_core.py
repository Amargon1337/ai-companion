from __future__ import annotations

from companion import bot_core


def test_cleanup_pending_commands_removes_expired_entries():
    bot_core.PENDING_COMMANDS.clear()
    bot_core.PENDING_COMMANDS["expired"] = {
        "command": "add_todo",
        "payload": "добавь задачу",
        "uid": 12345,
        "created_at": 100.0,
    }
    bot_core.PENDING_COMMANDS["fresh"] = {
        "command": "add_todo",
        "payload": "добавь другую задачу",
        "uid": 12345,
        "created_at": 990.0,
    }

    bot_core.cleanup_pending_commands(now=1001.0)

    assert "expired" not in bot_core.PENDING_COMMANDS
    assert "fresh" in bot_core.PENDING_COMMANDS
    bot_core.PENDING_COMMANDS.clear()
