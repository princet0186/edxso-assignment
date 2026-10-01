"""Background stage jobs for the API. n8n starts a stage, gets a job id back immediately, and
polls until the job finishes — long stages never hit an HTTP timeout."""

import logging

from sqlalchemy import Engine
from sqlmodel import Session, col, select

from outreach.models import Job, JobStatus, PipelineRun, RunStatus, utc_now
from outreach.pipeline import Stage, execute_stage, finish_run
from outreach.sources.youtube import QuotaBudgetExceeded

ACTIVE_STATUSES = (JobStatus.QUEUED, JobStatus.RUNNING)

logger = logging.getLogger(__name__)


class JobConflictError(RuntimeError):
    """Another stage is still running; stages share one database writer and one quota."""


class UnknownRunError(LookupError):
    """The referenced pipeline run does not exist."""


def create_job(engine: Engine, run_id: int, stage: Stage) -> int:
    with Session(engine) as session:
        if session.get(PipelineRun, run_id) is None:
            raise UnknownRunError(f"Run {run_id} does not exist")
        active = session.exec(select(Job).where(col(Job.status).in_(ACTIVE_STATUSES))).first()
        if active is not None:
            raise JobConflictError(f"Job {active.id} ({active.stage}) is still {active.status}")
        job = Job(run_id=run_id, stage=stage)
        session.add(job)
        session.commit()
        return job.id


def run_job(engine: Engine, job_id: int) -> None:
    """Executes the job's stage and records the outcome on the job row. Runs in a background
    thread, so failures are recorded and logged here rather than raised to a caller."""
    with Session(engine) as session:
        job = session.get(Job, job_id)
        job.status = JobStatus.RUNNING
        session.add(job)
        session.commit()
        run_id, stage = job.run_id, Stage(job.stage)

    status, report, error = JobStatus.SUCCEEDED, {}, None
    try:
        report = execute_stage(engine, run_id, stage).counts
    except QuotaBudgetExceeded as exc:
        status, error = JobStatus.FAILED, f"Quota stop: {exc}"
    except Exception as exc:
        logger.exception("Job %d (%s) failed", job_id, stage)
        status, error = JobStatus.FAILED, repr(exc)

    with Session(engine) as session:
        job = session.get(Job, job_id)
        job.status, job.report, job.error, job.finished_at = status, report, error, utc_now()
        session.add(job)
        session.commit()


def get_job(engine: Engine, job_id: int) -> Job | None:
    with Session(engine, expire_on_commit=False) as session:
        return session.get(Job, job_id)


def close_run(engine: Engine, run_id: int) -> RunStatus:
    """Final run status from its jobs: all succeeded -> COMPLETED, some -> PARTIAL."""
    with Session(engine) as session:
        if session.get(PipelineRun, run_id) is None:
            raise UnknownRunError(f"Run {run_id} does not exist")
        statuses = [job.status for job in session.exec(select(Job).where(Job.run_id == run_id))]
    if statuses and all(status == JobStatus.SUCCEEDED for status in statuses):
        final = RunStatus.COMPLETED
    elif JobStatus.SUCCEEDED in statuses:
        final = RunStatus.PARTIAL
    else:
        final = RunStatus.FAILED
    finish_run(engine, run_id, final)
    return final
