"""Runs stages in order and records each run: per-stage counts, quota spent, final status."""

import logging
from collections.abc import Sequence
from enum import StrEnum

from sqlalchemy import Engine
from sqlmodel import Session

from outreach.classify import classify_creators
from outreach.config import Settings, load_secrets, load_settings
from outreach.discovery import discover_creators
from outreach.enrichment.stage import enrich_contacts
from outreach.exports import export_deliverables
from outreach.filtering.stage import qualify_creators
from outreach.metrics import compute_metrics
from outreach.models import PipelineRun, RunStatus, utc_now
from outreach.personalize.generator import generate_messages
from outreach.quota import quota_used_today
from outreach.sources.youtube import QuotaBudgetExceeded, QuotaTracker
from outreach.stage import StageContext, StageHandler, StageReport

logger = logging.getLogger(__name__)


class Stage(StrEnum):
    """Declaration order is execution order."""

    DISCOVER = "discover"
    METRICS = "metrics"
    CLASSIFY = "classify"
    FILTER = "filter"
    ENRICH = "enrich"
    GENERATE = "generate"
    EXPORT = "export"


STAGE_HANDLERS: dict[Stage, StageHandler] = {
    Stage.DISCOVER: discover_creators,
    Stage.METRICS: compute_metrics,
    Stage.CLASSIFY: classify_creators,
    Stage.FILTER: qualify_creators,
    Stage.ENRICH: enrich_contacts,
    Stage.GENERATE: generate_messages,
    Stage.EXPORT: export_deliverables,
}


def in_pipeline_order(stages: Sequence[Stage]) -> list[Stage]:
    return [stage for stage in Stage if stage in stages]


def _remaining_quota(session: Session, settings: Settings) -> int:
    used_today = quota_used_today(session)
    return max(settings.youtube.quota_ceiling_units - used_today, 0)


def run_pipeline(
    engine: Engine, stages: Sequence[Stage], item_limit: int | None = None
) -> PipelineRun:
    """CLI entry point: runs the requested stages in order inside one recorded run."""
    run_id = start_run(engine, stages)
    try:
        for stage in in_pipeline_order(stages):
            execute_stage(engine, run_id, stage, item_limit)
    except QuotaBudgetExceeded as exc:
        logger.warning("Stopping early: %s", exc)
        return finish_run(engine, run_id, RunStatus.PARTIAL, failure=str(exc))
    except BaseException as exc:
        # BaseException so Ctrl+C is recorded too; the run must never be left RUNNING.
        finish_run(engine, run_id, RunStatus.FAILED, failure=repr(exc))
        raise
    return finish_run(engine, run_id, RunStatus.COMPLETED)


def start_run(engine: Engine, stages: Sequence[Stage]) -> int:
    with Session(engine) as session:
        run = PipelineRun(stages=[str(stage) for stage in in_pipeline_order(stages)])
        session.add(run)
        session.commit()
        logger.info("Run %d started: %s", run.id, ", ".join(run.stages))
        return run.id


def execute_stage(
    engine: Engine, run_id: int, stage: Stage, item_limit: int | None = None
) -> StageReport:
    """Runs one stage of an existing run in its own session. Used by the CLI loop above and
    by API jobs (n8n starts each stage separately and polls for completion)."""
    settings = load_settings()
    with Session(engine, expire_on_commit=False) as session:
        quota = QuotaTracker(_remaining_quota(session, settings))
        context = StageContext(session, settings, load_secrets(), run_id, quota, item_limit)
        logger.info("Stage %s started", stage)
        try:
            report = STAGE_HANDLERS[stage](context)
        except BaseException:
            session.rollback()
            _add_quota_spent(session, run_id, quota)
            raise
        run = session.get(PipelineRun, run_id)
        # JSON columns only persist on reassignment; in-place mutation is not change-tracked.
        run.stage_reports = {**run.stage_reports, str(stage): report.counts}
        _add_quota_spent(session, run_id, quota)
        logger.info("Stage %s finished: %s", stage, report.counts)
        return report


def _add_quota_spent(session: Session, run_id: int, quota: QuotaTracker) -> None:
    run = session.get(PipelineRun, run_id)
    run.youtube_quota_used += quota.used_units
    session.add(run)
    session.commit()


def finish_run(
    engine: Engine, run_id: int, status: RunStatus, failure: str | None = None
) -> PipelineRun:
    with Session(engine, expire_on_commit=False) as session:
        run = session.get(PipelineRun, run_id)
        run.status = status
        run.failure = failure
        run.finished_at = utc_now()
        session.add(run)
        session.commit()
        logger.info(
            "Run %d %s (YouTube quota used: %d units)", run_id, status, run.youtube_quota_used
        )
        return run
