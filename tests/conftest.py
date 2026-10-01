from collections.abc import Iterator

import pytest
from sqlmodel import Session

from outreach.config import Secrets, load_settings
from outreach.db import create_db_engine, init_db
from outreach.models import PipelineRun
from outreach.sources.youtube import QuotaTracker
from outreach.stage import StageContext


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_db_engine("sqlite:///:memory:")
    init_db(engine)
    with Session(engine, expire_on_commit=False) as db_session:
        yield db_session


@pytest.fixture
def context(session: Session) -> StageContext:
    run = PipelineRun()
    session.add(run)
    session.commit()
    secrets = Secrets(_env_file=None, youtube_data_api_key="test-key")
    return StageContext(session, load_settings(), secrets, run.id, QuotaTracker(10_000))
