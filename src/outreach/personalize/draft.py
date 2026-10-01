"""Schema of a generated outreach draft — what the LLM must return."""

from typing import Literal

from pydantic import BaseModel, Field

Signal = Literal[
    "recent_video", "niche", "tone", "audience", "collaboration", "value_proposition", "geography"
]


class OutreachDraft(BaseModel):
    email_subject: str = Field(min_length=3)
    email_body: str = Field(min_length=1)
    instagram_dm: str = Field(min_length=1)
    referenced_video_title: str | None = None
    signals_used: list[Signal] = Field(default_factory=list)
