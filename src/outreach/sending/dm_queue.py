"""Instagram DMs are sent by hand. Instagram's APIs do not allow cold DMs to arbitrary users,
and the brief forbids bypassing platform restrictions — so the system prepares each DM, links
the profile, and records when a person has actually sent it."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from outreach.models import (
    Contact,
    Creator,
    DeliveryStatus,
    Outreach,
    OutreachChannel,
    OutreachEvent,
    OutreachMessage,
    ReviewStatus,
    utc_now,
)

MANUAL_MODE = "MANUAL"


class DmNotSendableError(ValueError):
    """The DM cannot be marked as sent (not approved, no Instagram handle, or already sent)."""


@dataclass(frozen=True)
class DmQueueItem:
    creator_id: int
    name: str
    instagram_url: str | None
    dm: str
    sent_at: datetime | None


def dm_queue(session: Session, campaign_id: str) -> list[DmQueueItem]:
    rows = session.exec(
        select(OutreachMessage, Creator, Contact)
        .join(Creator, col(Creator.id) == col(OutreachMessage.creator_id))
        .join(Contact, col(Contact.creator_id) == col(OutreachMessage.creator_id), isouter=True)
        .where(
            OutreachMessage.campaign_id == campaign_id,
            OutreachMessage.review_status == ReviewStatus.APPROVED,
        )
    ).all()
    sent_at = {
        outreach.creator_id: outreach.sent_at
        for outreach in session.exec(
            select(Outreach).where(
                Outreach.campaign_id == campaign_id,
                Outreach.channel == OutreachChannel.INSTAGRAM_DM,
            )
        ).all()
    }
    return [
        DmQueueItem(
            creator_id=creator.id,
            name=creator.name,
            instagram_url=contact.instagram if contact else None,
            dm=message.instagram_dm,
            sent_at=sent_at.get(creator.id),
        )
        for message, creator, contact in rows
    ]


def mark_dm_sent(session: Session, campaign_id: str, creator_id: int) -> None:
    item = next((i for i in dm_queue(session, campaign_id) if i.creator_id == creator_id), None)
    if item is None:
        raise DmNotSendableError("Only approved messages can be marked as sent")
    if not item.instagram_url:
        raise DmNotSendableError("No Instagram handle was found for this creator")
    outreach = Outreach(
        creator_id=creator_id,
        campaign_id=campaign_id,
        channel=OutreachChannel.INSTAGRAM_DM,
        recipient=item.instagram_url.lower(),
        status=DeliveryStatus.MANUAL_SENT,
        mode=MANUAL_MODE,
        attempts=1,
        sent_at=utc_now(),
    )
    session.add(outreach)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise DmNotSendableError("This DM was already marked as sent") from exc
    session.add(OutreachEvent(outreach_id=outreach.id, event=DeliveryStatus.MANUAL_SENT))
    session.commit()
