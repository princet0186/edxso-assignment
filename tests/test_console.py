"""Smoke test: every console page renders without raising, against a fresh database."""

import pytest
from streamlit.testing.v1 import AppTest

from outreach.config import load_secrets
from outreach.db import get_engine, init_db

RENDER_TIMEOUT_SECONDS = 60


@pytest.fixture
def fresh_database(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'console.db'}")
    load_secrets.cache_clear()
    get_engine.cache_clear()
    init_db(get_engine())
    yield
    load_secrets.cache_clear()
    get_engine.cache_clear()


def render_page(page_name: str) -> None:
    import sys

    from outreach.config import PROJECT_ROOT

    sys.path.insert(0, str(PROJECT_ROOT / "app"))
    import console_pages

    getattr(console_pages, page_name)()


@pytest.mark.parametrize("page", ["dashboard_page", "creators_page", "review_page"])
def test_page_renders_without_errors(fresh_database, page: str) -> None:
    app = AppTest.from_function(render_page, args=(page,), default_timeout=RENDER_TIMEOUT_SECONDS)

    app.run()

    assert not app.exception
