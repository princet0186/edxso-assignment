"""Generate stage: one personalised email + Instagram DM per qualified creator.

Write → validate → (if needed) rewrite with the validator's feedback, up to a retry limit.
A draft that still fails is saved as NEEDS_REVIEW, so nothing unchecked can be sent.
"""

import json
import logging
from dataclasses import dataclass

from sqlmodel import Session, col, select

from outreach.config import BrandProfile, Settings, load_brand
from outreach.llm.client import LlmClient, LlmUnavailableError, build_llm_client
from outreach.llm.types import ChatMessage, LlmRequest, Role
from outreach.models import (
    Creator,
    FilterResult,
    OutreachMessage,
    QualificationStatus,
    ReviewStatus,
)
from outreach.personalize.brief import CreatorBrief, build_brief
from outreach.personalize.draft import OutreachDraft
from outreach.personalize.similarity import find_near_duplicates
from outreach.personalize.validators import count_words, find_issues
from outreach.prompts import PromptTemplate, load_prompt
from outreach.stage import StageContext, StageReport

STAGE_NAME = "generate"
PROMPT_VERSION = "outreach_v1"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GenerationOutcome:
    draft: OutreachDraft
    issues: list[str]
    attempts: int
    provider: str
    model: str


def generate_messages(context: StageContext) -> StageReport:
    session, settings = context.session, context.settings
    brand = load_brand()
    llm = build_llm_client(context.secrets, settings.llm, session)
    prompt = load_prompt(PROMPT_VERSION)
    # AUTO_APPROVE exists for unattended demos; it never approves a draft that failed checks.
    status_when_valid = (
        ReviewStatus.APPROVED if context.secrets.auto_approve else ReviewStatus.GENERATED
    )

    passed = needs_review = failed = 0
    for creator in context.limited(_creators_needing_messages(session, settings.campaign_id)):
        brief = build_brief(session, creator, settings)
        try:
            outcome = draft_message(llm, prompt, brief, brand, settings)
        except LlmUnavailableError as exc:
            context.record_error(STAGE_NAME, f"LLM unavailable: {exc}", creator.id)
            context.session.commit()
            failed += 1
            continue
        status = ReviewStatus.NEEDS_REVIEW if outcome.issues else status_when_valid
        session.add(_to_message(brief, outcome, status, settings.campaign_id))
        session.commit()
        if outcome.issues:
            needs_review += 1
        else:
            passed += 1
        logger.info(
            "Message for %s: %s after %d attempt(s)", creator.name, status, outcome.attempts
        )

    flagged = flag_similar_messages(
        session, settings.campaign_id, settings.personalization.similarity_warning_threshold
    )
    return StageReport(
        {
            "messages_passed_validation": passed,
            "messages_needing_review": needs_review,
            "generation_failures": failed,
            "similarity_warnings": flagged,
        }
    )


def _creators_needing_messages(session: Session, campaign_id: str) -> list[Creator]:
    has_message = select(OutreachMessage.creator_id).where(
        OutreachMessage.campaign_id == campaign_id
    )
    return list(
        session.exec(
            select(Creator)
            .join(FilterResult, col(FilterResult.creator_id) == col(Creator.id))
            .where(
                FilterResult.status == QualificationStatus.QUALIFIED,
                col(Creator.id).not_in(has_message),
            )
            .order_by(col(FilterResult.score).desc())
        ).all()
    )


def draft_message(
    llm: LlmClient,
    prompt: PromptTemplate,
    brief: CreatorBrief,
    brand: BrandProfile,
    settings: Settings,
) -> GenerationOutcome:
    limits = settings.personalization
    system_prompt = prompt.render(
        brand_name=brand.name,
        email_words_min=str(limits.email_words_min),
        email_words_max=str(limits.email_words_max),
        dm_words_min=str(limits.dm_words_min),
        dm_words_max=str(limits.dm_words_max),
    )
    messages = [
        ChatMessage(Role.SYSTEM, system_prompt),
        ChatMessage(Role.USER, json.dumps(brief.facts_payload(brand), ensure_ascii=False)),
    ]
    validation_context = brief.validation_context(brand, limits)
    max_attempts = limits.max_validation_retries + 1

    def ask() -> tuple[OutreachDraft, list[str], str, str]:
        result = llm.complete_json(
            LlmRequest(tuple(messages), limits.temperature, prompt.version), OutreachDraft
        )
        return (
            result.value,
            find_issues(result.value, validation_context),
            result.provider,
            result.model,
        )

    attempt = 1
    draft, issues, provider, model = ask()
    while issues and attempt < max_attempts:
        messages += [
            ChatMessage(Role.ASSISTANT, draft.model_dump_json()),
            ChatMessage(Role.USER, _revision_request(issues)),
        ]
        attempt += 1
        draft, issues, provider, model = ask()
    return GenerationOutcome(draft, issues, attempt, provider, model)


def _revision_request(issues: list[str]) -> str:
    bullet_list = "\n".join(f"- {issue}" for issue in issues)
    return f"Revise the JSON to fix every issue below, keeping everything else:\n{bullet_list}"


def _to_message(
    brief: CreatorBrief, outcome: GenerationOutcome, status: ReviewStatus, campaign_id: str
) -> OutreachMessage:
    draft = outcome.draft
    return OutreachMessage(
        creator_id=brief.creator_id,
        campaign_id=campaign_id,
        angle=brief.angle.angle,
        angle_reason=brief.angle.reason,
        email_subject=draft.email_subject,
        email_body=draft.email_body,
        email_word_count=count_words(draft.email_body),
        instagram_dm=draft.instagram_dm,
        dm_word_count=count_words(draft.instagram_dm),
        referenced_video_title=draft.referenced_video_title,
        signals_used=list(draft.signals_used),
        validation_issues=outcome.issues,
        attempts=outcome.attempts,
        provider=outcome.provider,
        model=outcome.model,
        prompt_version=PROMPT_VERSION,
        review_status=status,
    )


def flag_similar_messages(session: Session, campaign_id: str, threshold: float) -> int:
    messages = session.exec(
        select(OutreachMessage).where(OutreachMessage.campaign_id == campaign_id)
    ).all()
    creator_id_by_message = {message.id: message.creator_id for message in messages}
    duplicates = find_near_duplicates({m.id: m.email_body for m in messages}, threshold)
    for message in messages:
        near = duplicates.get(message.id)
        message.similarity_warning = None
        if near:
            other_creator = session.get(Creator, creator_id_by_message[near.other_id])
            message.similarity_warning = (
                f"{near.similarity:.0%} word-trigram overlap with message #{near.other_id} "
                f"({other_creator.name})"
            )
        session.add(message)
    session.commit()
    return len(duplicates)
