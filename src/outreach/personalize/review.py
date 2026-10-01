"""Human review actions. Only APPROVED messages can be sent, and only a message that passes
every validator can be approved — a reviewer fixes issues by editing, which re-validates."""

from dataclasses import dataclass

from pydantic import ValidationError
from sqlmodel import Session, select

from outreach.config import Settings, load_brand
from outreach.models import Creator, OutreachMessage, ReviewStatus, utc_now
from outreach.personalize.brief import build_brief
from outreach.personalize.draft import OutreachDraft
from outreach.personalize.validators import count_words, find_issues


class ReviewError(ValueError):
    """The requested review action is not allowed for this message."""


@dataclass(frozen=True)
class MessageEdit:
    email_subject: str
    email_body: str
    instagram_dm: str


def _get_message(session: Session, message_id: int) -> OutreachMessage:
    message = session.get(OutreachMessage, message_id)
    if message is None:
        raise ReviewError(f"Message {message_id} does not exist")
    return message


def approve(session: Session, message_id: int) -> None:
    message = _get_message(session, message_id)
    if message.validation_issues:
        raise ReviewError("Fix the validation issues (edit and save) before approving")
    _set_status(session, message, ReviewStatus.APPROVED)


def reject(session: Session, message_id: int) -> None:
    _set_status(session, _get_message(session, message_id), ReviewStatus.REJECTED)


def approve_all_valid(session: Session, campaign_id: str) -> int:
    ready = session.exec(
        select(OutreachMessage).where(
            OutreachMessage.campaign_id == campaign_id,
            OutreachMessage.review_status == ReviewStatus.GENERATED,
        )
    ).all()
    for message in ready:
        message.review_status = ReviewStatus.APPROVED
        message.reviewed_at = utc_now()
        session.add(message)
    session.commit()
    return len(ready)


def save_edit(
    session: Session, message_id: int, edit: MessageEdit, settings: Settings
) -> list[str]:
    """Applies a reviewer's edit and re-runs every validator. Returns the remaining issues."""
    message = _get_message(session, message_id)
    brief = build_brief(session, session.get(Creator, message.creator_id), settings)
    try:
        draft = OutreachDraft(
            email_subject=edit.email_subject,
            email_body=edit.email_body,
            instagram_dm=edit.instagram_dm,
            referenced_video_title=message.referenced_video_title,
            signals_used=message.signals_used,
        )
    except ValidationError as exc:
        fields = ", ".join(str(error["loc"][0]) for error in exc.errors())
        raise ReviewError(f"Edit not saved: {fields} is empty or too short") from exc
    issues = find_issues(draft, brief.validation_context(load_brand(), settings.personalization))

    message.email_subject = edit.email_subject
    message.email_body = edit.email_body
    message.email_word_count = count_words(edit.email_body)
    message.instagram_dm = edit.instagram_dm
    message.dm_word_count = count_words(edit.instagram_dm)
    message.validation_issues = issues
    message.edited_by_reviewer = True
    message.review_status = ReviewStatus.NEEDS_REVIEW if issues else ReviewStatus.GENERATED
    session.add(message)
    session.commit()
    return issues


def _set_status(session: Session, message: OutreachMessage, status: ReviewStatus) -> None:
    message.review_status = status
    message.reviewed_at = utc_now()
    session.add(message)
    session.commit()
