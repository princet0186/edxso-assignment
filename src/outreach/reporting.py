"""Read-only views over the database, shared by the CLI, the API and the Streamlit UI."""

from dataclasses import dataclass

from sqlmodel import Session, col, func, select

from outreach.config import Settings
from outreach.models import Creator, CreatorMetrics, PipelineError, PipelineRun


@dataclass(frozen=True)
class FunnelStep:
    label: str
    count: int


def build_funnel(session: Session, settings: Settings) -> list[FunnelStep]:
    creators = session.exec(select(Creator)).all()
    in_range = [
        c
        for c in creators
        if c.subscriber_count is not None
        and settings.filters.subscriber_range_contains(c.subscriber_count)
    ]
    measured = session.exec(select(CreatorMetrics)).all()
    return [
        FunnelStep("Discovered channels", len(creators)),
        FunnelStep("Within subscriber range", len(in_range)),
        FunnelStep("Recent videos fetched", sum(1 for c in in_range if c.videos_fetched_at)),
        FunnelStep(
            "Engagement rate computed", sum(1 for m in measured if m.engagement_rate is not None)
        ),
    ]


def recent_runs(session: Session, limit: int) -> list[PipelineRun]:
    return list(session.exec(select(PipelineRun).order_by(col(PipelineRun.id).desc()).limit(limit)))


def error_count(session: Session) -> int:
    return session.exec(select(func.count()).select_from(PipelineError)).one()
