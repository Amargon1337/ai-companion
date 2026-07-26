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


def test_sanitize_chat_history_ensures_valid_roles():
    raw_history = [
        {"role": "user", "parts": [{"text": "Hello"}]},
        {"role": "assistant", "parts": [{"text": "Hi there"}]},
        {"role": "system", "parts": [{"text": "System note"}]},
        {"role": None, "parts": [{"text": "Null role note"}]},
    ]
    sanitized = bot_core._sanitize_chat_history(raw_history)
    
    assert len(sanitized) == 4
    roles = [m["role"] for m in sanitized]
    assert all(r in ("user", "model") for r in roles)
    assert roles == ["user", "model", "user", "user"]
    assert "[Note]: System note" in sanitized[2]["parts"][0]["text"]
    assert "[Note]: Null role note" in sanitized[3]["parts"][0]["text"]

