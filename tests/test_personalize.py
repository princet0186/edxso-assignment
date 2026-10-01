import json

import pytest
from sqlmodel import Session
from tenacity import wait_none

from outreach.config import load_brand, load_settings
from outreach.llm.cache import LlmCache
from outreach.llm.client import LlmClient
from outreach.personalize.angle import AngleChoice, AngleInputs, CollaborationAngle, choose_angle
from outreach.personalize.brief import CreatorBrief, RecentVideo, address_name
from outreach.personalize.draft import OutreachDraft
from outreach.personalize.generator import draft_message
from outreach.personalize.similarity import find_near_duplicates
from outreach.personalize.validators import count_words, find_issues
from outreach.prompts import load_prompt

SETTINGS = load_settings()
BRAND = load_brand()
THRESHOLDS = SETTINGS.personalization.angles

BRIEF = CreatorBrief(
    creator_id=1,
    address_as="Code With Asha",
    channel_name="Code With Asha | Python & DSA",
    niche="EdTech",
    sub_niches=["Python", "DSA"],
    content_themes=["beginner Python tutorials", "DSA interview prep"],
    tone="calm, step-by-step",
    audience_level="beginner",
    country="IN",
    recent_videos=[
        RecentVideo("Binary Search Explained in 20 Minutes", "2026-09-20"),
        RecentVideo("Python Lists for Absolute Beginners", "2026-09-12"),
    ],
    angle=AngleChoice(CollaborationAngle.AFFILIATE, "Audience is mainly beginners"),
)
CONTEXT = BRIEF.validation_context(BRAND, SETTINGS.personalization)

GOOD_EMAIL = (
    "Hi Code With Asha, your Binary Search Explained in 20 Minutes video made a topic most "
    "beginners dread feel calm and logical. Your audience of new programmers is exactly who "
    "SkillSprint helps, with guided learning paths and mock interviews that give instant "
    "feedback. We would love to offer an affiliate partnership with a unique discount code "
    "for your viewers and commission on every sign-up. Would you be open to a quick reply or "
    "a short call next week to talk it through?"
)
GOOD_DM = (
    "Hey Code With Asha, loved your binary search breakdown! Would you be up for an "
    "affiliate partnership with a discount code for your viewers?"
)


def draft(**overrides) -> OutreachDraft:
    fields = {
        "email_subject": "Your binary search video + a SkillSprint idea",
        "email_body": GOOD_EMAIL,
        "instagram_dm": GOOD_DM,
        "referenced_video_title": "Binary Search Explained in 20 Minutes",
        "signals_used": ["recent_video", "audience", "collaboration"],
    }
    return OutreachDraft(**(fields | overrides))


def test_a_well_formed_draft_has_no_issues() -> None:
    assert 60 <= count_words(GOOD_EMAIL) <= 90
    assert 15 <= count_words(GOOD_DM) <= 30
    assert find_issues(draft(), CONTEXT) == []


def test_word_limits_are_enforced() -> None:
    issues = find_issues(draft(instagram_dm="Hi Code With Asha, collab?"), CONTEXT)
    assert any("Instagram DM has 5 words" in issue for issue in issues)


def test_invented_video_is_rejected() -> None:
    issues = find_issues(draft(referenced_video_title="My React Course Launch"), CONTEXT)
    assert any("not one of the recent videos" in issue for issue in issues)


def test_invented_numbers_are_rejected() -> None:
    body = GOOD_EMAIL.replace("feel calm", "reach 50000 people and feel calm")
    issues = find_issues(draft(email_body=body), CONTEXT)
    assert any("Numbers not in the brief" in issue and "50000" in issue for issue in issues)


def test_numbers_from_the_brief_are_allowed() -> None:
    # "20" comes from the real video title, so it is not an invented fact.
    assert not any("Numbers" in issue for issue in find_issues(draft(), CONTEXT))


