"""Filter stage: evaluates every creator against the rules and stores pass/fail with reasons.

The stage is pure database work (no API calls), so it re-evaluates everyone on every run:
changing a threshold in settings.yaml and re-running is all it takes to re-qualify.
"""

from sqlmodel import Session, select

from outreach.config import Settings
from outreach.filtering.rules import CreatorFacts, QualificationRules, evaluate
from outreach.filtering.scoring import ScoreBreakdown, ScoreInputs, brand_fit_score
from outreach.models import (
    Classification,
    Contact,
    Creator,
    CreatorMetrics,
    EmailStatus,
    FilterResult,
    QualificationStatus,
    utc_now,
)
from outreach.stage import StageContext, StageReport


def qualify_creators(context: StageContext) -> StageReport:
    session = context.session
    rules = QualificationRules(
        context.settings.filters, context.settings.metrics.activity_window_days, utc_now()
    )
    metrics_by_id = _rows_by_creator_id(session, CreatorMetrics)
    classification_by_id = _rows_by_creator_id(session, Classification)
    contacts_by_id = _rows_by_creator_id(session, Contact)

    qualified = 0
    creators = session.exec(select(Creator)).all()
    for creator in creators:
        facts = CreatorFacts(
            creator, metrics_by_id.get(creator.id), classification_by_id.get(creator.id)
        )
        rejections = evaluate(facts, rules)
        result = FilterResult(
            creator_id=creator.id,
            run_id=context.run_id,
            status=QualificationStatus.REJECTED if rejections else QualificationStatus.QUALIFIED,
            reasons=[rejection.as_dict() for rejection in rejections],
            rules_version=context.settings.rules_version,
        )
        if not rejections:
            _apply_score(result, facts, contacts_by_id.get(creator.id), context.settings)
            qualified += 1
        session.merge(result)
    session.commit()
    return StageReport(
        {"evaluated": len(creators), "qualified": qualified, "rejected": len(creators) - qualified}
    )


def refresh_score(session: Session, creator_id: int, settings: Settings) -> None:
    """Recomputes a qualified creator's score after enrichment changed its contactability."""
    result = session.get(FilterResult, creator_id)
    if result is None or result.status != QualificationStatus.QUALIFIED:
        return
    facts = CreatorFacts(
        session.get(Creator, creator_id),
        session.get(CreatorMetrics, creator_id),
        session.get(Classification, creator_id),
    )
    _apply_score(result, facts, session.get(Contact, creator_id), settings)
    session.add(result)


def _apply_score(
    result: FilterResult, facts: CreatorFacts, contact: Contact | None, settings: Settings
) -> None:
    breakdown = _score(facts, contact, settings)
    result.score = breakdown.total
    result.score_breakdown = breakdown.as_dict()


def _score(facts: CreatorFacts, contact: Contact | None, settings: Settings) -> ScoreBreakdown:
    # Only called for qualified creators, for whom metrics and classification always exist.
    inputs = ScoreInputs(
        relevance=facts.classification.relevance,
        engagement_rate=facts.metrics.engagement_rate,
        subscriber_count=facts.creator.subscriber_count,
        uploads_in_activity_window=facts.metrics.uploads_in_activity_window,
        has_email=contact is not None and contact.email_status == EmailStatus.FOUND,
    )
    return brand_fit_score(inputs, settings.scoring, settings.filters)


def _rows_by_creator_id[T](session: Session, table: type[T]) -> dict[int, T]:
    return {row.creator_id: row for row in session.exec(select(table)).all()}
