"""Phase 6.5: Tool Registry — unified internal tool API.

Provides a registry for tools that the AI can invoke. Each tool
has a name, description, JSON schema, and execute() method.
MCP transport can be layered on top later (Phase 7).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class Tool:
    """A single invocable tool."""
    name: str
    description: str
    schema: dict[str, Any] = field(default_factory=dict)
    _executor: Callable[..., Any] | None = None

    def execute(self, params: dict[str, Any] | None = None) -> Any:
        if self._executor is None:
            return {"error": f"Tool '{self.name}' has no executor bound."}
        try:
            return self._executor(params or {})
        except Exception as e:
            logger.error("Tool %s execution failed: %s", self.name, e)
            return {"error": str(e)}


class ToolRegistry:
    """Central registry for all available tools."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool
        logger.debug("Registered tool: %s", tool.name)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def execute(self, name: str, params: dict[str, Any] | None = None) -> Any:
        tool = self.get(name)
        if tool is None:
            return {"error": f"Tool '{name}' not found."}
        return tool.execute(params)

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {"name": t.name, "description": t.description, "schema": t.schema}
            for t in self._tools.values()
        ]


# ── Built-in tool executors ──────────────────────────────────────────

def _filesystem_read(params: dict[str, Any]) -> Any:
    """Reads a file from the filesystem."""
    path = params.get("path", "")
    if not path:
        return {"error": "Missing 'path' parameter."}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return {"content": f.read()[:5000]}
    except Exception as e:
        return {"error": str(e)}


def _calendar_get(params: dict[str, Any]) -> Any:
    """Returns current date/time information."""
    from datetime import datetime
    now = datetime.now()
    return {"date": now.isoformat()[:10], "time": now.strftime("%H:%M"), "weekday": now.strftime("%A")}


def _notes_save(params: dict[str, Any]) -> Any:
    """Saves a note to memory."""
    text = params.get("text", "")
    if not text:
        return {"error": "Missing 'text' parameter."}
    return {"status": "saved", "text": text[:200]}


def _search_stub(params: dict[str, Any]) -> Any:
    """Stub for web search (to be connected to real search API)."""
    query = params.get("query", "")
    return {"status": "stub", "query": query, "results": []}


def _python_run(params: dict[str, Any]) -> Any:
    """Evaluates a simple Python expression (sandboxed)."""
    expr = params.get("expression", "")
    if not expr:
        return {"error": "Missing 'expression' parameter."}
    try:
        result = eval(expr, {"__builtins__": {}}, {})
        return {"result": str(result)}
    except Exception as e:
        return {"error": str(e)}


def create_default_registry() -> ToolRegistry:
    """Creates a registry with all built-in tools registered."""
    registry = ToolRegistry()

    registry.register(Tool(
        name="filesystem.read",
        description="Read content from a file on disk.",
        schema={"type": "object", "properties": {"path": {"type": "string"}}},
        _executor=_filesystem_read,
    ))
    registry.register(Tool(
        name="calendar.get",
        description="Get current date, time, and weekday.",
        schema={"type": "object", "properties": {}},
        _executor=_calendar_get,
    ))
    registry.register(Tool(
        name="notes.save",
        description="Save a short note to memory.",
        schema={"type": "object", "properties": {"text": {"type": "string"}}},
        _executor=_notes_save,
    ))
    registry.register(Tool(
        name="search.web",
        description="Search the web for information.",
        schema={"type": "object", "properties": {"query": {"type": "string"}}},
        _executor=_search_stub,
    ))
    registry.register(Tool(
        name="python.run",
        description="Evaluate a simple Python expression.",
        schema={"type": "object", "properties": {"expression": {"type": "string"}}},
        _executor=_python_run,
    ))

    return registry
