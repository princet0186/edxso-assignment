"""Shared test data builders."""

from sqlmodel import Session

from outreach.config import load_settings
from outreach.models import (
    Contact,
    Creator,
    EmailSource,
    EmailStatus,
    OutreachMessage,
    Platform,
    ReviewStatus,
)

CAMPAIGN = load_settings().campaign_id


def seed_creator(
    session: Session,
    channel_id: str,
    email: str,
    review_status: ReviewStatus = ReviewStatus.APPROVED,
    instagram: str | None = None,
) -> Creator:
    creator = Creator(
        platform=Platform.YOUTUBE,
        platform_id=channel_id,
        name=f"Creator {channel_id}",
        profile_url="https://youtube.com/x",
        discovered_via_query="q",
    )
    session.add(creator)
    session.commit()
    session.add(
        Contact(
            creator_id=creator.id,
            email=email,
            email_status=EmailStatus.FOUND,
            email_source=EmailSource.CHANNEL_DESCRIPTION,
            instagram=instagram,
        )
    )
    session.add(
        OutreachMessage(
            creator_id=creator.id,
            campaign_id=CAMPAIGN,
            angle="AFFILIATE",
            angle_reason="r",
            email_subject="Your binary search video",
            email_body="Validated body.",
            email_word_count=70,
            instagram_dm="Hey!",
            dm_word_count=20,
            attempts=1,
            provider="t",
            model="t",
            prompt_version="outreach_v1",
            review_status=review_status,
        )
    )
    session.commit()
    return creator
