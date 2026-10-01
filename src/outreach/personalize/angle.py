"""Chooses the collaboration angle for each creator with explicit, explainable rules.

The angle is decided here, not by the LLM, so that the "why this offer" can be shown to a
reviewer and stays consistent between runs. Rules are checked top to bottom; first match wins.
"""

from dataclasses import dataclass
from enum import StrEnum

from outreach.config import AngleThresholds

TUTORIAL_HINTS = ("tutorial", "how to", "course", "learn", "walkthrough", "explained", "lesson")
REVIEW_HINTS = ("review", "tool", "comparison", " vs ", "gadget", "setup", "unboxing")
BEGINNER_AUDIENCES = ("student", "beginner")


class CollaborationAngle(StrEnum):
    BRAND_AMBASSADOR = "BRAND_AMBASSADOR"
    SPONSORED_INTEGRATION = "SPONSORED_INTEGRATION"
    PAID_PLACEMENT = "PAID_PLACEMENT"
    AFFILIATE = "AFFILIATE"
    UGC = "UGC"
    BARTER = "BARTER"


@dataclass(frozen=True)
class AngleInputs:
    engagement_rate: float
    uploads_in_activity_window: int
    subscriber_count: int
    content_themes: list[str]
    audience_level: str


@dataclass(frozen=True)
class AngleChoice:
    angle: CollaborationAngle
    reason: str


def _mentions_any(themes: list[str], hints: tuple[str, ...]) -> bool:
    text = f" {' '.join(themes).lower()} "
    return any(hint in text for hint in hints)


def choose_angle(inputs: AngleInputs, thresholds: AngleThresholds) -> AngleChoice:
    if (
        inputs.engagement_rate >= thresholds.ambassador_min_engagement
        and inputs.uploads_in_activity_window >= thresholds.ambassador_min_uploads
    ):
        return AngleChoice(
            CollaborationAngle.BRAND_AMBASSADOR,
            f"High engagement ({inputs.engagement_rate:.1%}) with "
            f"{inputs.uploads_in_activity_window} recent uploads suits a long-term partnership",
        )
    if (
        _mentions_any(inputs.content_themes, TUTORIAL_HINTS)
        and inputs.subscriber_count >= thresholds.sponsorship_min_subscribers
    ):
        return AngleChoice(
            CollaborationAngle.SPONSORED_INTEGRATION,
            f"Tutorial-led channel with {inputs.subscriber_count:,} subscribers",
        )
    if _mentions_any(inputs.content_themes, REVIEW_HINTS):
        return AngleChoice(
            CollaborationAngle.PAID_PLACEMENT, "Reviews or compares tools, a natural placement"
        )
    if inputs.audience_level in BEGINNER_AUDIENCES:
        return AngleChoice(
            CollaborationAngle.AFFILIATE,
            f"Audience is mainly {inputs.audience_level}s, who respond to discount codes",
        )
    if inputs.subscriber_count < thresholds.ugc_max_subscribers:
        return AngleChoice(
            CollaborationAngle.UGC, "Smaller channel; paid UGC fits better than a sponsorship"
        )
    return AngleChoice(CollaborationAngle.BARTER, "No stronger signal; product access first")
