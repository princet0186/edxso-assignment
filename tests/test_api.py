import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from outreach.api.main import app, database
from outreach.config import load_secrets
from outreach.db import create_db_engine, init_db
from outreach.models import Job, JobStatus, PipelineRun
from tests.factories import seed_creator

API_KEY = "test-api-key"
AUTH = {"X-API-Key": API_KEY}


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("API_KEY", API_KEY)
    monkeypatch.setenv("SEND_MODE", "DRY_RUN")
    load_secrets.cache_clear()
    test_engine = create_db_engine(f"sqlite:///{tmp_path / 'api.db'}")
    init_db(test_engine)
    app.dependency_overrides[database] = lambda: test_engine
    yield test_engine
    app.dependency_overrides.clear()
    load_secrets.cache_clear()


@pytest.fixture
def client(engine) -> TestClient:
    return TestClient(app)


def test_health_needs_no_key(client: TestClient) -> None:
    response = client.get("/health")
    assert response.json() == {"status": "ok", "send_mode": "DRY_RUN"}


def test_protected_routes_reject_missing_or_wrong_key(client: TestClient) -> None:
    assert client.get("/summary").status_code == 401
    assert client.get("/summary", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/summary", headers=AUTH).status_code == 200


def test_api_refuses_to_run_without_a_configured_key(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("API_KEY", "")
    load_secrets.cache_clear()
    assert client.get("/summary", headers=AUTH).status_code == 503


def test_stage_job_runs_and_run_can_be_closed(client: TestClient) -> None:
    run_id = client.post("/runs", json={"stages": ["filter"]}, headers=AUTH).json()["run_id"]

    started = client.post(f"/runs/{run_id}/stages/filter", headers=AUTH)
    job = client.get(f"/jobs/{started.json()['job_id']}", headers=AUTH).json()
    finished = client.post(f"/runs/{run_id}/finish", headers=AUTH).json()

    assert started.status_code == 202
    assert job["status"] == "SUCCEEDED"
    assert job["report"] == {"evaluated": 0, "qualified": 0, "rejected": 0}
    assert finished["status"] == "COMPLETED"


def test_second_stage_is_refused_while_one_is_active(client: TestClient, engine) -> None:
    with Session(engine) as session:
        run = PipelineRun()
        session.add(run)
        session.commit()
        session.add(Job(run_id=run.id, stage="classify", status=JobStatus.RUNNING))
        session.commit()
        run_id = run.id

    response = client.post(f"/runs/{run_id}/stages/filter", headers=AUTH)

    assert response.status_code == 409


def test_unknown_run_is_404(client: TestClient) -> None:
    assert client.post("/runs/999/stages/filter", headers=AUTH).status_code == 404


def test_claim_then_report_result_once(client: TestClient, engine) -> None:
    with Session(engine) as session:
        seed_creator(session, "A", "a@asha.dev")

    (email,) = client.post("/outreach/claim", headers=AUTH).json()
    first = client.post(
        f"/outreach/{email['outreach_id']}/result", json={"status": "SIMULATED"}, headers=AUTH
    )
    second = client.post(
        f"/outreach/{email['outreach_id']}/result", json={"status": "SENT"}, headers=AUTH
    )

    assert email["method"] == "simulate"
    assert first.json()["status"] == "SIMULATED"
    assert second.status_code == 409
    assert client.post("/outreach/claim", headers=AUTH).json() == []


def test_workflow_errors_are_recorded(client: TestClient) -> None:
    response = client.post(
        "/events/workflow-error",
        json={"workflow": "outreach_send", "node": "Send Email", "message": "SMTP auth failed"},
        headers=AUTH,
    )
    assert response.status_code == 201
