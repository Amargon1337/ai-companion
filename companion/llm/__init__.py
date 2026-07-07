"""LLM integration."""
from __future__ import annotations

from companion.llm.client import (
    SAFETY_SETTINGS,
    aio_delete_file,
    async_oneshot,
    async_delete_file,
    async_search_with_grounding,
    async_upload_file,
    delete_file,
    format_grounding_sources,
    get_file,
    history_item,
    make_config,
    make_search_config,
    oneshot,
    parse_json_array,
    parse_json_object,
    run_llm,
    search_with_grounding,
    upload_file,
)

# Re-export module-level functions for convenience (avoid shadowing `genai.Client` instance)
__all__ = [
    "SAFETY_SETTINGS",
    "aio_delete_file",
    "async_oneshot",
    "async_delete_file",
    "async_search_with_grounding",
    "async_upload_file",
    "delete_file",
    "format_grounding_sources",
    "get_file",
    "history_item",
    "make_config",
    "make_search_config",
    "oneshot",
    "parse_json_array",
    "parse_json_object",
    "run_llm",
    "search_with_grounding",
    "upload_file",
]
