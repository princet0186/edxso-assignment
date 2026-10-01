"""Value types shared by the LLM client, its cache and its callers."""

from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"role": str(self.role), "content": self.content}


@dataclass(frozen=True)
class LlmRequest:
    messages: tuple[ChatMessage, ...]
    temperature: float
    prompt_version: str


@dataclass(frozen=True)
class LlmResult[T]:
    value: T
    provider: str
    model: str
    from_cache: bool