@pytest.mark.parametrize(
    "bad_text",
    ["Hi [Creator Name], great video", "I hope this email finds you well, Code With Asha"],
)
def test_placeholders_and_cliches_are_rejected(bad_text: str) -> None:
    issues = find_issues(draft(email_body=f"{bad_text}. {GOOD_EMAIL}"), CONTEXT)
    assert any("placeholder" in i.lower() or "cliché" in i for i in issues)


def test_missing_creator_name_is_rejected() -> None:
    issues = find_issues(draft(instagram_dm=GOOD_DM.replace("Code With Asha", "there")), CONTEXT)
    assert 'DM must address the creator as "Code With Asha"' in issues


@pytest.mark.parametrize(
    ("channel_name", "expected"),
    [
        ("Code With Asha | Python & DSA", "Code With Asha"),
        ("Tech - Daily", "Tech"),
        ("Solo", "Solo"),
    ],
)
def test_address_name_drops_channel_taglines(channel_name: str, expected: str) -> None:
    assert address_name(channel_name) == expected


def angle_for(**overrides) -> CollaborationAngle:
    fields = {
        "engagement_rate": 0.03,
        "uploads_in_activity_window": 3,
        "subscriber_count": 30_000,
        "content_themes": ["coding news"],
        "audience_level": "professional",
    }
    return choose_angle(AngleInputs(**(fields | overrides)), THRESHOLDS).angle


def test_angle_rules_in_priority_order() -> None:
    assert angle_for(engagement_rate=0.07, uploads_in_activity_window=8) == (
        CollaborationAngle.BRAND_AMBASSADOR
    )
    assert (
        angle_for(content_themes=["python tutorials"]) == CollaborationAngle.SPONSORED_INTEGRATION
    )
    assert angle_for(content_themes=["dev tool reviews"]) == CollaborationAngle.PAID_PLACEMENT
    assert angle_for(audience_level="student") == CollaborationAngle.AFFILIATE
    assert angle_for(subscriber_count=8_000) == CollaborationAngle.UGC
    assert angle_for() == CollaborationAngle.BARTER


def test_near_duplicate_emails_are_flagged() -> None:
    templated = "Hi there, we love your content and want to work together on a campaign soon."
    texts = {1: templated, 2: templated.replace("Hi there", "Hello there"), 3: GOOD_EMAIL}

    duplicates = find_near_duplicates(texts, threshold=0.5)

    assert set(duplicates) == {1, 2}
    assert duplicates[1].other_id == 2


class SequenceBackend:
    name = "fake"
    model = "fake-model"

    def __init__(self, drafts: list[OutreachDraft]) -> None:
        self._replies = [d.model_dump_json() for d in drafts]
        self.requests = []

    def complete(self, request) -> str:
        self.requests.append(request)
        return self._replies[min(len(self.requests), len(self._replies)) - 1]


def test_failed_draft_is_rewritten_with_validator_feedback(session: Session) -> None:
    backend = SequenceBackend([draft(instagram_dm="Hi Code With Asha, collab?"), draft()])
    llm = LlmClient([backend], LlmCache(session), backoff=wait_none())

    outcome = draft_message(llm, load_prompt("outreach_v1"), BRIEF, BRAND, SETTINGS)

    assert outcome.issues == []
    assert outcome.attempts == 2
    feedback = backend.requests[1].messages[-1].content
    assert "Instagram DM has 5 words" in feedback


def test_draft_still_failing_after_retries_keeps_its_issues(session: Session) -> None:
    backend = SequenceBackend([draft(referenced_video_title="Invented Video")])
    llm = LlmClient([backend], LlmCache(session), backoff=wait_none())

    outcome = draft_message(llm, load_prompt("outreach_v1"), BRIEF, BRAND, SETTINGS)

    assert outcome.attempts == SETTINGS.personalization.max_validation_retries + 1
    assert outcome.issues


def test_brief_payload_contains_only_offer_for_chosen_angle() -> None:
    payload = BRIEF.facts_payload(BRAND)
    assert payload["collaboration"]["offer"] == BRAND.offers["AFFILIATE"]
    assert json.dumps(payload)  # serialisable for the prompt
