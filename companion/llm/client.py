"""Gemini API client wrapper."""
from __future__ import annotations

import asyncio
import inspect
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


def history_item(role: str, text: str) -> dict:
    return {"role": role, "parts": [{"text": text}]}


def oneshot(prompt: str, model: str = MODEL_NAME, **kwargs: Any) -> str:
    import time
    from companion.config import LLM_RETRIES, LLM_RETRY_DELAY
    from companion.security.egress import prepare_external_payload
    prompt = prepare_external_payload(prompt, purpose="oneshot", model=model).payload
    last_exc = None
    for attempt in range(LLM_RETRIES):
        try:
            temp = client.chats.create(model=model, config=make_config(**kwargs))
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


async def aio_oneshot(prompt: str, model: str = MODEL_NAME, timeout: float = 25.0, **kwargs: Any) -> str:
    from companion.security.egress import prepare_external_payload
    prompt = prepare_external_payload(prompt, purpose="aio_oneshot", model=model).payload
    c = _get_aio_client()
    async def _call():
        temp = c.aio.chats.create(model=model, config=make_config(**kwargs))
        r = await temp.send_message(prompt)
        return (r.text or "").strip().replace("```json", "").replace("```", "").strip()

    try:
        return await asyncio.wait_for(_call(), timeout=timeout)
    except asyncio.TimeoutError:
        import logging
        logging.getLogger(__name__).error(f"Gemini API aio_oneshot timed out after {timeout}s")
        raise TimeoutError(f"Gemini API timeout after {timeout}s")


async def aio_oneshot_multimodal(contents: list[Any], model: str = MODEL_NAME, timeout: float = 25.0, **kwargs: Any) -> str:
    from companion.security.egress import prepare_external_payload
    # Only textual parts are inspectable/redactable here. Binary media is
    # governed by upload policy and is never logged as an egress payload.
    sanitized_contents = [
        prepare_external_payload(item, purpose="multimodal_text", model=model).payload
        if isinstance(item, str) else item
        for item in contents
    ]
    contents = sanitized_contents
    c = _get_aio_client()
    async def _call():
        r = await c.aio.models.generate_content(model=model, contents=contents, config=make_config(**kwargs))
        return (r.text or "").strip().replace("```json", "").replace("```", "").strip()

    try:
        return await asyncio.wait_for(_call(), timeout=timeout)
    except asyncio.TimeoutError:
        import logging
        logging.getLogger(__name__).error(f"Gemini API aio_oneshot_multimodal timed out after {timeout}s")
        raise TimeoutError(f"Gemini API timeout after {timeout}s")


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


async def async_upload_file(path: str) -> Any:
    return await asyncio.to_thread(upload_file, path)


async def async_delete_file(name: str) -> None:
    await asyncio.to_thread(delete_file, name)


# ── Retry wrapper with exponential backoff ────────────────────────────

from companion.config import LLM_RETRIES, LLM_RETRY_DELAY, LLM_TIMEOUT


async def run_llm(
    sync_func, *args, timeout: int = LLM_TIMEOUT, retries: int = LLM_RETRIES, **kwargs
):
    """Run an LLM call (sync OR async) with timeout + exponential backoff retry.

    `sync_func` may be:
      * a blocking function  -> run in a worker thread (default path), or
      * an async coroutine function -> awaited directly on the event loop.

    This keeps run_llm version-agnostic across google-genai releases where the
    same client method (e.g. ``chat.send_message``) switched between a sync
    signature and an async one. Without this check, an async function passed to
    asyncio.to_thread returns an un-awaited coroutine, so ``await run_llm(...)``
    yields the coroutine and downstream ``.text`` access crashes the caller
    (e.g. the compress pipeline).
    """
    last_exc = None
    for attempt in range(retries):
        try:
            if inspect.iscoroutinefunction(sync_func):
                coro = sync_func(*args, **kwargs)
                if timeout:
                    return await asyncio.wait_for(coro, timeout=timeout)
                return await coro
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
    needs_clarification: str = Field(default="", description="Текст уточняющего вопроса для заполнения пробелов в модели пользователя (gap-filling), если выявлена интересная тема без деталей. Иначе пустая строка.")

