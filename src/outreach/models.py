"""Database tables. SQLite is the single source of truth; exports are derived from it."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Column, DateTime, UniqueConstraint
from sqlalchemy.types import TypeDecorator
from sqlmodel import Field, SQLModel

CREATOR_FK = "creators.id"
RUN_FK = "runs.id"


def utc_now() -> datetime:
    return datetime.now(UTC)


class UtcDateTime(TypeDecorator):
    """Stores naive UTC, returns aware UTC.

    SQLite silently drops timezone info, and comparing naive with aware datetimes raises
    at runtime — so every datetime crossing the DB boundary is normalised here, once.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Refusing to store a naive datetime; use timezone-aware UTC")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)


def utc_column(*, nullable: bool) -> Column:
    return Column(UtcDateTime(), nullable=nullable)


def json_column() -> Column:
    return Column(JSON, nullable=False)


class Platform(StrEnum):
    YOUTUBE = "YOUTUBE"


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class EngagementMethod(StrEnum):
    LONG_FORM = "LONG_FORM"
    MIXED_WITH_SHORTS = "MIXED_WITH_SHORTS"
    UNAVAILABLE = "UNAVAILABLE"


class QualificationStatus(StrEnum):
    QUALIFIED = "QUALIFIED"
    REJECTED = "REJECTED"


class EmailStatus(StrEnum):
    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"


class EmailSource(StrEnum):
    CHANNEL_DESCRIPTION = "CHANNEL_DESCRIPTION"
    VIDEO_DESCRIPTION = "VIDEO_DESCRIPTION"
    WEBSITE = "WEBSITE"
    NONE = "NONE"


EMAIL_NOT_FOUND = "Not Found"
NOT_AVAILABLE = "Not Available"


class PipelineRun(SQLModel, table=True):
    __tablename__ = "runs"

    id: int | None = Field(default=None, primary_key=True)
    started_at: datetime = Field(default_factory=utc_now, sa_column=utc_column(nullable=False))
    finished_at: datetime | None = Field(default=None, sa_column=utc_column(nullable=True))
    status: RunStatus = RunStatus.RUNNING
    stages: list[str] = Field(default_factory=list, sa_column=json_column())
    youtube_quota_used: int = 0
    stage_reports: dict[str, dict[str, int]] = Field(default_factory=dict, sa_column=json_column())
    failure: str | None = None


class SearchQueryLog(SQLModel, table=True):
    """Remembers which queries already ran, so a re-run (e.g. after a quota stop) does not
    spend 100 units per query searching again for channels it already has."""

    __tablename__ = "search_query_log"

    query: str = Field(primary_key=True)
    searched_at: datetime = Field(default_factory=utc_now, sa_column=utc_column(nullable=False))
    channel_ids_found: int


class Creator(SQLModel, table=True):
    __tablename__ = "creators"
    __table_args__ = (UniqueConstraint("platform", "platform_id"),)

    id: int | None = Field(default=None, primary_key=True)
    platform: Platform
    platform_id: str = Field(index=True)
    name: str
    handle: str | None = None
    profile_url: str
    country: str | None = None
    subscriber_count: int | None = None
    subscribers_hidden: bool = False
    video_count: int | None = None
    total_view_count: int | None = None
    description: str = ""
    uploads_playlist_id: str | None = None
    topic_categories: list[str] = Field(default_factory=list, sa_column=json_column())
    discovered_via_query: str
    first_seen_run_id: int | None = Field(default=None, foreign_key=RUN_FK)
    discovered_at: datetime = Field(default_factory=utc_now, sa_column=utc_column(nullable=False))
    videos_fetched_at: datetime | None = Field(default=None, sa_column=utc_column(nullable=True))


class Video(SQLModel, table=True):
    __tablename__ = "videos"

    video_id: str = Field(primary_key=True)
    creator_id: int = Field(foreign_key=CREATOR_FK, index=True)
    title: str
    description: str = ""
    published_at: datetime = Field(sa_column=utc_column(nullable=False))
    duration_seconds: int
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    live_status: str = "none"


class CreatorMetrics(SQLModel, table=True):
    __tablename__ = "creator_metrics"

    creator_id: int = Field(foreign_key=CREATOR_FK, primary_key=True)
    engagement_rate: float | None = None
    engagement_method: EngagementMethod
    sample_size: int
    median_views: int | None = None
    views_per_subscriber: float | None = None
    last_upload_at: datetime | None = Field(default=None, sa_column=utc_column(nullable=True))
    uploads_in_activity_window: int
    unavailable_reason: str | None = None
    computed_at: datetime = Field(default_factory=utc_now, sa_column=utc_column(nullable=False))


class PipelineError(SQLModel, table=True):
    """Per-creator failures. One creator failing must never stop a run, but it must be visible."""

    __tablename__ = "pipeline_errors"

    id: int | None = Field(default=None, primary_key=True)
    run_id: int | None = Field(default=None, foreign_key=RUN_FK)
    creator_id: int | None = Field(default=None, foreign_key=CREATOR_FK)
    stage: str
    message: str
    occurred_at: datetime = Field(default_factory=utc_now, sa_column=utc_column(nullable=False))


class Classification(SQLModel, table=True):
    """LLM-derived niche and content context. Provider, model and prompt version are kept so
    every label can be traced back to exactly what produced it."""

    __tablename__ = "classifications"

    creator_id: int = Field(foreign_key=CREATOR_FK, primary_key=True)
    primary_niche: str
    sub_niches: list[str] = Field(default_factory=list, sa_column=json_column())
    content_themes: list[str] = Field(default_factory=list, sa_column=json_column())
    tone: str
    audience_level: str
    language: str
    relevance: float
    brand_safety_flags: list[str] = Field(default_factory=list, sa_column=json_column())
    evidence: list[str] = Field(default_factory=list, sa_column=json_column())
    provider: str
    model: str
    prompt_version: str
    classified_at: datetime = Field(default_factory=utc_now, sa_column=utc_column(nullable=False))


class FilterResult(SQLModel, table=True):
    __tablename__ = "filter_results"

    creator_id: int = Field(foreign_key=CREATOR_FK, primary_key=True)
    run_id: int | None = Field(default=None, foreign_key=RUN_FK)
    status: QualificationStatus
    reasons: list[dict[str, str]] = Field(default_factory=list, sa_column=json_column())
    score: float | None = None
    score_breakdown: dict[str, float] = Field(default_factory=dict, sa_column=json_column())
    rules_version: str
    evaluated_at: datetime = Field(default_factory=utc_now, sa_column=utc_column(nullable=False))


class Contact(SQLModel, table=True):
    __tablename__ = "contacts"

    creator_id: int = Field(foreign_key=CREATOR_FK, primary_key=True)
    email: str = EMAIL_NOT_FOUND
    email_status: EmailStatus = EmailStatus.NOT_FOUND
    email_source: EmailSource = EmailSource.NONE
    email_source_url: str | None = None
    instagram: str | None = None
    tiktok: str | None = None
    x: str | None = None
    linkedin: str | None = None
    website: str | None = None
    notes: list[str] = Field(default_factory=list, sa_column=json_column())
    enriched_at: datetime = Field(default_factory=utc_now, sa_column=utc_column(nullable=False))


class LlmCacheEntry(SQLModel, table=True):
    """Re-runs and resumes reuse earlier answers instead of spending free-tier quota again."""

    __tablename__ = "llm_cache"

    key: str = Field(primary_key=True)
    response_text: str
    provider: str
    model: str
    created_at: datetime = Field(default_factory=utc_now, sa_column=utc_column(nullable=False))
