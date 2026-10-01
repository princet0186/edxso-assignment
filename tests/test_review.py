from datetime import UTC, datetime

import pytest
from sqlmodel import Session

from outreach.config import load_settings
from outreach.models import (
    Classification,
    Creator,
    CreatorMetrics,
    EngagementMethod,
    OutreachMessage,
    Platform,
    ReviewStatus,
    Video,
)
from outreach.personalize.review import (
    MessageEdit,
    ReviewError,
    approve,
    approve_all_valid,
    save_edit,
)

SETTINGS = load_settings()
VIDEO_TITLE = "Binary Search Explained in 20 Minutes"
VALID_BODY = (
    "Hi Code With Asha, your Binary Search Explained in 20 Minutes video made a topic most "
    "beginners dread feel calm and logical. Your audience of new programmers is exactly who "
    "SkillSprint helps, with guided learning paths and mock interviews that give instant "
    "feedback. We would love to offer an affiliate partnership with a unique discount code "
    "for your viewers and commission on every sign-up. Would you be open to a quick reply or "
    "a short call next week to talk it through?"
)
VALID_DM = (
    "Hey Code With Asha, loved your binary search breakdown! Would you be up for an "
    "affiliate partnership with a discount code for your viewers?"
)


def seed_message(session: Session, status: ReviewStatus, issues: list[str]) -> OutreachMessage:
    creator = Creator(
        platform=Platform.YOUTUBE,
        platform_id="UC1",
        name="Code With Asha",
        profile_url="https://youtube.com/@asha",
        subscriber_count=20_000,
        discovered_via_query="q",
    )
    session.add(creator)
    session.commit()
    session.add_all(
        [
            CreatorMetrics(
                creator_id=creator.id,
                engagement_rate=0.04,
                engagement_method=EngagementMethod.LONG_FORM,
                sample_size=5,
                uploads_in_activity_window=4,
            ),
            Classification(
                creator_id=creator.id,
                primary_niche="EdTech",
                content_themes=["beginner tutorials"],
                tone="calm",
                audience_level="beginner",
                language="en",
                relevance=0.9,
                provider="t",
                model="t",
                prompt_version="classify_v1",
            ),
            Video(
                video_id="v1",
                creator_id=creator.id,
                title=VIDEO_TITLE,
                published_at=datetime(2026, 9, 20, tzinfo=UTC),
                duration_seconds=1200,
            ),
        ]
    )
    message = OutreachMessage(
        creator_id=creator.id,
        campaign_id=SETTINGS.campaign_id,
        angle="AFFILIATE",
        angle_reason="beginners",
        email_subject="Your binary search video",
        email_body="Too short.",
        email_word_count=2,
        instagram_dm=VALID_DM,
        dm_word_count=20,
        referenced_video_title=VIDEO_TITLE,
        signals_used=["recent_video", "audience"],
        validation_issues=issues,
        attempts=3,
        provider="t",
        model="t",
        prompt_version="outreach_v1",
        review_status=status,
    )
    session.add(message)
    session.commit()
    return message


def test_message_with_open_issues_cannot_be_approved(session: Session) -> None:
    message = seed_message(session, ReviewStatus.NEEDS_REVIEW, ["Email body has 2 words"])

    with pytest.raises(ReviewError):
        approve(session, message.id)


def test_reviewer_edit_is_revalidated_and_then_approvable(session: Session) -> None:
    message = seed_message(session, ReviewStatus.NEEDS_REVIEW, ["Email body has 2 words"])

    issues = save_edit(
        session, message.id, MessageEdit("Your binary search video", VALID_BODY, VALID_DM), SETTINGS
    )
    approve(session, message.id)

    assert issues == []
    assert message.edited_by_reviewer
    assert message.review_status == ReviewStatus.APPROVED


def test_bad_edit_keeps_message_out_of_the_send_queue(session: Session) -> None:
    message = seed_message(session, ReviewStatus.GENERATED, [])

    edit = MessageEdit("Quick idea", "Hi [Name], short.", VALID_DM)
    issues = save_edit(session, message.id, edit, SETTINGS)

    assert issues
    assert message.review_status == ReviewStatus.NEEDS_REVIEW


def test_empty_subject_is_refused_with_a_clear_error(session: Session) -> None:
    message = seed_message(session, ReviewStatus.GENERATED, [])

    with pytest.raises(ReviewError, match="email_subject"):
        save_edit(session, message.id, MessageEdit("", VALID_BODY, VALID_DM), SETTINGS)


def test_bulk_approval_skips_messages_needing_review(session: Session) -> None:
    message = seed_message(session, ReviewStatus.NEEDS_REVIEW, ["issue"])

    assert approve_all_valid(session, SETTINGS.campaign_id) == 0
    assert message.review_status == ReviewStatus.NEEDS_REVIEW
