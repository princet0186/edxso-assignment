"""The email send queue. Duplicate prevention lives here, in the database — never only in a
workflow — so the CLI sender and n8n get the same guarantees.

Lifecycle: QUEUED -> SENDING (claimed) -> SENT | SIMULATED | FAILED (retried) | SKIPPED
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Session, col, select

from outreach.config import BrandProfile, MissingConfigError, Secrets, SendMode, Settings, require
from outreach.models import (
    Contact,
    DeliveryStatus,
    EmailStatus,
    Outreach,
    OutreachChannel,
    OutreachEvent,
    OutreachMessage,
    ReviewStatus,
    utc_now,
)
from outreach.sending.compose import compose_body, redirect_subject

FINAL_STATUSES = {DeliveryStatus.SENT, DeliveryStatus.SIMULATED}


class DeliveryMethod(StrEnum):
    SMTP = "smtp"
    SIMULATE = "simulate"


class InvalidTransitionError(ValueError):
    """A result was reported for an outreach row that is not currently being sent."""


@dataclass(frozen=True)
class Routing:
    deliver_to: str
    subject_prefix_for: str | None
    method: DeliveryMethod


@dataclass(frozen=True)
class EmailToSend:
    outreach_id: int
    deliver_to: str
    intended_recipient: str
    subject: str
    body: str
    mode: SendMode
    method: DeliveryMethod
    idempotency_key: str


@dataclass(frozen=True)
class DeliveryResult:
    status: DeliveryStatus
    provider_message_id: str | None = None
    error: str | None = None


def route(intended_recipient: str, secrets: Secrets) -> Routing | None:
    """Where an email really goes in the current SEND_MODE. None means: do not send."""
    if secrets.send_mode == SendMode.DRY_RUN:
        return Routing(intended_recipient, None, DeliveryMethod.SIMULATE)
    if secrets.send_mode == SendMode.REDIRECT:
        test_inbox = require(secrets.test_inbox, "TEST_INBOX")
        return Routing(test_inbox, intended_recipient, DeliveryMethod.SMTP)
    if intended_recipient.lower() in secrets.live_allowlist_addresses:
        return Routing(intended_recipient, None, DeliveryMethod.SMTP)
    return None


def queue_approved_emails(session: Session, campaign_id: str) -> int:
    """Adds every approved, contactable message to the queue. Already-queued creators and
    already-used addresses are ignored by the UNIQUE constraints (ON CONFLICT DO NOTHING)."""
    approved = session.exec(
        select(OutreachMessage, Contact)
        .join(Contact, col(Contact.creator_id) == col(OutreachMessage.creator_id))
        .where(
            OutreachMessage.campaign_id == campaign_id,
            OutreachMessage.review_status == ReviewStatus.APPROVED,
            Contact.email_status == EmailStatus.FOUND,
        )
    ).all()
    queued = 0
    for message, contact in approved:
        statement = (
            sqlite_insert(Outreach)
            .values(
                creator_id=message.creator_id,
                campaign_id=campaign_id,
                channel=OutreachChannel.EMAIL,
                recipient=contact.email.lower(),
                status=DeliveryStatus.QUEUED,
                attempts=0,
                queued_at=utc_now(),
            )
            .on_conflict_do_nothing()
        )
        queued += session.exec(statement).rowcount
    session.commit()
    return queued


def release_stale_claims(session: Session, older_than: timedelta, now: datetime) -> int:
    """A sender that crashed mid-batch leaves rows in SENDING; hand them back to the queue."""
    stale = session.exec(
        select(Outreach).where(
            Outreach.status == DeliveryStatus.SENDING, col(Outreach.claimed_at) < now - older_than
        )
    ).all()
    for outreach in stale:
        outreach.status = DeliveryStatus.QUEUED
        _log(session, outreach, "CLAIM_RELEASED", {"reason": "stale claim"})
    session.commit()
    return len(stale)


def claim_emails(
    session: Session, settings: Settings, secrets: Secrets, brand: BrandProfile, limit: int
) -> list[EmailToSend]:
    queue_approved_emails(session, settings.campaign_id)
    now = utc_now()
    release_stale_claims(session, timedelta(minutes=settings.sending.stale_claim_minutes), now)
    claimed_ids = _atomically_claim(session, settings, limit, now)
    emails = []
    for outreach_id in claimed_ids:
        email = _prepare(session, session.get(Outreach, outreach_id), secrets, brand)
        if email is not None:
            emails.append(email)
    session.commit()
    return emails


def _atomically_claim(session: Session, settings: Settings, limit: int, now: datetime) -> list[int]:
    # One UPDATE ... WHERE id IN (SELECT ... LIMIT n) RETURNING id: a row can only move to
    # SENDING once, so two concurrent senders can never claim the same email.
    claimable = (
        select(Outreach.id)
        .where(
            Outreach.campaign_id == settings.campaign_id,
            Outreach.channel == OutreachChannel.EMAIL,
            (Outreach.status == DeliveryStatus.QUEUED)
            | (
                (Outreach.status == DeliveryStatus.FAILED)
                & (col(Outreach.attempts) < settings.sending.max_attempts)
            ),
        )
        .order_by(col(Outreach.id))
        .limit(limit)
    )
    statement = (
        update(Outreach)
        .where(
            col(Outreach.id).in_(claimable),
            col(Outreach.status).in_([DeliveryStatus.QUEUED, DeliveryStatus.FAILED]),
        )
        .values(
            status=DeliveryStatus.SENDING,
            claimed_at=now,
            attempts=col(Outreach.attempts) + 1,
        )
        .returning(col(Outreach.id))
    )
    ids = list(session.execute(statement).scalars())
    session.commit()
    return sorted(ids)


def _prepare(
    session: Session, outreach: Outreach, secrets: Secrets, brand: BrandProfile
) -> EmailToSend | None:
    try:
        routing = route(outreach.recipient, secrets)
    except MissingConfigError as exc:
        _fail(session, outreach, str(exc))
        return None
    if routing is None:
        outreach.status = DeliveryStatus.SKIPPED
        outreach.mode = secrets.send_mode
        _log(session, outreach, "SKIPPED", {"reason": "recipient not in LIVE_ALLOWLIST"})
        return None

    message = session.exec(
        select(OutreachMessage).where(
            OutreachMessage.creator_id == outreach.creator_id,
            OutreachMessage.campaign_id == outreach.campaign_id,
        )
    ).one()
    subject = message.email_subject
    if routing.subject_prefix_for:
        subject = redirect_subject(subject, routing.subject_prefix_for)
    outreach.mode = secrets.send_mode
    _log(
        session, outreach, "CLAIMED", {"deliver_to": routing.deliver_to, "mode": secrets.send_mode}
    )
    return EmailToSend(
        outreach_id=outreach.id,
        deliver_to=routing.deliver_to,
        intended_recipient=outreach.recipient,
        subject=subject,
        body=compose_body(message.email_body, brand, secrets.sender_name),
        mode=secrets.send_mode,
        method=routing.method,
        idempotency_key=f"{outreach.campaign_id}:{outreach.channel}:{outreach.creator_id}",
    )


def record_result(session: Session, outreach_id: int, result: DeliveryResult) -> Outreach:
    outreach = session.get(Outreach, outreach_id)
    if outreach is None:
        raise InvalidTransitionError(f"Outreach {outreach_id} does not exist")
    if outreach.status != DeliveryStatus.SENDING:
        raise InvalidTransitionError(
            f"Outreach {outreach_id} is {outreach.status}, not SENDING; result ignored"
        )
    if result.status not in {*FINAL_STATUSES, DeliveryStatus.FAILED}:
        raise InvalidTransitionError(f"{result.status} is not a delivery outcome")
    if result.status == DeliveryStatus.FAILED:
        _fail(session, outreach, result.error or "unknown error")
    else:
        outreach.status = result.status
        outreach.sent_at = utc_now()
        outreach.provider_message_id = result.provider_message_id
        outreach.last_error = None
        _log(session, outreach, result.status, {"message_id": result.provider_message_id or ""})
    session.commit()
    return outreach


def _fail(session: Session, outreach: Outreach, error: str) -> None:
    outreach.status = DeliveryStatus.FAILED
    outreach.last_error = error
    _log(session, outreach, "FAILED", {"error": error})


def _log(session: Session, outreach: Outreach, event: str, detail: dict[str, str]) -> None:
    session.add(outreach)
    session.add(OutreachEvent(outreach_id=outreach.id, event=str(event), detail=detail))
