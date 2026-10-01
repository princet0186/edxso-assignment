from datetime import UTC, datetime, timedelta

import pytest

from outreach.config import MetricsSettings
from outreach.metrics import summarize_engagement
from outreach.models import EngagementMethod, Video

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
RULES = MetricsSettings(
    min_video_age_hours=48, long_form_min_seconds=180, min_sample_videos=3, activity_window_days=90
)


def make_video(
    days_ago: float,
    views: int = 1000,
    likes: int | None = 50,
    comments: int | None = 10,
    duration: int = 600,
    live_status: str = "none",
) -> Video:
    return Video(
        video_id=f"v{days_ago}-{views}-{duration}",
        creator_id=1,
        title="t",
        published_at=NOW - timedelta(days=days_ago),
        duration_seconds=duration,
        view_count=views,
        like_count=likes,
        comment_count=comments,
        live_status=live_status,
    )


def test_engagement_is_median_of_likes_plus_comments_over_views() -> None:
    videos = [
        make_video(5, views=1000, likes=50, comments=10),  # 6%
        make_video(10, views=1000, likes=30, comments=0),  # 3%
        make_video(15, views=1000, likes=900, comments=100),  # 100% — viral outlier
    ]

    summary = summarize_engagement(videos, subscriber_count=20_000, rules=RULES, now=NOW)

    assert summary.engagement_rate == pytest.approx(0.06)
    assert summary.engagement_method == EngagementMethod.LONG_FORM
    assert summary.views_per_subscriber == pytest.approx(1000 / 20_000)


def test_videos_younger_than_48_hours_are_excluded() -> None:
    videos = [make_video(1, likes=999), make_video(5), make_video(6), make_video(7)]

    summary = summarize_engagement(videos, 10_000, RULES, NOW)

    assert summary.sample_size == 3
    assert summary.engagement_rate == pytest.approx(0.06)


def test_falls_back_to_shorts_when_too_few_long_form_videos() -> None:
    videos = [make_video(5), make_video(6, duration=45), make_video(7, duration=30)]

    summary = summarize_engagement(videos, 10_000, RULES, NOW)

    assert summary.engagement_method == EngagementMethod.MIXED_WITH_SHORTS
    assert summary.engagement_rate is not None


def test_hidden_likes_make_engagement_unavailable_not_estimated() -> None:
    videos = [make_video(5, likes=None), make_video(6, likes=None), make_video(7), make_video(8)]

    summary = summarize_engagement(videos, 10_000, RULES, NOW)

    assert summary.engagement_rate is None
    assert summary.engagement_method == EngagementMethod.UNAVAILABLE
    assert summary.unavailable_reason == "Likes hidden on 2/4 sampled videos"


def test_too_few_videos_is_reported_with_reason() -> None:
    summary = summarize_engagement([make_video(5)], 10_000, RULES, NOW)

    assert summary.engagement_rate is None
    assert summary.unavailable_reason == "Only 1 eligible videos (need 3)"


def test_live_and_upcoming_streams_are_ignored_for_activity() -> None:
    videos = [make_video(1, live_status="upcoming"), make_video(100), make_video(120)]

    summary = summarize_engagement(videos, 10_000, RULES, NOW)

    assert summary.last_upload_at == NOW - timedelta(days=100)
    assert summary.uploads_in_activity_window == 0