class FactItem(BaseModel):
    fact: str
    memory_kind: Literal["permanent", "state", "event"]
    # IDs must refer to source messages supplied to the extraction prompt.
    # They are mandatory provenance for a claim to be promoted above inference.
    evidence_messages: List[str] = Field(default_factory=list)
    importance: int = Field(default=5, ge=1, le=10)
    confidence: float = Field(default=0.75, ge=0.0, le=1.0)
    tags: List[str] = Field(default_factory=list)
    domain: Literal["user", "world", "system"] = Field(default="user")


class FactExtractionResult(BaseModel):
    facts: List[FactItem]


class EpisodeExtractionResult(BaseModel):
    """Эпизод: cвязная иcтория из группы фактов за период."""
    title: str = Field(default="")
    narrative: str = Field(default="")
    participants: List[str] = Field(default_factory=list)
    emotions: Dict[str, float] = Field(default_factory=dict)  # joy|sadness|anger|fear|hope -> 0..1
    lesson: str = Field(default="")
    importance: int = Field(default=7, ge=1, le=10)


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

class PatternItem(BaseModel):
    pattern: str
    category: str = Field(default="behavior")  # behavior | coping | mistake | relationship | trend
    evidence: List[str] = Field(default_factory=list)
    importance: int = Field(default=6, ge=1, le=10)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)

class PatternExtractionResult(BaseModel):
    patterns: List[PatternItem]

class CommPrefItem(BaseModel):
    """Уровень 4: одно предпочтение общения. Пустое поле = 'не менялось'."""
    style: str = Field(default="")        # желаемый стиль общения
    formality: str = Field(default="")    # уровень формальности
    humor: str = Field(default="")        # отношение к юмору
    language: str = Field(default="")     # предпочтительный язык
    liked_topics: List[str] = Field(default_factory=list)
    avoided_topics: List[str] = Field(default_factory=list)

class CommPrefExtractionResult(BaseModel):
    comm_pref: CommPrefItem

class HumanModelItem(BaseModel):
    """Уровень 6: выводы о человеке. Пустой список = 'без изменений' (не очищает)."""
    goals: List[str] = Field(default_factory=list)            # цели пользователя
    fears: List[str] = Field(default_factory=list)            # страхи
    strengths: List[str] = Field(default_factory=list)        # сильные стороны
    recurring_mistakes: List[str] = Field(default_factory=list)  # повторяющиеся ошибки
    long_term_trends: List[str] = Field(default_factory=list)    # долгосрочные тенденции

class HumanModelExtractionResult(BaseModel):
    human_model: HumanModelItem

class LifeTransitionItem(BaseModel):
    """LCE: один устойчивый переход состояния. Пустой — не создавать."""
    domain: str = Field(default="identity")  # identity|career|relationships|habits|interests|worldview|mental_state
    from_state: str = Field(default="")
    to_state: str = Field(default="")
    explanation: str = Field(default="")
    trigger_events: List[str] = Field(default_factory=list)  # описания/факты-основания
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)

class LifeTransitionExtractionResult(BaseModel):
    transitions: List[LifeTransitionItem]

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
    """Run structured output through the same external-data boundary."""
    import time
    from companion.security.egress import prepare_external_payload
    prompt = prepare_external_payload(prompt, purpose="structured", model=model).payload
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
                logger.warning(f"Empty response from model. Fallback to empty schema dict. Config schema: {response_schema.__name__}")
                try:
                    return response_schema.model_validate({})
                except Exception:
                    empty_data = {k: [] for k in response_schema.model_fields.keys()}
                    return response_schema.model_validate(empty_data)
            try:
                data = json.loads(text)
                return response_schema.model_validate(data)
            except json.JSONDecodeError as jde:
                logger.error("JSON decode failed on attempt %d: %s (response length=%d)", attempt + 1, jde, len(text))
                raise
            except Exception as ve:
                logger.error("Pydantic validation failed on attempt %d: %s (response length=%d)", attempt + 1, ve, len(text))
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

