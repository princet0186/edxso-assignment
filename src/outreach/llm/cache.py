"""Persistent cache of LLM responses, keyed by everything that determines the answer."""

import hashlib
import json

from sqlmodel import Session

from outreach.llm.types import LlmRequest
from outreach.models import LlmCacheEntry


class LlmCache:
    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def key_for(request: LlmRequest) -> str:
        fingerprint = {
            "prompt_version": request.prompt_version,
            "temperature": request.temperature,
            "messages": [message.as_dict() for message in request.messages],
        }
        encoded = json.dumps(fingerprint, sort_keys=True, ensure_ascii=False).encode()
        return hashlib.sha256(encoded).hexdigest()

    def get(self, key: str) -> LlmCacheEntry | None:
        return self._session.get(LlmCacheEntry, key)

    def put(self, key: str, response_text: str, provider: str, model: str) -> None:
        self._session.merge(
            LlmCacheEntry(key=key, response_text=response_text, provider=provider, model=model)
        )
        self._session.commit()
