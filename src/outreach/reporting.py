"""Read-only views over the database, shared by the CLI, the API, the UI and the exports, so
every surface reports the same numbers from one implementation."""

from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlmodel import Session, col, func, select

from outreach.config import Settings
from outreach.models import (
    NOT_AVAILABLE,
    Classification,
    Contact,
    Creator,
    CreatorMetrics,
    DeliveryStatus,
    EmailStatus,
    FilterResult,
    Outreach,
    OutreachChannel,
    OutreachMessage,
    PipelineError,
    PipelineRun,
    QualificationStatus,
    ReviewStatus,
)

SENT_LABEL_BY_STATUS = {
    DeliveryStatus.SENT: "Yes",
    DeliveryStatus.SIMULATED: "Yes (simulated)",
    DeliveryStatus.MANUAL_SENT: "Yes (manual)",
}
EMAIL_NOT_SEARCHED = "Not searched (rejected)"
EMAIL_PENDING = "Pending enrichment"
SUBSCRIBERS_HIDDEN = "Hidden"


@dataclass(frozen=True)
class FunnelStep:
    label: str
    count: int


def _count(session: Session, statement: Any) -> int:
    return session.exec(select(func.count()).select_from(statement.subquery())).one()


def build_funnel(session: Session, settings: Settings) -> list[FunnelStep]:
    creators = session.exec(select(Creator)).all()
    in_range = [
        c
        for c in creators
        if c.subscriber_count is not None
        and settings.filters.subscriber_range_contains(c.subscriber_count)
    ]
    measured = session.exec(select(CreatorMetrics)).all()
    qualified = select(FilterResult).where(FilterResult.status == QualificationStatus.QUALIFIED)
    emails_found = select(Contact).where(Contact.email_status == EmailStatus.FOUND)
    messages = select(OutreachMessage).where(OutreachMessage.campaign_id == settings.campaign_id)
    approved = messages.where(OutreachMessage.review_status == ReviewStatus.APPROVED)
    return [
        FunnelStep("Discovered channels", len(creators)),
        FunnelStep("Within subscriber range", len(in_range)),
        FunnelStep("Recent videos fetched", sum(1 for c in in_range if c.videos_fetched_at)),
        FunnelStep(
            "Engagement rate computed", sum(1 for m in measured if m.engagement_rate is not None)
        ),
        FunnelStep("Niche classified", _count(session, select(Classification))),
        FunnelStep("Qualified", _count(session, qualified)),
        FunnelStep("Verified email found", _count(session, emails_found)),
        FunnelStep("Messages generated", _count(session, messages)),
        FunnelStep("Messages approved", _count(session, approved)),
    ]


def rejection_reason_counts(session: Session) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for result in session.exec(select(FilterResult)).all():
        counts.update(reason["code"] for reason in result.reasons)
    return dict(counts.most_common())


def recent_runs(session: Session, limit: int) -> list[PipelineRun]:
    return list(session.exec(select(PipelineRun).order_by(col(PipelineRun.id).desc()).limit(limit)))


def error_count(session: Session) -> int:
    return session.exec(select(func.count()).select_from(PipelineError)).one()


def _by_creator_id[T](session: Session, table: type[T]) -> dict[int, T]:
    return {row.creator_id: row for row in session.exec(select(table)).all()}


def _email_cell(result: FilterResult | None, contact: Contact | None) -> str:
    if contact is not None:
        return contact.email
    if result is not None and result.status == QualificationStatus.QUALIFIED:
        return EMAIL_PENDING
    return EMAIL_NOT_SEARCHED


def _engagement_cell(metrics: CreatorMetrics | None) -> str:
    if metrics is None or metrics.engagement_rate is None:
        return NOT_AVAILABLE
    return f"{metrics.engagement_rate:.2%}"


def _followers_cell(creator: Creator) -> int | str:
    return SUBSCRIBERS_HIDDEN if creator.subscriber_count is None else creator.subscriber_count


def _contact_fields(contact: Contact | None) -> dict[str, str]:
    if contact is None:
        return dict.fromkeys(
            ("Email Source", "Email Source URL", "Instagram", "TikTok", "X", "LinkedIn", "Website"),
            "",
        )
    return {
        "Email Source": contact.email_source,
        "Email Source URL": contact.email_source_url or "",
        "Instagram": contact.instagram or "",
        "TikTok": contact.tiktok or "",
        "X": contact.x or "",
        "LinkedIn": contact.linkedin or "",
        "Website": contact.website or "",
    }


def _classification_fields(label: Classification | None) -> dict[str, Any]:
    if label is None:
        return {
            "Niche": NOT_AVAILABLE,
            "Sub-niches": "",
            "Content Themes": NOT_AVAILABLE,
            "Tone": "",
            "Audience Level (inferred)": NOT_AVAILABLE,
            "Language": "",
            "Relevance": None,
        }
    return {
        "Niche": label.primary_niche,
        "Sub-niches": ", ".join(label.sub_niches),
        "Content Themes": "; ".join(label.content_themes),
        "Tone": label.tone,
        "Audience Level (inferred)": label.audience_level,
        "Language": label.language,
        "Relevance": label.relevance,
    }


