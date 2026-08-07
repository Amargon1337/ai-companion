"""LLM Provider abstraction — swappable backends for language model calls.

Current implementation: GeminiProvider (wraps google-genai).
Future implementations: OpenAIProvider, AnthropicProvider, LocalProvider.

Architecture:
    LLMProvider (Protocol)
      ├── complete(prompt, config) → str
      ├── complete_structured(prompt, schema) → Pydantic model
      ├── embed(texts) → list[list[float]]
      └── chat(history, system_instruction, message) → str

    GeminiProvider — wraps google.genai.Client
    (OpenAIProvider) — wraps openai.Client (future)
    (LocalProvider) — wraps local model via ONNX/llama.cpp (future)

The provider is selected via config (LLM_PROVIDER env var) and injected
through the AppContainer. All LLM-dependent code goes through this
interface, never importing google.genai directly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ── Configuration ───────────────────────────────────────────────────────

@dataclass
class LLMConfig:
    """Configuration for an LLM provider.

    Attributes:
        model: Model name/identifier (provider-specific).
        temperature: Sampling temperature (0.0-2.0).
        max_output_tokens: Maximum response length.
        timeout: Request timeout in seconds.
        retries: Number of retry attempts on failure.
        retry_delay: Base delay between retries (exponential backoff).
        safety_settings: Provider-specific safety configuration.
    """
    model: str = "gemini-3.5-flash-lite"
    temperature: float = 0.7
    max_output_tokens: int = 8192
    timeout: int = 120
    retries: int = 3
    retry_delay: int = 4
    safety_settings: dict[str, str] = field(default_factory=dict)


@dataclass
class EmbeddingConfig:
    """Configuration for an embedding provider."""
    model: str = "gemini-embedding-2"
    dimension: int = 768
    timeout: int = 30
    retries: int = 2


# ── Protocol ────────────────────────────────────────────────────────────

@runtime_checkable
class LLMProvider(Protocol):
    """Abstract interface for language model providers.

    All providers must implement these methods. The return types are
    provider-agnostic (strings and dicts), so callers don't depend on
    any specific SDK.
    """

    def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        temperature: float | None = None,
        timeout: int | None = None,
    ) -> str:
        """Generate a text completion.

        Args:
            prompt: Input text.
            model: Override default model for this call.
            temperature: Override default temperature.
            timeout: Override default timeout.

        Returns:
            Generated text (stripped of markdown fences).
        """
        ...

    def complete_structured(
        self,
        prompt: str,
        response_schema: type,
        *,
        model: str | None = None,
        temperature: float = 0.1,
    ) -> Any:
        """Generate a structured (JSON) completion validated against a schema.

        Args:
            prompt: Input text.
            response_schema: Pydantic model class for validation.
            model: Override default model.
            temperature: Low temperature for structured output.

        Returns:
            Validated Pydantic model instance.
        """
        ...

    def chat(
        self,
        history: list[dict],
        system_instruction: str,
        message: str,
        *,
        model: str | None = None,
        temperature: float | None = None,
    ) -> str:
        """Send a message in a multi-turn conversation.

        Args:
            history: Previous messages [{role, parts: [{text}]}].
            system_instruction: System prompt.
            message: New user message.
            model: Override default model.
            temperature: Override default temperature.

        Returns:
            Model response text.
        """
        ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Abstract interface for embedding providers."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a batch of texts.

        Args:
            texts: Input texts to embed.

        Returns:
            List of embedding vectors (same order as input).

        Raises:
            Exception: If the embedding API fails.
        """
        ...

    @property
    def dimension(self) -> int:
        """Embedding vector dimensionality."""
        ...


# ── Gemini Implementation ──────────────────────────────────────────────

