"""Classification stage: niche, content themes, tone, audience and relevance via the LLM.

Creators are classified in small batches so ~260 channels fit inside free-tier request limits.
Brand safety is double-checked with a deterministic phrase list, so it never rests on the
LLM alone.
"""

import json
import logging
import re
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field
from sqlmodel import col, desc, select

from outreach.llm.client import LlmClient, LlmUnavailableError, build_llm_client
from outreach.llm.types import ChatMessage, LlmRequest, LlmResult, Role
from outreach.models import Classification, Creator, CreatorMetrics, Video
from outreach.prompts import load_prompt
from outreach.stage import StageContext, StageReport

STAGE_NAME = "classify"
PROMPT_VERSION = "classify_v1"
CHANNEL_DESCRIPTION_CHARS = 600
VIDEO_DESCRIPTION_CHARS = 150

# Phrases, not single words: "crack" alone would flag "Cracking the Coding Interview".
BRAND_SAFETY_PHRASES = {
    "piracy": ("cracked software", "crack version", "mod apk", "keygen", "free license key"),
    "gambling": ("betting", "casino", "gambling", "satta"),
    "account hacking": ("hack instagram", "hack facebook", "hack whatsapp", "hack any account"),
    "get-rich-quick": ("get rich quick", "earn money fast", "double your money"),
}

logger = logging.getLogger(__name__)

Niche = Literal[
    "Technology", "EdTech", "Gaming", "Finance", "Business", "Entertainment", "Lifestyle", "Other"
]


class CreatorClassification(BaseModel):
    channel_id: str
    primary_niche: Niche
    sub_niches: list[str] = Field(max_length=6)
    content_themes: list[str] = Field(min_length=1, max_length=6)
    tone: str
    audience_level: Literal["student", "beginner", "professional", "mixed"]
    language: Literal["en", "hi-en", "hi", "other"]
    relevance: float = Field(ge=0, le=1)
    brand_safety_flags: list[str]
    evidence: list[str]


class ClassificationBatch(BaseModel):
    creators: list[CreatorClassification]


def find_brand_safety_flags(texts: Sequence[str]) -> list[str]:
    corpus = " ".join(texts).lower()
    return [
        label
        for label, phrases in BRAND_SAFETY_PHRASES.items()
        if any(re.search(rf"\b{re.escape(phrase)}\b", corpus) for phrase in phrases)
    ]


def classify_creators(context: StageContext) -> StageReport:
    session = context.session
    already_classified = select(Classification.creator_id)
    pending = session.exec(
        select(Creator)
        .join(CreatorMetrics, col(CreatorMetrics.creator_id) == col(Creator.id))
        .where(col(Creator.id).not_in(already_classified))
    ).all()
    to_classify = context.limited(list(pending))
    llm = build_llm_client(context.secrets, context.settings.llm, session)

    classified = failed = 0
    batch_size = context.settings.llm.classify_batch_size
    for start in range(0, len(to_classify), batch_size):
        batch = to_classify[start : start + batch_size]
        saved = _classify_and_save_batch(context, llm, batch)
        classified += saved
        failed += len(batch) - saved
        logger.info("Classified %d/%d creators", classified, len(to_classify))
    return StageReport({"creators_classified": classified, "classification_failures": failed})


def _classify_and_save_batch(context: StageContext, llm: LlmClient, batch: list[Creator]) -> int:
    videos_by_creator = {creator.id: _recent_videos(context, creator) for creator in batch}
    try:
        result = llm.complete_json(
            _build_request(context, batch, videos_by_creator), ClassificationBatch
        )
    except LlmUnavailableError as exc:
        for creator in batch:
            context.record_error(STAGE_NAME, f"LLM unavailable: {exc}", creator.id)
        context.session.commit()
        return 0

    by_channel_id = {item.channel_id: item for item in result.value.creators}
    saved = 0
    for creator in batch:
        item = by_channel_id.get(creator.platform_id)
        if item is None:
            context.record_error(STAGE_NAME, "LLM returned no classification", creator.id)
            continue
        context.session.add(_to_row(creator, item, videos_by_creator[creator.id], result))
        saved += 1
    context.session.commit()
    return saved


def _recent_videos(context: StageContext, creator: Creator) -> list[Video]:
    return list(
        context.session.exec(
            select(Video)
            .where(Video.creator_id == creator.id)
            .order_by(desc(col(Video.published_at)))
        ).all()
    )


def _build_request(
    context: StageContext, batch: list[Creator], videos_by_creator: dict[int | None, list[Video]]
) -> LlmRequest:
    prompt = load_prompt(PROMPT_VERSION)
    channels = [
        {
            "channel_id": creator.platform_id,
            "name": creator.name,
            "description": creator.description[:CHANNEL_DESCRIPTION_CHARS],
            "topic_tags": creator.topic_categories,
            "country": creator.country,
            "recent_videos": [
                {"title": video.title, "description": video.description[:VIDEO_DESCRIPTION_CHARS]}
                for video in videos_by_creator[creator.id]
            ],
        }
        for creator in batch
    ]
    return LlmRequest(
        messages=(
            ChatMessage(Role.SYSTEM, prompt.render(niche_name=context.settings.niche_name)),
            ChatMessage(Role.USER, json.dumps({"channels": channels}, ensure_ascii=False)),
        ),
        temperature=context.settings.llm.classify_temperature,
        prompt_version=prompt.version,
    )


def _to_row(
    creator: Creator,
    item: CreatorClassification,
    videos: list[Video],
    result: LlmResult[ClassificationBatch],
) -> Classification:
    scanned_texts = [creator.name, creator.description, *(v.title for v in videos)]
    flags = sorted({*item.brand_safety_flags, *find_brand_safety_flags(scanned_texts)})
    return Classification(
        creator_id=creator.id,
        primary_niche=item.primary_niche,
        sub_niches=item.sub_niches,
        content_themes=item.content_themes,
        tone=item.tone,
        audience_level=item.audience_level,
        language=item.language,
        relevance=item.relevance,
        brand_safety_flags=flags,
        evidence=item.evidence,
        provider=result.provider,
        model=result.model,
        prompt_version=PROMPT_VERSION,
    )