def creator_rows(session: Session) -> list[dict[str, Any]]:
    """One row per discovered creator with every field the dataset deliverable needs.
    Data that does not exist publicly is labelled, never left blank or guessed."""
    results = _by_creator_id(session, FilterResult)
    metrics = _by_creator_id(session, CreatorMetrics)
    classifications = _by_creator_id(session, Classification)
    contacts = _by_creator_id(session, Contact)
    rows = []
    for creator in session.exec(select(Creator)).all():
        result = results.get(creator.id)
        metric = metrics.get(creator.id)
        contact = contacts.get(creator.id)
        rows.append(
            {
                "Creator ID": creator.id,
                "Name": creator.name,
                "Platform": "YouTube",
                "Profile URL": creator.profile_url,
                "Followers": _followers_cell(creator),
                "Engagement Rate": _engagement_cell(metric),
                "Engagement Method": metric.engagement_method if metric else NOT_AVAILABLE,
                **_classification_fields(classifications.get(creator.id)),
                "Email": _email_cell(result, contact),
                **_contact_fields(contact),
                "Audience Geography": creator.country or NOT_AVAILABLE,
                "Audience Age": NOT_AVAILABLE,
                "Audience Gender": NOT_AVAILABLE,
                "Status": result.status if result else "NOT_EVALUATED",
                "Score": result.score if result else None,
                "Reasons": "; ".join(r["message"] for r in result.reasons) if result else "",
                "Discovered Via Query": creator.discovered_via_query,
            }
        )
    return rows


def message_rows(session: Session, campaign_id: str) -> list[dict[str, Any]]:
    creators = {c.id: c for c in session.exec(select(Creator)).all()}
    contacts = _by_creator_id(session, Contact)
    messages = session.exec(
        select(OutreachMessage).where(OutreachMessage.campaign_id == campaign_id)
    ).all()
    rows = []
    for message in messages:
        contact = contacts.get(message.creator_id)
        rows.append(
            {
                "Message ID": message.id,
                "Name": creators[message.creator_id].name,
                "Email": contact.email if contact else EMAIL_PENDING,
                "Instagram": (contact.instagram or "") if contact else "",
                "Angle": message.angle,
                "Why This Angle": message.angle_reason,
                "Email Subject": message.email_subject,
                "Email Body": message.email_body,
                "Email Words": message.email_word_count,
                "Instagram DM": message.instagram_dm,
                "DM Words": message.dm_word_count,
                "Referenced Video": message.referenced_video_title or "",
                "Signals Used": ", ".join(message.signals_used),
                "Validation Issues": "; ".join(message.validation_issues),
                "Attempts": message.attempts,
                "Similarity Warning": message.similarity_warning or "",
                "Model": f"{message.provider}/{message.model}",
                "Prompt Version": message.prompt_version,
                "Review Status": message.review_status,
            }
        )
    return rows


def _delivery_status_label(
    message: OutreachMessage, contact: Contact | None, row: Outreach | None
) -> str:
    if row is not None:
        return row.status
    if message.review_status != ReviewStatus.APPROVED:
        return f"Awaiting review ({message.review_status})"
    if contact is None or contact.email_status != EmailStatus.FOUND:
        return "No email - DM only"
    return "Approved, not yet queued"


def tracker_rows(session: Session, campaign_id: str) -> list[dict[str, Any]]:
    """The outreach tracker: one row per creator with a generated message."""
    creators = {c.id: c for c in session.exec(select(Creator)).all()}
    contacts = _by_creator_id(session, Contact)
    deliveries = {
        (row.creator_id, row.channel): row
        for row in session.exec(select(Outreach).where(Outreach.campaign_id == campaign_id)).all()
    }
    messages = session.exec(
        select(OutreachMessage).where(OutreachMessage.campaign_id == campaign_id)
    ).all()
    rows = []
    for message in messages:
        contact = contacts.get(message.creator_id)
        email_row = deliveries.get((message.creator_id, OutreachChannel.EMAIL))
        dm_row = deliveries.get((message.creator_id, OutreachChannel.INSTAGRAM_DM))
        rows.append(
            {
                "Influencer": creators[message.creator_id].name,
                "Email": contact.email if contact else EMAIL_PENDING,
                "Message Generated": "Yes",
                "Review Status": message.review_status,
                "Sent": SENT_LABEL_BY_STATUS.get(email_row.status, "No") if email_row else "No",
                "Date": f"{email_row.sent_at:%Y-%m-%d %H:%M} UTC"
                if email_row and email_row.sent_at
                else "",
                "Status": _delivery_status_label(message, contact, email_row),
                "Mode": (email_row.mode or "") if email_row else "",
                "Attempts": email_row.attempts if email_row else 0,
                "Error": (email_row.last_error or "") if email_row else "",
                "Instagram DM Sent": "Yes (manual)" if dm_row else "No",
                "DM Date": f"{dm_row.sent_at:%Y-%m-%d %H:%M} UTC"
                if dm_row and dm_row.sent_at
                else "",
            }
        )
    return rows
