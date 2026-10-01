"""Metrics stage: engagement rate and activity, computed only from real API numbers.

Engagement rate = median over sampled videos of (likes + comments) / views.
The median is used so one viral video cannot make an average creator look exceptional.
If too few videos have visible likes, the rate is reported as unavailable — never estimated.
"""

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from statistics import median

from sqlmodel import col, select

from outreach.config import MetricsSettings
from outreach.models import Creator, CreatorMetrics, EngagementMethod, Video, utc_now
from outreach.stage import StageContext, StageReport

PUBLISHED_LIVE_STATUS = "none"  # excludes live-now and upcoming broadcasts


@dataclass(frozen=True)
class EngagementSummary:
    engagement_rate: float | None
    engagement_method: EngagementMethod
    sample_size: int
    median_views: int | None
    views_per_subscriber: float | None
    last_upload_at: datetime | None
    uploads_in_activity_window: int
    unavailable_reason: str | None


def summarize_engagement(
    videos: Sequence[Video], subscriber_count: int | None, rules: MetricsSettings, now: datetime
) -> EngagementSummary:
    published = [v for v in videos if v.live_status == PUBLISHED_LIVE_STATUS]
    last_upload_at = max((v.published_at for v in published), default=None)
    window_start = now - timedelta(days=rules.activity_window_days)
    uploads_in_window = sum(1 for v in published if v.published_at >= window_start)

    sample, method = _choose_sample(published, rules, now)
    with_visible_likes = [v for v in sample if v.like_count is not None]
    unavailable_reason = _why_engagement_unavailable(sample, with_visible_likes, rules)
    if unavailable_reason:
        return EngagementSummary(
            engagement_rate=None,
            engagement_method=EngagementMethod.UNAVAILABLE,
            sample_size=len(sample),
            median_views=None,
            views_per_subscriber=None,
            last_upload_at=last_upload_at,
            uploads_in_activity_window=uploads_in_window,
            unavailable_reason=unavailable_reason,
        )

    median_views = int(median(v.view_count or 0 for v in sample))
    return EngagementSummary(
        engagement_rate=median(_engagement_of(v) for v in with_visible_likes),
        engagement_method=method,
        sample_size=len(with_visible_likes),
        median_views=median_views,
        views_per_subscriber=median_views / subscriber_count if subscriber_count else None,
        last_upload_at=last_upload_at,
        uploads_in_activity_window=uploads_in_window,
        unavailable_reason=None,
    )


def _why_engagement_unavailable(
    sample: Sequence[Video], with_visible_likes: Sequence[Video], rules: MetricsSettings
) -> str | None:
    if len(sample) < rules.min_sample_videos:
        return f"Only {len(sample)} eligible videos (need {rules.min_sample_videos})"
    if len(with_visible_likes) < rules.min_sample_videos:
        hidden = len(sample) - len(with_visible_likes)
        return f"Likes hidden on {hidden}/{len(sample)} sampled videos"
    return None


def _choose_sample(
    published: Sequence[Video], rules: MetricsSettings, now: datetime
) -> tuple[list[Video], EngagementMethod]:
    """Long-form videos give a fairer rate (Shorts inflate like-ratios); fall back to all
    settled videos only when a creator has too few long-form uploads."""
    settled_before = now - timedelta(hours=rules.min_video_age_hours)
    settled = [v for v in published if v.published_at <= settled_before and v.view_count]
    long_form = [v for v in settled if v.duration_seconds > rules.long_form_min_seconds]
    if len(long_form) >= rules.min_sample_videos:
        return long_form, EngagementMethod.LONG_FORM
    return settled, EngagementMethod.MIXED_WITH_SHORTS


def _engagement_of(video: Video) -> float:
    # Comments can be disabled; that genuinely means zero comment engagement.
    return ((video.like_count or 0) + (video.comment_count or 0)) / (video.view_count or 1)


def compute_metrics(context: StageContext) -> StageReport:
    session = context.session
    creators_with_metrics = select(CreatorMetrics.creator_id)
    pending = session.exec(
        select(Creator).where(
            col(Creator.videos_fetched_at).is_not(None),
            col(Creator.id).not_in(creators_with_metrics),
        )
    ).all()
    to_measure = context.limited(list(pending))

    engagement_available = 0
    now = utc_now()
    for creator in to_measure:
        videos = session.exec(select(Video).where(Video.creator_id == creator.id)).all()
        summary = summarize_engagement(
            videos, creator.subscriber_count, context.settings.metrics, now
        )
        session.add(CreatorMetrics(creator_id=creator.id, **asdict(summary)))
        if summary.engagement_rate is not None:
            engagement_available += 1
    session.commit()
    return StageReport(
        {"creators_measured": len(to_measure), "engagement_available": engagement_available}
    )
