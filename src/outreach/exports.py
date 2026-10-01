"""Export stage: writes the submission deliverables to outputs/.

- influencers.csv — every creator in the micro-influencer size range, with status and reasons
- discovered_channels.csv — every channel discovered, including out-of-range ones
- messages.csv — generated email pitch + Instagram DM per qualified creator
- outreach_tracker.csv — what was sent, when, how
- outreach_results.xlsx — all of the above as sheets in one workbook
- run_summary.json — funnel, rejection reasons, email sources, models used, quota
"""

import json
from collections import Counter
from pathlib import Path

import pandas as pd
from sqlmodel import Session, select

from outreach.config import PROJECT_ROOT, Settings
from outreach.models import Classification, Contact, EmailStatus, OutreachMessage, utc_now
from outreach.quota import quota_used_today
from outreach.reporting import (
    build_funnel,
    creator_rows,
    message_rows,
    rejection_reason_counts,
    tracker_rows,
)
from outreach.stage import StageContext, StageReport

OUTPUTS_DIR = PROJECT_ROOT / "outputs"
DISCOVERED_COLUMNS = [
    "Name",
    "Profile URL",
    "Followers",
    "Status",
    "Reasons",
    "Discovered Via Query",
]


def _in_size_range(row: dict, settings: Settings) -> bool:
    followers = row["Followers"]
    return isinstance(followers, int) and settings.filters.subscriber_range_contains(followers)


def _ranked(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    return frame.sort_values(["Status", "Score"], ascending=[False, False], na_position="last")


def build_summary(session: Session, settings: Settings) -> dict:
    contacts = session.exec(select(Contact)).all()
    email_sources = Counter(c.email_source for c in contacts if c.email_status == EmailStatus.FOUND)
    labels = session.exec(select(Classification)).all()
    messages = session.exec(
        select(OutreachMessage).where(OutreachMessage.campaign_id == settings.campaign_id)
    ).all()
    found = sum(email_sources.values())
    return {
        "generated_at": utc_now().isoformat(timespec="seconds"),
        "campaign_id": settings.campaign_id,
        "rules_version": settings.rules_version,
        "funnel": {step.label: step.count for step in build_funnel(session, settings)},
        "rejection_reasons": rejection_reason_counts(session),
        "email_hit_rate": round(found / len(contacts), 3) if contacts else None,
        "email_sources": dict(email_sources),
        "classification_models": dict(Counter(f"{c.provider}/{c.model}" for c in labels)),
        "message_models": dict(Counter(f"{m.provider}/{m.model}" for m in messages)),
        "messages_needing_review": sum(1 for m in messages if m.validation_issues),
        "messages_with_similarity_warning": sum(1 for m in messages if m.similarity_warning),
        "youtube_quota_used_today": quota_used_today(session),
    }


def write_deliverables(session: Session, settings: Settings, directory: Path) -> dict[str, int]:
    directory.mkdir(parents=True, exist_ok=True)
    every_creator = creator_rows(session)
    influencers = _ranked(pd.DataFrame([r for r in every_creator if _in_size_range(r, settings)]))
    discovered = pd.DataFrame(every_creator)
    discovered = discovered[DISCOVERED_COLUMNS] if not discovered.empty else discovered
    messages = pd.DataFrame(message_rows(session, settings.campaign_id))
    tracker = pd.DataFrame(tracker_rows(session, settings.campaign_id))
    summary = build_summary(session, settings)

    influencers.to_csv(directory / "influencers.csv", index=False)
    discovered.to_csv(directory / "discovered_channels.csv", index=False)
    messages.to_csv(directory / "messages.csv", index=False)
    tracker.to_csv(directory / "outreach_tracker.csv", index=False)
    with pd.ExcelWriter(directory / "outreach_results.xlsx") as workbook:
        influencers.to_excel(workbook, sheet_name="Influencers", index=False)
        messages.to_excel(workbook, sheet_name="Messages", index=False)
        tracker.to_excel(workbook, sheet_name="Outreach Tracker", index=False)
        discovered.to_excel(workbook, sheet_name="All Discovered", index=False)
    (directory / "run_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    return {
        "influencer_rows": len(influencers),
        "discovered_rows": len(discovered),
        "message_rows": len(messages),
        "tracker_rows": len(tracker),
    }


def export_deliverables(context: StageContext) -> StageReport:
    return StageReport(write_deliverables(context.session, context.settings, OUTPUTS_DIR))
