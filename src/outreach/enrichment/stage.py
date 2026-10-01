"""Enrichment stage: contact email, socials and website for every qualified creator.

Email sources are tried in order of reliability — channel description, then repeated or
business-labelled addresses in video descriptions, then the creator's own website — and the
first address that passes validation (syntax + mail-capable domain) wins.
"""

import logging

from sqlmodel import col, select

from outreach.enrichment.emails import (
    EmailCandidate,
    EmailChecker,
    candidates_from_channel_description,
    candidates_from_video_descriptions,
    check_deliverability,
)
from outreach.enrichment.links import ProfileLinks, collect_profile_links
from outreach.enrichment.website import WebsiteEmailFinder
from outreach.filtering.stage import refresh_score
from outreach.models import (
    Contact,
    Creator,
    EmailSource,
    EmailStatus,
    FilterResult,
    QualificationStatus,
    Video,
)
from outreach.stage import StageContext, StageReport

logger = logging.getLogger(__name__)


def enrich_contacts(context: StageContext) -> StageReport:
    session = context.session
    already_enriched = select(Contact.creator_id)
    pending = session.exec(
        select(Creator)
        .join(FilterResult, col(FilterResult.creator_id) == col(Creator.id))
        .where(
            FilterResult.status == QualificationStatus.QUALIFIED,
            col(Creator.id).not_in(already_enriched),
        )
    ).all()
    finder = WebsiteEmailFinder(context.settings.enrichment, check_deliverability)

    found_by_source: dict[str, int] = {}
    to_enrich = context.limited(list(pending))
    for creator in to_enrich:
        contact = _build_contact(context, creator, finder, check_deliverability)
        session.merge(contact)
        session.flush()
        refresh_score(session, creator.id, context.settings)
        session.commit()
        if contact.email_status == EmailStatus.FOUND:
            found_by_source[contact.email_source] = found_by_source.get(contact.email_source, 0) + 1
        logger.info("Enriched %s: %s", creator.name, contact.email)

    emails_found = sum(found_by_source.values())
    counts = {"creators_enriched": len(to_enrich), "emails_found": emails_found}
    counts |= {f"emails_from_{source.lower()}": n for source, n in found_by_source.items()}
    return StageReport(counts)


def _build_contact(
    context: StageContext, creator: Creator, finder: WebsiteEmailFinder, check: EmailChecker
) -> Contact:
    videos = context.session.exec(select(Video).where(Video.creator_id == creator.id)).all()
    links = collect_profile_links(creator.description, [v.description for v in videos])
    notes: list[str] = []
    candidates = [
        *candidates_from_channel_description(creator.description, creator.profile_url),
        *candidates_from_video_descriptions(videos),
    ]
    email = _first_valid(candidates, check, notes) or _search_own_site(links, finder, notes)

    contact = Contact(
        creator_id=creator.id,
        instagram=links.instagram,
        tiktok=links.tiktok,
        x=links.x,
        linkedin=links.linkedin,
        website=links.website or links.link_hub,
        notes=notes,
    )
    if email:
        contact.email = email.address
        contact.email_status = EmailStatus.FOUND
        contact.email_source = email.source
        contact.email_source_url = email.source_url
    return contact


def _first_valid(
    candidates: list[EmailCandidate], check: EmailChecker, notes: list[str]
) -> EmailCandidate | None:
    checked: set[str] = set()
    for candidate in candidates:
        if candidate.address in checked:
            continue
        checked.add(candidate.address)
        result = check(candidate.address)
        if result.is_valid:
            return candidate
        notes.append(f"{candidate.address} rejected: {result.reason}")
    return None


def _search_own_site(
    links: ProfileLinks, finder: WebsiteEmailFinder, notes: list[str]
) -> EmailCandidate | None:
    site = links.website or links.link_hub
    if site is None:
        return None
    result = finder.find_email(site)
    notes.extend(result.notes)
    if result.email is None:
        return None
    return EmailCandidate(result.email, EmailSource.WEBSITE, result.source_url or site)
