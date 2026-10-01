"""Runs stages in order and records each run: per-stage counts, quota spent, final status."""

import logging
from collections.abc import Sequence
from datetime import datetime, time
from enum import StrEnum
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, func
from sqlmodel import Session, select

from outreach.classify import classify_creators
from outreach.config import Settings, load_secrets, load_settings
from outreach.discovery import discover_creators
from outreach.filtering.stage import qualify_creators
from outreach.metrics import compute_metrics
from outreach.models import PipelineRun, RunStatus, utc_now
from outreach.sources.youtube import QuotaBudgetExceeded, QuotaTracker
from outreach.stage import StageContext, StageHandler

# YouTube's daily quota resets at midnight Pacific time, so "today's spend" is counted from there.
QUOTA_RESET_TIMEZONE = ZoneInfo("America/Los_Angeles")

logger = logging.getLogger(__name__)


class Stage(StrEnum):
    """Declaration order is execution order."""

    DISCOVER = "discover"
    METRICS = "metrics"
    CLASSIFY = "classify"
    FILTER = "filter"


STAGE_HANDLERS: dict[Stage, StageHandler] = {
    Stage.DISCOVER: discover_creators,
    Stage.METRICS: compute_metrics,
    Stage.CLASSIFY: classify_creators,
    Stage.FILTER: qualify_creators,
}


def in_pipeline_order(stages: Sequence[Stage]) -> list[Stage]:
    return [stage for stage in Stage if stage in stages]


def quota_used_since_reset(session: Session, now: datetime) -> int:
    local_now = now.astimezone(QUOTA_RESET_TIMEZONE)
    reset_at = datetime.combine(local_now.date(), time.min, tzinfo=QUOTA_RESET_TIMEZONE)
    used = session.exec(
        select(func.sum(PipelineRun.youtube_quota_used)).where(PipelineRun.started_at >= reset_at)
    ).one()
    return used or 0


def _remaining_quota(session: Session, settings: Settings) -> int:
    used_today = quota_used_since_reset(session, utc_now())
    return max(settings.youtube.quota_ceiling_units - used_today, 0)


def run_pipeline(
    engine: Engine, stages: Sequence[Stage], item_limit: int | None = None
) -> PipelineRun:
    settings = load_settings()
    with Session(engine, expire_on_commit=False) as session:
        quota = QuotaTracker(_remaining_quota(session, settings))
        run = _start_run(session, stages)
        context = StageContext(session, settings, load_secrets(), run.id, quota, item_limit)
        try:
            for stage in in_pipeline_order(stages):
                _run_stage(context, run, stage)
        except QuotaBudgetExceeded as exc:
            logger.warning("Stopping early: %s", exc)
            session.rollback()
            _finish_run(session, run, quota, RunStatus.PARTIAL, failure=str(exc))
        except Exception as exc:
            session.rollback()
            _finish_run(session, run, quota, RunStatus.FAILED, failure=repr(exc))
            raise
        else:
            _finish_run(session, run, quota, RunStatus.COMPLETED)
    return run


def _start_run(session: Session, stages: Sequence[Stage]) -> PipelineRun:
    run = PipelineRun(stages=[str(stage) for stage in in_pipeline_order(stages)])
    session.add(run)
    session.commit()
    logger.info("Run %d started: %s", run.id, ", ".join(run.stages))
    return run


def _run_stage(context: StageContext, run: PipelineRun, stage: Stage) -> None:
    logger.info("Stage %s started", stage)
    report = STAGE_HANDLERS[stage](context)
    # JSON columns only persist on reassignment; in-place mutation is not change-tracked.
    run.stage_reports = {**run.stage_reports, str(stage): report.counts}
    run.youtube_quota_used = context.youtube_quota.used_units
    context.session.add(run)
    context.session.commit()
    logger.info("Stage %s finished: %s", stage, report.counts)


def _finish_run(
    session: Session,
    run: PipelineRun,
    quota: QuotaTracker,
    status: RunStatus,
    failure: str | None = None,
) -> None:
    run.status = status
    run.failure = failure
    run.youtube_quota_used = quota.used_units
    run.finished_at = utc_now()
    session.add(run)
    session.commit()
    logger.info("Run %d %s (YouTube quota used: %d units)", run.id, status, quota.used_units)
