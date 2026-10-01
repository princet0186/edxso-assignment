"""HTTP API used by the n8n workflows. Run: uv run outreach api

Every endpoint except /health requires the X-API-Key header. The API holds no business rules;
each route calls the same functions the CLI uses.
"""

import secrets as secrets_module
from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import Engine
from sqlmodel import Session

from outreach.config import load_brand, load_secrets, load_settings
from outreach.db import get_engine, init_db
from outreach.jobs import JobConflictError, UnknownRunError, close_run, create_job, get_job, run_job
from outreach.models import DeliveryStatus, PipelineError
from outreach.pipeline import Stage, start_run
from outreach.reporting import build_funnel, rejection_reason_counts
from outreach.sending.queue import (
    DeliveryResult,
    InvalidTransitionError,
    claim_emails,
    record_result,
)

N8N_STAGE_NAME = "n8n"


def database() -> Engine:
    engine = get_engine()
    init_db(engine)
    return engine


def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    expected = load_secrets().api_key
    if not expected:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "API_KEY is not configured")
    # compare_digest: constant-time, so the key cannot be guessed from response timing.
    if not x_api_key or not secrets_module.compare_digest(x_api_key, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid X-API-Key")


Db = Annotated[Engine, Depends(database)]


class StartRunRequest(BaseModel):
    stages: list[Stage] = Field(default_factory=lambda: list(Stage))


class DeliveryReport(BaseModel):
    status: Literal["SENT", "SIMULATED", "FAILED"]
    provider_message_id: str | None = None
    error: str | None = None


class WorkflowError(BaseModel):
    workflow: str
    node: str = ""
    message: str


app = FastAPI(title="Micro-Influencer Outreach API", version="1.0.0")
protected = APIRouter(dependencies=[Depends(require_api_key)])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "send_mode": load_secrets().send_mode}


@protected.post("/runs", status_code=status.HTTP_201_CREATED)
def start_pipeline_run(request: StartRunRequest, engine: Db) -> dict[str, int]:
    return {"run_id": start_run(engine, request.stages)}


@protected.post("/runs/{run_id}/stages/{stage}", status_code=status.HTTP_202_ACCEPTED)
def start_stage(run_id: int, stage: Stage, background: BackgroundTasks, engine: Db) -> dict:
    try:
        job_id = create_job(engine, run_id, stage)
    except UnknownRunError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except JobConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    background.add_task(run_job, engine, job_id)
    return {"job_id": job_id, "stage": stage}


@protected.get("/jobs/{job_id}")
def job_status(job_id: int, engine: Db) -> dict:
    job = get_job(engine, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Job {job_id} does not exist")
    return {
        "job_id": job.id,
        "stage": job.stage,
        "status": job.status,
        "report": job.report,
        "error": job.error,
    }


@protected.post("/runs/{run_id}/finish")
def finish_pipeline_run(run_id: int, engine: Db) -> dict:
    try:
        return {"run_id": run_id, "status": close_run(engine, run_id)}
    except UnknownRunError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@protected.get("/summary")
def summary(engine: Db) -> dict:
    with Session(engine) as session:
        funnel = build_funnel(session, load_settings())
        return {
            "funnel": {step.label: step.count for step in funnel},
            "rejection_reasons": rejection_reason_counts(session),
        }


@protected.post("/outreach/claim")
def claim(engine: Db, limit: int | None = None) -> list[dict]:
    settings = load_settings()
    batch = limit or settings.sending.batch_size
    with Session(engine) as session:
        emails = claim_emails(session, settings, load_secrets(), load_brand(), batch)
    return [asdict(email) for email in emails]


@protected.post("/outreach/{outreach_id}/result")
def report_result(outreach_id: int, report: DeliveryReport, engine: Db) -> dict:
    result = DeliveryResult(DeliveryStatus(report.status), report.provider_message_id, report.error)
    with Session(engine) as session:
        try:
            outreach = record_result(session, outreach_id, result)
        except InvalidTransitionError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        return {"outreach_id": outreach_id, "status": outreach.status}


@protected.post("/events/workflow-error", status_code=status.HTTP_201_CREATED)
def workflow_error(error: WorkflowError, engine: Db) -> dict[str, str]:
    with Session(engine) as session:
        message = f"[{error.workflow} / {error.node}] {error.message}"
        session.add(PipelineError(stage=N8N_STAGE_NAME, message=message))
        session.commit()
    return {"recorded": "ok"}


app.include_router(protected)