class GeminiProvider:
    """LLM provider backed by Google Gemini API.

    Wraps google.genai.Client with retry logic, timeout handling, and
    structured output support. This is the current production provider.
    """

    def __init__(
        self,
        api_key: str,
        config: LLMConfig | None = None,
        embedding_config: EmbeddingConfig | None = None,
    ) -> None:
        from google import genai
        from google.genai import types as google_types

        self._config = config or LLMConfig()
        self._embedding_config = embedding_config or EmbeddingConfig()
        self._client = genai.Client(
            api_key=api_key,
            http_options=google_types.HttpOptions(
                timeout=max(250, self._config.timeout) * 1000
            ),
        )
        self._google_types = google_types

    def complete(
        self,
        prompt: str,
        *,
        model: str | None = None,
        temperature: float | None = None,
        timeout: int | None = None,
    ) -> str:
        import time
        model = model or self._config.model
        temp = temperature if temperature is not None else self._config.temperature
        last_exc = None

        for attempt in range(self._config.retries):
            try:
                chat = self._client.chats.create(
                    model=model,
                    config=self._make_config(temperature=temp),
                )
                r = chat.send_message(prompt)
                return (r.text or "").strip().replace("```json", "").replace("```", "").strip()
            except Exception as e:
                last_exc = e
                if attempt < self._config.retries - 1:
                    delay = self._config.retry_delay * (2 ** attempt)
                    time.sleep(delay)
        raise last_exc or RuntimeError("Gemini complete() failed")

    def complete_structured(
        self,
        prompt: str,
        response_schema: type,
        *,
        model: str | None = None,
        temperature: float = 0.1,
    ) -> Any:
        import json
        import time
        model = model or self._config.model
        last_exc = None

        config = self._make_config(
            response_mime_type="application/json",
            response_schema=response_schema,
            temperature=temperature,
        )
        for attempt in range(self._config.retries):
            try:
                r = self._client.models.generate_content(
                    model=model, contents=prompt, config=config,
                )
                text = (r.text or "").strip()
                if not text:
                    try:
                        return response_schema.model_validate({})
                    except Exception:
                        return response_schema.model_validate(
                            {k: [] for k in response_schema.model_fields}
                        )
                data = json.loads(text)
                return response_schema.model_validate(data)
            except Exception as e:
                last_exc = e
                if attempt < self._config.retries - 1:
                    delay = self._config.retry_delay * (2 ** attempt)
                    time.sleep(delay)
        raise last_exc or RuntimeError("Gemini complete_structured() failed")

    def chat(
        self,
        history: list[dict],
        system_instruction: str,
        message: str,
        *,
        model: str | None = None,
        temperature: float | None = None,
    ) -> str:
        model = model or self._config.model
        temp = temperature if temperature is not None else self._config.temperature
        chat = self._client.chats.create(
            model=model,
            history=history,
            config=self._make_config(
                system_instruction=system_instruction,
                temperature=temp,
            ),
        )
        r = chat.send_message(message)
        return (r.text or "").strip()

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings via Gemini API."""
        if not texts:
            return []
        from google.genai import types
        chunk_size = 90
        all_embeddings = []
        for i in range(0, len(texts), chunk_size):
            chunk = texts[i:i + chunk_size]
            result = self._client.models.embed_content(
                model=self._embedding_config.model,
                contents=chunk,
                config=types.EmbedContentConfig(
                    output_dimensionality=self._embedding_config.dimension
                ),
            )
            for embedding in result.embeddings:
                all_embeddings.append(list(embedding.values))
        return all_embeddings

    @property
    def dimension(self) -> int:
        return self._embedding_config.dimension

    def _make_config(self, **kwargs):
        """Build a GenerateContentConfig with safety settings."""
        from companion.config import SAFETY_SETTINGS_CONFIG
        mapping = {
            "HARASSMENT": self._google_types.HarmCategory.HARM_CATEGORY_HARASSMENT,
            "HATE_SPEECH": self._google_types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
            "SEXUAL": self._google_types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
            "DANGEROUS": self._google_types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
        }
        safety = []
        for key, cat in mapping.items():
            threshold_str = SAFETY_SETTINGS_CONFIG.get(key, "BLOCK_NONE")
            threshold = getattr(
                self._google_types.HarmBlockThreshold, threshold_str,
                self._google_types.HarmBlockThreshold.BLOCK_NONE
            )
            safety.append(self._google_types.SafetySetting(category=cat, threshold=threshold))

        if "max_output_tokens" not in kwargs:
            kwargs["max_output_tokens"] = self._config.max_output_tokens
        return self._google_types.GenerateContentConfig(safety_settings=safety, **kwargs)


# ── Factory ─────────────────────────────────────────────────────────────

def create_llm_provider(provider_type: str = "gemini", **kwargs) -> Any:
    """Create an LLM provider by type name.

    Args:
        provider_type: "gemini" (default), "openai" (future), "local" (future).
        **kwargs: Provider-specific arguments (api_key, config, etc.).

    Returns:
        An LLMProvider implementation.
    """
    if provider_type == "gemini":
        return GeminiProvider(**kwargs)
    else:
        raise ValueError(f"Unknown LLM provider: {provider_type}")


def create_embedding_provider(provider_type: str = "gemini", **kwargs) -> Any:
    """Create an embedding provider by type name."""
    if provider_type == "gemini":
        return GeminiProvider(**kwargs)
    else:
        raise ValueError(f"Unknown embedding provider: {provider_type}")
