import json
from datetime import UTC, datetime

from sqlmodel import Session, select
from tenacity import wait_none

from outreach import classify
from outreach.llm.cache import LlmCache
from outreach.llm.client import LlmClient
from outreach.models import (
    Classification,
    Creator,
    CreatorMetrics,
    EngagementMethod,
    PipelineError,
    Platform,
    Video,
)
from outreach.stage import StageContext


def test_brand_safety_phrases_flag_piracy_but_not_interview_prep() -> None:
    assert classify.find_brand_safety_flags(["Download Photoshop cracked software free"]) == [
        "piracy"
    ]
    assert classify.find_brand_safety_flags(["Cracking the Coding Interview in 30 days"]) == []


class FixedReplyBackend:
    name = "fake"
    model = "fake-model"

    def __init__(self, reply: dict) -> None:
        self._reply = json.dumps(reply)

    def complete(self, request) -> str:
        return self._reply


def add_measured_creator(session: Session, channel_id: str) -> Creator:
    creator = Creator(
        platform=Platform.YOUTUBE,
        platform_id=channel_id,
        name=f"Channel {channel_id}",
        profile_url="https://youtube.com/x",
        discovered_via_query="q",
    )
    session.add(creator)
    session.commit()
    session.add(
        CreatorMetrics(
            creator_id=creator.id,
            engagement_method=EngagementMethod.LONG_FORM,
            sample_size=3,
            uploads_in_activity_window=3,
        )
    )
    session.add(
        Video(
            video_id=f"{channel_id}-v1",
            creator_id=creator.id,
            title="Install cracked software (mod apk)",
            published_at=datetime(2026, 9, 1, tzinfo=UTC),
            duration_seconds=600,
        )
    )
    session.commit()
    return creator


def classification_for(channel_id: str) -> dict:
    return {
        "channel_id": channel_id,
        "primary_niche": "EdTech",
        "sub_niches": ["Python"],
        "content_themes": ["beginner Python tutorials"],
        "tone": "calm",
        "audience_level": "beginner",
        "language": "en",
        "relevance": 0.9,
        "brand_safety_flags": [],
        "evidence": ["Install cracked software (mod apk)"],
    }


def test_batch_saves_returned_creators_and_records_missing_ones(
    context: StageContext, session: Session, monkeypatch
) -> None:
    returned = add_measured_creator(session, "UC_returned")
    missing = add_measured_creator(session, "UC_missing")
    reply = {"creators": [classification_for("UC_returned")]}
    fake_llm = LlmClient([FixedReplyBackend(reply)], LlmCache(session), backoff=wait_none())
    monkeypatch.setattr(classify, "build_llm_client", lambda *args: fake_llm)

    report = classify.classify_creators(context)

    saved = session.exec(select(Classification)).one()
    assert saved.creator_id == returned.id
    assert saved.brand_safety_flags == ["piracy"]  # caught by the phrase list, not the LLM
    assert report.counts == {"creators_classified": 1, "classification_failures": 1}
    error = session.exec(select(PipelineError)).one()
    assert error.creator_id == missing.id
