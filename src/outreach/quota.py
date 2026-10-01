"""YouTube quota accounting across runs.

Google resets the daily quota at midnight Pacific time, so "today's spend" is the sum of every
run started since then — that is what stops a second run from overshooting the daily limit.
"""

from datetime import datetime, time
from zoneinfo import ZoneInfo

from sqlmodel import Session, func, select

from outreach.models import PipelineRun, utc_now

QUOTA_RESET_TIMEZONE = ZoneInfo("America/Los_Angeles")


def quota_used_since_reset(session: Session, now: datetime) -> int:
    local_now = now.astimezone(QUOTA_RESET_TIMEZONE)
    reset_at = datetime.combine(local_now.date(), time.min, tzinfo=QUOTA_RESET_TIMEZONE)
    used = session.exec(
        select(func.sum(PipelineRun.youtube_quota_used)).where(PipelineRun.started_at >= reset_at)
    ).one()
    return used or 0


def quota_used_today(session: Session) -> int:
    return quota_used_since_reset(session, utc_now())
