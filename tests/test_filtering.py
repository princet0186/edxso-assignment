from datetime import UTC, datetime, timedelta

import pytest

from outreach.config import load_settings
from outreach.filtering.rules import (
    CreatorFacts,
    QualificationRules,
    RejectionCode,
    evaluate,
)
from outreach.filtering.scoring import ScoreInputs, brand_fit_score, size_fit
from outreach.models import Classification, Creator, CreatorMetrics, EngagementMethod, Platform

NOW = datetime(2026, 10, 1, tzinfo=UTC)
SETTINGS = load_settings()
RULES = QualificationRules(SETTINGS.filters, activity_window_days=90, now=NOW)


def make_creator(subscribers: int | None = 20_000, country: str | None = "IN") -> Creator:
    return Creator(
        id=1,
        platform=Platform.YOUTUBE,
        platform_id="UC1",
        name="Code With Asha",
        profile_url="https://youtube.com/@asha",
        subscriber_count=subscribers,
        subscribers_hidden=subscribers is None,
        country=country,
        discovered_via_query="q",
    )


def make_metrics(engagement: float | None = 0.05, days_since_upload: int = 3) -> CreatorMetrics:
    return CreatorMetrics(
        creator_id=1,
        engagement_rate=engagement,
        engagement_method=EngagementMethod.LONG_FORM,
        sample_size=5,
        last_upload_at=NOW - timedelta(days=days_since_upload),
        uploads_in_activity_window=4,
        unavailable_reason=None if engagement is not None else "Likes hidden on 5/5 videos",
    )


def make_classification(**overrides) -> Classification:
    fields = {
        "creator_id": 1,
        "primary_niche": "EdTech",
        "tone": "calm",
        "audience_level": "beginner",
        "language": "en",
        "relevance": 0.9,
        "provider": "test",
        "model": "test",
        "prompt_version": "classify_v1",
    }
    return Classification(**(fields | overrides))


def codes(facts: CreatorFacts) -> set[RejectionCode]:
    return {rejection.code for rejection in evaluate(facts, RULES)}


def test_a_creator_meeting_every_rule_qualifies() -> None:
    facts = CreatorFacts(make_creator(), make_metrics(), make_classification())
    assert codes(facts) == set()


def test_out_of_range_creator_gets_only_the_size_reason() -> None:
    facts = CreatorFacts(make_creator(subscribers=250_000), None, None)

    (rejection,) = evaluate(facts, RULES)

    assert rejection.code == RejectionCode.SUBSCRIBERS_OUT_OF_RANGE
    assert rejection.message == "250,000 subscribers is outside 5,000–100,000"


def test_every_failing_rule_is_reported_not_just_the_first() -> None:
    facts = CreatorFacts(
        make_creator(),
        make_metrics(engagement=0.01, days_since_upload=200),
        make_classification(primary_niche="Gaming", relevance=0.2, language="other"),
    )
    assert codes(facts) == {
        RejectionCode.INACTIVE,
        RejectionCode.ENGAGEMENT_TOO_LOW,
        RejectionCode.NICHE_MISMATCH,
        RejectionCode.LOW_RELEVANCE,
        RejectionCode.LANGUAGE,
    }


def test_unavailable_engagement_reports_the_measured_reason() -> None:
    facts = CreatorFacts(make_creator(), make_metrics(engagement=None), make_classification())

    (rejection,) = evaluate(facts, RULES)

    assert rejection.code == RejectionCode.ENGAGEMENT_UNAVAILABLE
    assert rejection.message == "Likes hidden on 5/5 videos"


def test_brand_safety_flag_rejects() -> None:
    facts = CreatorFacts(
        make_creator(), make_metrics(), make_classification(brand_safety_flags=["piracy"])
    )
    assert codes(facts) == {RejectionCode.BRAND_SAFETY}


def test_geography_is_only_enforced_when_an_allowlist_is_configured() -> None:
    restricted = SETTINGS.filters.model_copy(update={"geography_allowlist": ["IN"]})
    rules = QualificationRules(restricted, 90, NOW)
    facts = CreatorFacts(make_creator(country="US"), make_metrics(), make_classification())

    assert codes(facts) == set()
    assert {r.code for r in evaluate(facts, rules)} == {RejectionCode.GEOGRAPHY}


@pytest.mark.parametrize(
    ("subscribers", "expected"),
    [(5_000, 0.5), (7_500, 0.75), (10_000, 1.0), (30_000, 1.0), (50_000, 1.0), (100_000, 0.5)],
)
def test_size_fit_peaks_in_sweet_spot_and_slopes_to_half_at_edges(subscribers, expected) -> None:
    assert size_fit(subscribers, SETTINGS.filters, SETTINGS.scoring) == pytest.approx(expected)


def test_perfect_creator_scores_100_and_email_adds_contactability() -> None:
    perfect = ScoreInputs(
        relevance=1.0,
        engagement_rate=0.10,
        subscriber_count=20_000,
        uploads_in_activity_window=8,
        has_email=True,
    )
    without_email = ScoreInputs(**(perfect.__dict__ | {"has_email": False}))

    assert brand_fit_score(perfect, SETTINGS.scoring, SETTINGS.filters).total == 100.0
    assert brand_fit_score(without_email, SETTINGS.scoring, SETTINGS.filters).total == 90.0
