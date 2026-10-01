"""Qualification rules for the Technology & EdTech category.

Every applicable rule is evaluated and every failure is reported, so the dataset says exactly
why a creator was rejected. One exception: creators outside the subscriber range get only the
size reason, because their videos are deliberately never fetched (to save API quota).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from outreach.config import FilterSettings
from outreach.models import Classification, Creator, CreatorMetrics


class RejectionCode(StrEnum):
    SUBSCRIBERS_HIDDEN = "SUBSCRIBERS_HIDDEN"
    SUBSCRIBERS_OUT_OF_RANGE = "SUBSCRIBERS_OUT_OF_RANGE"
    NO_VIDEO_DATA = "NO_VIDEO_DATA"
    INACTIVE = "INACTIVE"
    ENGAGEMENT_UNAVAILABLE = "ENGAGEMENT_UNAVAILABLE"
    ENGAGEMENT_TOO_LOW = "ENGAGEMENT_TOO_LOW"
    NOT_CLASSIFIED = "NOT_CLASSIFIED"
    NICHE_MISMATCH = "NICHE_MISMATCH"
    LOW_RELEVANCE = "LOW_RELEVANCE"
    BRAND_SAFETY = "BRAND_SAFETY"
    LANGUAGE = "LANGUAGE"
    GEOGRAPHY = "GEOGRAPHY"


@dataclass(frozen=True)
class Rejection:
    code: RejectionCode
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": str(self.code), "message": self.message}


@dataclass(frozen=True)
class CreatorFacts:
    creator: Creator
    metrics: CreatorMetrics | None
    classification: Classification | None


@dataclass(frozen=True)
class QualificationRules:
    filters: FilterSettings
    activity_window_days: int
    now: datetime


def evaluate(facts: CreatorFacts, rules: QualificationRules) -> list[Rejection]:
    size_rejection = _check_size(facts.creator, rules.filters)
    if size_rejection:
        return [size_rejection]
    if facts.metrics is None:
        return [Rejection(RejectionCode.NO_VIDEO_DATA, "Recent videos could not be fetched")]

    rejections = [
        _check_activity(facts.metrics, rules),
        _check_engagement(facts.metrics, rules.filters),
    ]
    rejections.extend(_check_classification(facts.classification, rules.filters))
    rejections.append(_check_geography(facts.creator, rules.filters))
    return [rejection for rejection in rejections if rejection is not None]


def _check_size(creator: Creator, filters: FilterSettings) -> Rejection | None:
    if creator.subscribers_hidden or creator.subscriber_count is None:
        return Rejection(RejectionCode.SUBSCRIBERS_HIDDEN, "Subscriber count hidden by creator")
    if filters.subscriber_range_contains(creator.subscriber_count):
        return None
    return Rejection(
        RejectionCode.SUBSCRIBERS_OUT_OF_RANGE,
        f"{creator.subscriber_count:,} subscribers is outside "
        f"{filters.subscribers_min:,}–{filters.subscribers_max:,}",
    )


def _check_activity(metrics: CreatorMetrics, rules: QualificationRules) -> Rejection | None:
    if metrics.last_upload_at is None:
        return Rejection(RejectionCode.INACTIVE, "No published uploads found")
    days_since_upload = (rules.now - metrics.last_upload_at).days
    if metrics.last_upload_at >= rules.now - timedelta(days=rules.activity_window_days):
        return None
    return Rejection(
        RejectionCode.INACTIVE,
        f"Last upload {metrics.last_upload_at:%Y-%m-%d} ({days_since_upload} days ago)",
    )


def _check_engagement(metrics: CreatorMetrics, filters: FilterSettings) -> Rejection | None:
    if metrics.engagement_rate is None:
        reason = metrics.unavailable_reason or "Engagement rate unavailable"
        return Rejection(RejectionCode.ENGAGEMENT_UNAVAILABLE, reason)
    if metrics.engagement_rate >= filters.min_engagement_rate:
        return None
    return Rejection(
        RejectionCode.ENGAGEMENT_TOO_LOW,
        f"Engagement {metrics.engagement_rate:.2%} < {filters.min_engagement_rate:.2%} minimum",
    )


def _check_classification(
    classification: Classification | None, filters: FilterSettings
) -> list[Rejection]:
    if classification is None:
        return [Rejection(RejectionCode.NOT_CLASSIFIED, "Niche classification not available")]
    rejections = []
    if classification.primary_niche not in filters.accepted_niches:
        rejections.append(
            Rejection(RejectionCode.NICHE_MISMATCH, f"Classified as {classification.primary_niche}")
        )
    if classification.relevance < filters.min_relevance:
        rejections.append(
            Rejection(
                RejectionCode.LOW_RELEVANCE,
                f"Relevance {classification.relevance:.2f} < {filters.min_relevance:.2f}",
            )
        )
    if classification.brand_safety_flags:
        flags = ", ".join(classification.brand_safety_flags)
        rejections.append(Rejection(RejectionCode.BRAND_SAFETY, f"Brand-safety flags: {flags}"))
    if classification.language not in filters.allowed_languages:
        rejections.append(
            Rejection(RejectionCode.LANGUAGE, f"Content language: {classification.language}")
        )
    return rejections


def _check_geography(creator: Creator, filters: FilterSettings) -> Rejection | None:
    if not filters.geography_allowlist:
        return None
    if creator.country is None:
        return Rejection(RejectionCode.GEOGRAPHY, "Country not available")
    if creator.country in filters.geography_allowlist:
        return None
    return Rejection(RejectionCode.GEOGRAPHY, f"Country {creator.country} not in allowlist")
