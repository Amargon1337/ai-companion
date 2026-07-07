"""Gemini API client wrapper."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from google import genai
from google.genai import types as google_types

from companion.config import GOOGLE_API_KEY, MODEL_NAME, SAFETY_SETTINGS_CONFIG, SEARCH_MODEL, LLM_TIMEOUT

logger = logging.getLogger(__name__)

client = genai.Client(
    api_key=GOOGLE_API_KEY,
    http_options=google_types.HttpOptions(
        timeout=max(250, LLM_TIMEOUT) * 1000
    )
)

def _build_safety_settings() -> list:
    """Build SafetySetting list from config (env-overridable)."""
    _mapping = {
        "HARASSMENT": google_types.HarmCategory.HARM_CATEGORY_HARASSMENT,
        "HATE_SPEECH": google_types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        "SEXUAL": google_types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        "DANGEROUS": google_types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
    }
    result = []
    for key, cat in _mapping.items():
        threshold_str = SAFETY_SETTINGS_CONFIG.get(key, "BLOCK_NONE")
        threshold = getattr(google_types.HarmBlockThreshold, threshold_str, google_types.HarmBlockThreshold.BLOCK_NONE)
        result.append(google_types.SafetySetting(category=cat, threshold=threshold))
    return result


SAFETY_SETTINGS = _build_safety_settings()


def make_config(**kwargs: Any) -> google_types.GenerateContentConfig:
    # Устанавливаем max_output_tokens по умолчанию, если не передан
    if 'max_output_tokens' not in kwargs:
        kwargs['max_output_tokens'] = 8192
    return google_types.GenerateContentConfig(safety_settings=SAFETY_SETTINGS, **kwargs)


def make_search_config(**kwargs: Any) -> google_types.GenerateContentConfig:
    return make_config(
        tools=[google_types.Tool(google_search=google_types.GoogleSearch())],
        **kwargs,
    )


def history_item(role: str, text: str) -> dict:
    return {"role": role, "parts": [{"text": text}]}


def oneshot(prompt: str, model: str = MODEL_NAME) -> str:
    import time
    from companion.config import LLM_RETRIES, LLM_RETRY_DELAY
    last_exc = None
    for attempt in range(LLM_RETRIES):
        try:
            temp = client.chats.create(model=model, config=make_config())
            r = temp.send_message(prompt)
            return (r.text or "").strip().replace("```json", "").replace("```", "").strip()
        except Exception as e:
            logger.error("oneshot call failed (attempt %d/%d): %s", attempt + 1, LLM_RETRIES, e)
            last_exc = e
            if attempt < LLM_RETRIES - 1:
                delay = LLM_RETRY_DELAY * (2 ** attempt)
                time.sleep(delay)
    raise last_exc or RuntimeError("oneshot call failed: unknown error")


def parse_json_array(text: str) -> list:
    import json  # noqa: PLC0415

    text = text.strip()
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as e:
            # Fallback: try to extract from markdown code block
            import re
            match = re.search(r'```json\s*(\[.*?\])\s*```', text, re.DOTALL)
            if match:
                return json.loads(match.group(1))
            # Log failure and return empty
            import logging
            logging.getLogger(__name__).error(f"JSON array parse failed: {e}, text: {text[:200]}")
            return []
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        import logging
        logging.getLogger(__name__).error(f"JSON array parse failed (fallback): {e}")
        return []


def parse_json_object(text: str) -> dict:
    import json  # noqa: PLC0415

    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as e:
            # Fallback: try to extract from markdown code block
            import re
            match = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL)
            if match:
                return json.loads(match.group(1))
            # Log failure and return empty
            import logging
            logging.getLogger(__name__).error(f"JSON object parse failed: {e}, text: {text[:200]}")
            return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        import logging
        logging.getLogger(__name__).error(f"JSON object parse failed (fallback): {e}")
        return {}


def format_grounding_sources(response: Any) -> str:
    lines: list[str] = []
    seen: set = set()
    try:
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return ""
        meta = getattr(candidates[0], "grounding_metadata", None)
        if not meta:
            return ""
        for chunk in getattr(meta, "grounding_chunks", None) or []:
            web = getattr(chunk, "web", None)
            if not web:
                continue
            uri = getattr(web, "uri", "") or ""
            title = getattr(web, "title", "") or uri
            if uri and uri not in seen:
                seen.add(uri)
                lines.append(f"• {title}\n  {uri}")
    except Exception:
        pass
    return "\n".join(lines)


def search_with_grounding(query: str, context: str = "") -> tuple[str, str]:
    """Google Search с grounding через Gemini 2.5 Flash."""
    # Формируем персонализированный запрос
    if context:
        system_instruction = (
            "Ты — персональный ассистент Ивана. "
            "Используй Google Search для актуальных данных. "
            "Интерпретируй результаты поиска с учетом личного контекста пользователя. "
            "Отвечай на русском с лёгким цинизмом в стиле Ивана.\n\n"
            f"Контекст о пользователе:\n{context[:1500]}"
        )
        prompt = query
    else:
        system_instruction = (
            "Отвечай на русском. Используй Google Search для актуальных данных. "
            "Факты — только из поиска."
        )
        prompt = query

    response = client.models.generate_content(
        model=SEARCH_MODEL,
        contents=prompt,
        config=make_search_config(
            system_instruction=system_instruction,
            temperature=0.4,
        ),
    )
    # Safe text extraction with fallback
    text = getattr(response, 'text', None) or ""
    text = text.strip()
    if not text:
        import logging
        logging.getLogger(__name__).error(f"Empty response from search model {SEARCH_MODEL}")
        raise ValueError("Пустой ответ от модели")
    return text, format_grounding_sources(response)


def upload_file(path: str) -> Any:
    return client.files.upload(file=path)


def get_file(name: str) -> Any:
    return client.files.get(name=name)


def delete_file(name: str) -> None:
    client.files.delete(name=name)


# ── Native async Gemini client ────────────────────────────────────────

_aio_client = None


def _get_aio_client():
    global _aio_client
    if _aio_client is None:
        from google import genai
        from google.genai import types
        from companion.config import GOOGLE_API_KEY, LLM_TIMEOUT
        _aio_client = genai.Client(
            api_key=GOOGLE_API_KEY,
            http_options=types.HttpOptions(
                timeout=max(250, LLM_TIMEOUT) * 1000
            )
        )
    return _aio_client


async def aio_oneshot(prompt: str, model: str = MODEL_NAME) -> str:
    c = _get_aio_client()
    temp = c.aio.chats.create(model=model, config=make_config())
    r = await temp.send_message(prompt)
    return (r.text or "").strip().replace("```json", "").replace("```", "").strip()


async def aio_search_with_grounding(query: str, context: str = "") -> tuple[str, str]:
    from companion.config import SEARCH_MODEL
    c = _get_aio_client()
    if context:
        system_instruction = (
            "Ты — персональный ассистент Ивана. "
            "Используй Google Search для актуальных данных. "
            "Интерпретируй результаты поиска с учетом личного контекста пользователя. "
            "Отвечай на русском с лёгким цинизмом в стиле Ивана.\n\n"
            f"Контекст о пользователе:\n{context[:1500]}"
        )
    else:
        system_instruction = (
            "Отвечай на русском. Используй Google Search для актуальных данных. "
            "Факты — только из поиска."
        )
    response = await c.aio.models.generate_content(
        model=SEARCH_MODEL,
        contents=query,
        config=make_search_config(
            system_instruction=system_instruction,
            temperature=0.4,
        ),
    )
    text = getattr(response, 'text', None) or ""
    text = text.strip()
    if not text:
        raise ValueError("Пустой ответ от модели")
    return text, format_grounding_sources(response)


async def aio_upload_file(path: str) -> Any:
    c = _get_aio_client()
    return await c.aio.files.upload(file=path)


async def aio_get_file(name: str) -> Any:
    c = _get_aio_client()
    return await c.aio.files.get(name=name)


async def aio_delete_file(name: str) -> None:
    c = _get_aio_client()
    await c.aio.files.delete(name=name)


# ── Async wrappers for use from async contexts (thread fallback) ──────

async def async_oneshot(prompt: str, model: str = MODEL_NAME) -> str:
    return await asyncio.to_thread(oneshot, prompt, model)


async def async_search_with_grounding(query: str, context: str = "") -> tuple[str, str]:
    return await asyncio.to_thread(search_with_grounding, query, context)


async def async_upload_file(path: str) -> Any:
    return await asyncio.to_thread(upload_file, path)


async def async_delete_file(name: str) -> None:
    await asyncio.to_thread(delete_file, name)


# ── Retry wrapper with exponential backoff ────────────────────────────

from companion.config import LLM_RETRIES, LLM_RETRY_DELAY, LLM_TIMEOUT


async def run_llm(
    sync_func, *args, timeout: int = LLM_TIMEOUT, retries: int = LLM_RETRIES, **kwargs
):
    """Run a sync LLM function in a thread with timeout and retry with exponential backoff."""
    last_exc = None
    for attempt in range(retries):
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(sync_func, *args, **kwargs),
                timeout=timeout,
            )
        except TimeoutError:
            logger.error("LLM call timed out (%ss) attempt %d/%d", timeout, attempt + 1, retries)
            last_exc = TimeoutError(f"LLM call timed out after {timeout}s")
        except Exception as e:
            logger.error("LLM call failed (attempt %d/%d): %s", attempt + 1, retries, e)
            last_exc = e
        if attempt < retries - 1:
            delay = LLM_RETRY_DELAY * (2 ** attempt)
            logger.info("Retrying in %ds...", delay)
            await asyncio.sleep(delay)
    raise last_exc or RuntimeError("LLM call failed: unknown error")


# ── Structured Outputs with Pydantic ──────────────────────────────────

from pydantic import BaseModel, Field
from typing import Literal, Dict, List, Optional

class UserMood(BaseModel):
    anxiety: float = Field(default=0.0, ge=0.0, le=1.0)
    anger: float = Field(default=0.0, ge=0.0, le=1.0)
    sadness: float = Field(default=0.0, ge=0.0, le=1.0)
    energy: float = Field(default=0.5, ge=0.0, le=1.0)

class MessageAnalysis(BaseModel):
    intent: Literal["world", "memory", "mixed", "command", "chat_casual"]
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    user_mood: UserMood
    user_state: Literal["ANXIOUS", "DEPRESSED", "CURIOUS", "OVERWHELMED", "NORMAL"]
    estimated_importance: int = Field(default=5, ge=1, le=10)
    command: str = Field(default="")

class FactItem(BaseModel):
    fact: str
    memory_kind: Literal["permanent", "state", "event"]
    importance: int = Field(default=5, ge=1, le=10)
    confidence: float = Field(default=0.75, ge=0.0, le=1.0)
    tags: List[str] = Field(default_factory=list)
    evidence_messages: List[str] = Field(default_factory=list)

class FactExtractionResult(BaseModel):
    facts: List[FactItem]

class ConsolidationItem(BaseModel):
    new_fact_index: int
    existing_fact_id: str
    relation: Literal["supersedes", "contradicts", "confirms", "related_to"]
    reason: str = Field(default="")

class ConsolidationResult(BaseModel):
    relations: List[ConsolidationItem]

class CausalLinkItem(BaseModel):
    cause: str
    effect: str
    confidence: float = Field(default=0.75, ge=0.0, le=1.0)
    evidence: List[str] = Field(default_factory=list)
    mechanism: str = Field(default="")

class CausalLinkExtractionResult(BaseModel):
    links: List[CausalLinkItem]

class ReflectionItem(BaseModel):
    insight: str
    importance: int = Field(default=7, ge=1, le=10)
    confidence: float = Field(default=0.75, ge=0.0, le=1.0)

class ReflectionResult(BaseModel):
    reflections: List[ReflectionItem]

class PersonalityPipelineResult(BaseModel):
    interests_delta: Dict[str, int] = Field(default_factory=dict)
    values_to_add: List[str] = Field(default_factory=list)
    values_to_remove: List[str] = Field(default_factory=list)
    fears_to_add: List[str] = Field(default_factory=list)
    fears_to_remove: List[str] = Field(default_factory=list)
    beliefs_to_add: List[str] = Field(default_factory=list)
    beliefs_to_remove: List[str] = Field(default_factory=list)
    motivation_to_add: List[str] = Field(default_factory=list)
    motivation_to_remove: List[str] = Field(default_factory=list)
    strengths_to_add: List[str] = Field(default_factory=list)
    strengths_to_remove: List[str] = Field(default_factory=list)
    weaknesses_to_add: List[str] = Field(default_factory=list)
    weaknesses_to_remove: List[str] = Field(default_factory=list)
    habits_delta: Dict[str, str] = Field(default_factory=dict)
    relationships_delta: Dict[str, str] = Field(default_factory=dict)
    addictions_delta: Dict[str, str] = Field(default_factory=dict)
    changes: List[str] = Field(default_factory=list)

class KnowledgeDomainItem(BaseModel):
    domain: str
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)

class KnowledgeDomainsExtractionResult(BaseModel):
    domains: List[KnowledgeDomainItem]


def oneshot_structured(prompt: str, response_schema: type[BaseModel], model: str = MODEL_NAME) -> Any:
    """Run generate_content with structured JSON output and exponential backoff retries."""
    import time
    import json
    from companion.config import LLM_RETRIES, LLM_RETRY_DELAY
    last_exc = None
    config = make_config(
        response_mime_type="application/json",
        response_schema=response_schema,
        temperature=0.1,
    )
    for attempt in range(LLM_RETRIES):
        try:
            r = client.models.generate_content(
                model=model,
                contents=prompt,
                config=config,
            )
            text = (r.text or "").strip()
            if not text:
                raise ValueError("Empty response from model")
            try:
                data = json.loads(text)
                return response_schema.model_validate(data)
            except json.JSONDecodeError as jde:
                logger.error("JSON decode failed on attempt %d: %s. Raw text: %r", attempt + 1, jde, text)
                raise
            except Exception as ve:
                logger.error("Pydantic validation failed on attempt %d: %s. Raw text: %r", attempt + 1, ve, text)
                raise
        except Exception as e:
            logger.error("oneshot_structured call failed (attempt %d/%d): %s", attempt + 1, LLM_RETRIES, e)
            last_exc = e
            if attempt < LLM_RETRIES - 1:
                delay = LLM_RETRY_DELAY * (2 ** attempt)
                time.sleep(delay)
    raise last_exc or RuntimeError("oneshot_structured call failed: unknown error")


async def oneshot_structured_async(prompt: str, response_schema: type[BaseModel], model: str = MODEL_NAME) -> Any:
    """Async wrapper: runs the blocking structured call in a worker thread."""
    return await asyncio.to_thread(oneshot_structured, prompt, response_schema, model)

