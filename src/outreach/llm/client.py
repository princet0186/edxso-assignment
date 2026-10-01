"""One JSON-completion interface over several OpenAI-compatible providers.

Free tiers fail in ordinary ways — rate limits, "high demand" 503s, the odd malformed reply —
so every call is paced, retried with backoff, validated against a schema, and falls back to
the next provider before giving up. Successful answers are cached in the database.
"""

import json
import logging
import time
from dataclasses import dataclass
from typing import Protocol

import openai
from pydantic import BaseModel, ValidationError
from sqlmodel import Session
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential
from tenacity.wait import wait_base

from outreach.config import LlmSettings, MissingConfigError, Secrets
from outreach.llm.cache import LlmCache
from outreach.llm.types import LlmRequest, LlmResult

ATTEMPTS_PER_PROVIDER = 3
DEFAULT_BACKOFF = wait_exponential(multiplier=2, max=30)
SECONDS_PER_MINUTE = 60
JSON_RESPONSE_FORMAT = {"type": "json_object"}

logger = logging.getLogger(__name__)


class InvalidLlmOutput(ValueError):
    """The provider answered, but not with JSON matching the expected schema."""


class LlmUnavailableError(RuntimeError):
    """Every configured provider failed for this request."""


RETRYABLE_ERRORS = (
    openai.RateLimitError,
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.InternalServerError,
    InvalidLlmOutput,
)


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    api_key: str
    model: str
    requests_per_minute: int


class CompletionBackend(Protocol):
    name: str
    model: str

    def complete(self, request: LlmRequest) -> str: ...


class OpenAICompatibleBackend:
    def __init__(self, config: ProviderConfig, timeout_seconds: float) -> None:
        self.name = config.name
        self.model = config.model
        # Retries are handled here (with fallback); the SDK's own retries would hide them.
        self._client = openai.OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=timeout_seconds,
            max_retries=0,
        )
        self._min_interval = SECONDS_PER_MINUTE / config.requests_per_minute
        self._last_call_at = 0.0

    def complete(self, request: LlmRequest) -> str:
        self._wait_for_rate_limit_slot()
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[message.as_dict() for message in request.messages],
            temperature=request.temperature,
            response_format=JSON_RESPONSE_FORMAT,
        )
        return response.choices[0].message.content or ""

    def _wait_for_rate_limit_slot(self) -> None:
        wait = self._min_interval - (time.monotonic() - self._last_call_at)
        if wait > 0:
            time.sleep(wait)
        self._last_call_at = time.monotonic()


def parse_json_output[T: BaseModel](text: str, schema: type[T]) -> T:
    """Accepts plain JSON or JSON wrapped in prose/code fences, then validates the schema."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise InvalidLlmOutput(f"No JSON object in response: {text[:200]!r}")
    try:
        return schema.model_validate(json.loads(text[start : end + 1]))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise InvalidLlmOutput(str(exc)[:500]) from exc


class LlmClient:
    def __init__(
        self,
        backends: list[CompletionBackend],
        cache: LlmCache,
        backoff: wait_base = DEFAULT_BACKOFF,
    ) -> None:
        if not backends:
            raise MissingConfigError("No LLM provider is configured (see docs/SETUP.md)")
        self._backends = backends
        self._cache = cache
        self._backoff = backoff

    def complete_json[T: BaseModel](self, request: LlmRequest, schema: type[T]) -> LlmResult[T]:
        cache_key = self._cache.key_for(request)
        cached = self._cache.get(cache_key)
        if cached:
            try:
                value = parse_json_output(cached.response_text, schema)
                return LlmResult(value, cached.provider, cached.model, from_cache=True)
            except InvalidLlmOutput:
                logger.info("Ignoring cached response that no longer matches the schema")

        failures: list[str] = []
        for backend in self._backends:
            try:
                text, value = self._complete_with_retries(backend, request, schema)
            except (*RETRYABLE_ERRORS, openai.APIStatusError) as exc:
                failures.append(f"{backend.name}: {type(exc).__name__}: {str(exc)[:200]}")
                logger.warning("LLM provider %s failed; trying next. %s", backend.name, exc)
                continue
            self._cache.put(cache_key, text, backend.name, backend.model)
            return LlmResult(value, backend.name, backend.model, from_cache=False)
        raise LlmUnavailableError(" | ".join(failures))

    def _complete_with_retries[T: BaseModel](
        self, backend: CompletionBackend, request: LlmRequest, schema: type[T]
    ) -> tuple[str, T]:
        def complete_and_parse() -> tuple[str, T]:
            text = backend.complete(request)
            return text, parse_json_output(text, schema)

        retrying = Retrying(
            retry=retry_if_exception_type(RETRYABLE_ERRORS),
            stop=stop_after_attempt(ATTEMPTS_PER_PROVIDER),
            wait=self._backoff,
            reraise=True,
        )
        return retrying(complete_and_parse)


def _provider_config(name: str, secrets: Secrets, settings: LlmSettings) -> ProviderConfig:
    if name not in settings.requests_per_minute:
        raise MissingConfigError(f"llm.requests_per_minute has no entry for provider {name!r}")
    connection_by_provider = {
        "gemini": (secrets.gemini_base_url, secrets.gemini_api_key, secrets.gemini_model),
        "groq": (secrets.groq_base_url, secrets.groq_api_key, secrets.groq_model),
    }
    if name not in connection_by_provider:
        raise MissingConfigError(f"Unknown LLM provider {name!r} in LLM_PROVIDERS")
    base_url, api_key, model = connection_by_provider[name]
    return ProviderConfig(name, base_url, api_key, model, settings.requests_per_minute[name])


def build_llm_client(secrets: Secrets, settings: LlmSettings, session: Session) -> LlmClient:
    backends: list[CompletionBackend] = []
    for name in secrets.provider_order:
        config = _provider_config(name, secrets, settings)
        if not config.api_key:
            logger.warning("Skipping LLM provider %s: no API key configured", name)
            continue
        backends.append(OpenAICompatibleBackend(config, settings.request_timeout_seconds))
    return LlmClient(backends, LlmCache(session))
