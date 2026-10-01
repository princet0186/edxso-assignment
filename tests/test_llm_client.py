import httpx
import openai
import pytest
from pydantic import BaseModel
from sqlmodel import Session
from tenacity import wait_none

from outreach.config import Secrets, load_settings
from outreach.llm.cache import LlmCache
from outreach.llm.client import (
    CircuitBreaker,
    LlmClient,
    LlmUnavailableError,
    build_llm_client,
    parse_json_output,
)
from outreach.llm.types import ChatMessage, LlmRequest, Role


class Answer(BaseModel):
    niche: str


REQUEST = LlmRequest(
    messages=(ChatMessage(Role.USER, "classify"),), temperature=0.1, prompt_version="test_v1"
)


def rate_limit_error() -> openai.RateLimitError:
    response = httpx.Response(429, request=httpx.Request("POST", "https://llm.test/v1"))
    return openai.RateLimitError("rate limited", response=response, body=None)


class ScriptedBackend:
    """Returns (or raises) the scripted outcomes in order and counts calls."""

    def __init__(self, name: str, outcomes: list) -> None:
        self.name = name
        self.model = f"{name}-model"
        self._outcomes = outcomes
        self.calls = 0

    def complete(self, request: LlmRequest) -> str:
        outcome = self._outcomes[min(self.calls, len(self._outcomes) - 1)]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def make_client(session: Session, *backends: ScriptedBackend) -> LlmClient:
    return LlmClient(list(backends), LlmCache(session), backoff=wait_none())


def test_falls_back_to_next_provider_after_repeated_rate_limits(session: Session) -> None:
    primary = ScriptedBackend("gemini", [rate_limit_error()])
    fallback = ScriptedBackend("groq", ['{"niche": "EdTech"}'])

    result = make_client(session, primary, fallback).complete_json(REQUEST, Answer)

    assert result.value.niche == "EdTech"
    assert result.provider == "groq"
    assert primary.calls == 3


def test_malformed_json_is_retried_on_the_same_provider(session: Session) -> None:
    backend = ScriptedBackend("gemini", ["Sure! here you go", '```json\n{"niche": "Tech"}\n```'])

    result = make_client(session, backend).complete_json(REQUEST, Answer)

    assert result.value.niche == "Tech"
    assert backend.calls == 2


def test_second_identical_request_is_served_from_cache(session: Session) -> None:
    backend = ScriptedBackend("gemini", ['{"niche": "Tech"}'])
    client = make_client(session, backend)

    client.complete_json(REQUEST, Answer)
    second = client.complete_json(REQUEST, Answer)

    assert second.from_cache
    assert backend.calls == 1


def test_raises_when_every_provider_fails(session: Session) -> None:
    backends = [ScriptedBackend("gemini", [rate_limit_error()]), ScriptedBackend("groq", ["{}"])]

    with pytest.raises(LlmUnavailableError) as error:
        make_client(session, *backends).complete_json(REQUEST, Answer)

    assert "gemini" in str(error.value)
    assert "groq" in str(error.value)


def test_parse_json_output_rejects_schema_mismatch() -> None:
    with pytest.raises(ValueError):
        parse_json_output('{"unexpected": 1}', Answer)


def test_failing_primary_is_skipped_during_cooldown_then_retried(session: Session) -> None:
    now = [0.0]
    primary = ScriptedBackend("gemini", [rate_limit_error()])
    fallback = ScriptedBackend("groq", ['{"niche": "EdTech"}'])
    client = LlmClient(
        [primary, fallback], LlmCache(session), backoff=wait_none(), clock=lambda: now[0]
    )

    def ask(prompt: str) -> str:
        request = LlmRequest((ChatMessage(Role.USER, prompt),), 0.1, "test_v1")
        return client.complete_json(request, Answer).provider

    ask("first")
    ask("second")  # second consecutive failure opens the circuit
    calls_when_opened = primary.calls
    assert ask("third") == "groq"
    assert primary.calls == calls_when_opened  # skipped, no wasted retries

    now[0] += 301  # cooldown over: primary is tried again
    ask("fourth")
    assert primary.calls > calls_when_opened


def test_each_configured_model_becomes_its_own_fallback_step(session: Session) -> None:
    secrets = Secrets(
        _env_file=None,
        gemini_api_key="g-key",
        gemini_models="gemini-2.5-flash, gemini-3.1-flash-lite",
        groq_api_key="q-key",
        groq_models="openai/gpt-oss-120b",
    )

    client = build_llm_client(secrets, load_settings().llm, session)

    assert [(b.name, b.model) for b in client._backends] == [
        ("gemini", "gemini-2.5-flash"),
        ("gemini", "gemini-3.1-flash-lite"),
        ("groq", "openai/gpt-oss-120b"),
    ]


def test_breaker_cooldown_doubles_on_repeated_failure_and_is_capped() -> None:
    now = [0.0]
    breaker = CircuitBreaker(2, 300, clock=lambda: now[0], max_cooldown_seconds=1000)

    breaker.record_failure()
    assert not breaker.is_open()
    breaker.record_failure()  # opens for 300 s
    now[0] = 299
    assert breaker.is_open()
    now[0] = 301
    breaker.record_failure()  # still failing after cooldown: 600 s
    now[0] = 301 + 599
    assert breaker.is_open()
    now[0] = 301 + 601
    breaker.record_failure()  # 1200 s, capped at 1000
    now[0] += 1001
    assert not breaker.is_open()

    breaker.record_success()
    breaker.record_failure()
    assert not breaker.is_open()  # counter reset by the success
