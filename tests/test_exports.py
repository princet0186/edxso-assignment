import json

import pandas as pd
from sqlmodel import Session

from outreach.config import load_settings
from outreach.exports import write_deliverables
from outreach.models import FilterResult, QualificationStatus
from tests.factories import seed_creator

SETTINGS = load_settings()


def test_deliverables_are_written_with_honest_placeholders(session: Session, tmp_path) -> None:
    creator = seed_creator(session, "A", "a@asha.dev")
    creator.subscriber_count = 20_000
    session.add(creator)
    session.add(
        FilterResult(
            creator_id=creator.id,
            status=QualificationStatus.QUALIFIED,
            score=80.0,
            rules_version="1.0",
        )
    )
    session.commit()

    counts = write_deliverables(session, SETTINGS, tmp_path)

    influencers = pd.read_csv(tmp_path / "influencers.csv")
    assert counts["influencer_rows"] == 1
    assert influencers.loc[0, "Email"] == "a@asha.dev"
    assert influencers.loc[0, "Audience Age"] == "Not Available"
    tracker = pd.read_csv(tmp_path / "outreach_tracker.csv")
    assert tracker.loc[0, "Status"] == "Approved, not yet queued"
    summary = json.loads((tmp_path / "run_summary.json").read_text())
    assert summary["funnel"]["Qualified"] == 1
    assert (tmp_path / "outreach_results.xlsx").exists()
