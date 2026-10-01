"""Builds the creator brief the LLM writes from. Only stored, real data goes in — which is
what lets the validators reject any number or video the model did not get from us."""

import json
import re
from dataclasses import dataclass

from sqlmodel import Session, col, desc, select

from outreach.config import BrandProfile, PersonalizationSettings, Settings
from outreach.models import Classification, Creator, CreatorMetrics, Video
from outreach.personalize.angle import AngleChoice, AngleInputs, choose_angle
from outreach.personalize.validators import NUMBER_PATTERN, ValidationContext

# "Code With Asha | Python & DSA" -> "Code With Asha"
NAME_SEPARATOR = re.compile(r"\s+[|\-–—•:]\s+|\s*[|•]\s*")
PUBLISHED_LIVE_STATUS = "none"


def address_name(channel_name: str) -> str:
    return NAME_SEPARATOR.split(channel_name.strip(), maxsplit=1)[0].strip() or channel_name


@dataclass(frozen=True)
class RecentVideo:
    title: str
    published: str


@dataclass(frozen=True)
class CreatorBrief:
    creator_id: int
    address_as: str
    channel_name: str
    niche: str
    sub_niches: list[str]
    content_themes: list[str]
    tone: str
    audience_level: str
    country: str | None
    recent_videos: list[RecentVideo]
    angle: AngleChoice

    def facts_payload(self, brand: BrandProfile) -> dict:
        """Everything the model may state as fact. Validators check numbers against this."""
        return {
            "creator": {
                "address_as": self.address_as,
                "channel_name": self.channel_name,
                "niche": self.niche,
                "sub_niches": self.sub_niches,
                "content_themes": self.content_themes,
                "tone": self.tone,
                "audience_level": self.audience_level,
                "country": self.country,
                "recent_videos": [vars(video) for video in self.recent_videos],
            },
            "brand": {
                "name": brand.name,
                "product": brand.product,
                "audience": brand.audience,
                "value_propositions": brand.value_propositions,
            },
            "collaboration": {
                "type": self.angle.angle.replace("_", " ").lower(),
                "offer": brand.offers[self.angle.angle],
                "why_it_fits": self.angle.reason,
            },
        }

    def validation_context(
        self, brand: BrandProfile, limits: PersonalizationSettings
    ) -> ValidationContext:
        facts_text = json.dumps(self.facts_payload(brand), ensure_ascii=False)
        return ValidationContext(
            address_as=self.address_as,
            recent_titles=tuple(video.title for video in self.recent_videos),
            allowed_numbers=frozenset(NUMBER_PATTERN.findall(facts_text)),
            limits=limits,
        )


def build_brief(session: Session, creator: Creator, settings: Settings) -> CreatorBrief:
    classification = session.get(Classification, creator.id)
    metrics = session.get(CreatorMetrics, creator.id)
    recent = session.exec(
        select(Video)
        .where(Video.creator_id == creator.id, Video.live_status == PUBLISHED_LIVE_STATUS)
        .order_by(desc(col(Video.published_at)))
        .limit(settings.personalization.recent_videos_in_brief)
    ).all()
    angle = choose_angle(
        AngleInputs(
            engagement_rate=metrics.engagement_rate or 0.0,
            uploads_in_activity_window=metrics.uploads_in_activity_window,
            subscriber_count=creator.subscriber_count or 0,
            content_themes=[*classification.content_themes, *classification.sub_niches],
            audience_level=classification.audience_level,
        ),
        settings.personalization.angles,
    )
    return CreatorBrief(
        creator_id=creator.id,
        address_as=address_name(creator.name),
        channel_name=creator.name,
        niche=classification.primary_niche,
        sub_niches=classification.sub_niches,
        content_themes=classification.content_themes,
        tone=classification.tone,
        audience_level=classification.audience_level,
        country=creator.country,
        recent_videos=[RecentVideo(v.title, f"{v.published_at:%Y-%m-%d}") for v in recent],
        angle=angle,
    )
