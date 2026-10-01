import smtplib
from datetime import timedelta

import pytest
from sqlmodel import Session, select

from outreach.config import Secrets, SendMode, load_brand, load_settings
from outreach.models import (
    Contact,
    Creator,
    DeliveryStatus,
    EmailSource,
    EmailStatus,
    Outreach,
    OutreachChannel,
    OutreachEvent,
    OutreachMessage,
    Platform,
    ReviewStatus,
    utc_now,
)
from outreach.sending.dispatch import send_queued_emails
from outreach.sending.dm_queue import DmNotSendableError, mark_dm_sent
from outreach.sending.queue import (
    DeliveryMethod,
    DeliveryResult,
    InvalidTransitionError,
    claim_emails,
    queue_approved_emails,
    record_result,
    release_stale_claims,
)

SETTINGS = load_settings()
BRAND = load_brand()
CAMPAIGN = SETTINGS.campaign_id


def secrets(mode: SendMode, **overrides) -> Secrets:
    return Secrets(_env_file=None, send_mode=mode, sender_name="Prince", **overrides)


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


def outreach_rows(session: Session) -> list[Outreach]:
    return list(session.exec(select(Outreach)).all())


def test_only_approved_messages_with_emails_are_queued(session: Session) -> None:
    seed_creator(session, "A", "a@asha.dev")
    seed_creator(session, "B", "b@asha.dev", review_status=ReviewStatus.GENERATED)

    assert queue_approved_emails(session, CAMPAIGN) == 1


def test_queueing_twice_creates_no_duplicates(session: Session) -> None:
    seed_creator(session, "A", "a@asha.dev")

    queue_approved_emails(session, CAMPAIGN)
    second = queue_approved_emails(session, CAMPAIGN)

    assert second == 0
    assert len(outreach_rows(session)) == 1


def test_two_creators_sharing_one_address_get_one_email(session: Session) -> None:
    seed_creator(session, "A", "team@agency.com")
    seed_creator(session, "B", "Team@Agency.com")

    assert queue_approved_emails(session, CAMPAIGN) == 1


def test_claimed_and_sent_emails_are_never_claimed_again(session: Session) -> None:
    seed_creator(session, "A", "a@asha.dev")
    dry_run = secrets(SendMode.DRY_RUN)

    (email,) = claim_emails(session, SETTINGS, dry_run, BRAND, limit=10)
    assert claim_emails(session, SETTINGS, dry_run, BRAND, limit=10) == []  # still SENDING
    record_result(session, email.outreach_id, DeliveryResult(DeliveryStatus.SIMULATED))

    assert claim_emails(session, SETTINGS, dry_run, BRAND, limit=10) == []


def test_reporting_a_result_twice_is_rejected(session: Session) -> None:
    seed_creator(session, "A", "a@asha.dev")
    (email,) = claim_emails(session, SETTINGS, secrets(SendMode.DRY_RUN), BRAND, limit=10)
    record_result(session, email.outreach_id, DeliveryResult(DeliveryStatus.SIMULATED))

    with pytest.raises(InvalidTransitionError):
        record_result(session, email.outreach_id, DeliveryResult(DeliveryStatus.SENT))


def test_dry_run_simulates_to_the_real_address(session: Session) -> None:
    seed_creator(session, "A", "a@asha.dev")

    (email,) = claim_emails(session, SETTINGS, secrets(SendMode.DRY_RUN), BRAND, limit=10)

    assert email.method == DeliveryMethod.SIMULATE
    assert email.deliver_to == "a@asha.dev"
    assert email.body.endswith(BRAND.opt_out_line)


def test_redirect_mode_delivers_to_test_inbox_and_labels_subject(session: Session) -> None:
    seed_creator(session, "A", "a@asha.dev")
    redirect = secrets(SendMode.REDIRECT, test_inbox="me@inbox.dev")

    (email,) = claim_emails(session, SETTINGS, redirect, BRAND, limit=10)

    assert email.method == DeliveryMethod.SMTP
    assert email.deliver_to == "me@inbox.dev"
    assert email.subject.startswith("[TEST → a@asha.dev]")


def test_live_mode_skips_recipients_outside_the_allowlist(session: Session) -> None:
    seed_creator(session, "A", "a@asha.dev")
    seed_creator(session, "B", "ok@asha.dev")
    live = secrets(SendMode.LIVE, live_allowlist="ok@asha.dev")

    emails = claim_emails(session, SETTINGS, live, BRAND, limit=10)

    assert [e.deliver_to for e in emails] == ["ok@asha.dev"]
    statuses = {row.recipient: row.status for row in outreach_rows(session)}
    assert statuses["a@asha.dev"] == DeliveryStatus.SKIPPED


def test_redirect_without_test_inbox_fails_safely(session: Session) -> None:
    seed_creator(session, "A", "a@asha.dev")

    emails = claim_emails(session, SETTINGS, secrets(SendMode.REDIRECT), BRAND, limit=10)

    assert emails == []
    (row,) = outreach_rows(session)
    assert row.status == DeliveryStatus.FAILED
    assert "TEST_INBOX" in row.last_error


def test_stale_claims_are_released_back_to_the_queue(session: Session) -> None:
    seed_creator(session, "A", "a@asha.dev")
    claim_emails(session, SETTINGS, secrets(SendMode.DRY_RUN), BRAND, limit=10)

    released = release_stale_claims(session, timedelta(minutes=10), utc_now() + timedelta(hours=1))

    assert released == 1
    assert outreach_rows(session)[0].status == DeliveryStatus.QUEUED


class FailingMailer:
    def send(self, email) -> str:
        raise smtplib.SMTPAuthenticationError(535, b"bad credentials")


def test_failed_send_is_logged_retried_and_capped(session: Session) -> None:
    seed_creator(session, "A", "a@asha.dev")
    redirect = secrets(SendMode.REDIRECT, test_inbox="me@inbox.dev")
    fast = SETTINGS.model_copy(
        update={"sending": SETTINGS.sending.model_copy(update={"seconds_between_sends": 0})}
    )

    for _ in range(SETTINGS.sending.max_attempts + 1):
        send_queued_emails(session, fast, redirect, BRAND, FailingMailer)

    (row,) = outreach_rows(session)
    assert row.status == DeliveryStatus.FAILED
    assert row.attempts == SETTINGS.sending.max_attempts
    events = session.exec(select(OutreachEvent).where(OutreachEvent.event == "FAILED")).all()
    assert len(events) == SETTINGS.sending.max_attempts


def test_dm_can_be_marked_sent_once_and_needs_an_instagram_handle(session: Session) -> None:
    with_handle = seed_creator(session, "A", "a@asha.dev", instagram="https://instagram.com/asha")
    without_handle = seed_creator(session, "B", "b@asha.dev")

    mark_dm_sent(session, CAMPAIGN, with_handle.id)
    with pytest.raises(DmNotSendableError, match="already"):
        mark_dm_sent(session, CAMPAIGN, with_handle.id)
    with pytest.raises(DmNotSendableError, match="Instagram handle"):
        mark_dm_sent(session, CAMPAIGN, without_handle.id)

    (row,) = outreach_rows(session)
    assert row.channel == OutreachChannel.INSTAGRAM_DM
    assert row.status == DeliveryStatus.MANUAL_SENT
