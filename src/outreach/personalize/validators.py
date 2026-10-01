"""Automatic quality gate for generated outreach.

Each check returns a plain-English issue. Issues are fed back to the LLM for a rewrite, and
anything still failing is held for human review instead of being sent.
"""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from outreach.config import PersonalizationSettings
from outreach.personalize.draft import OutreachDraft

WORD_PATTERN = re.compile(r"[A-Za-z0-9]+(?:['’.\-][A-Za-z0-9]+)*")
NUMBER_PATTERN = re.compile(r"\d+(?:[.,]\d+)*")
PLACEHOLDER_PATTERN = re.compile(r"\[[^\]]*\]|\{[^}]*\}|<[^>]*>")
BANNED_PHRASES = (
    "hope this email finds you well",
    "came across your profile",
    "dear sir",
    "dear madam",
    "to whom it may concern",
    "we would like to collaborate with you",
)
MAX_SUBJECT_CHARS = 80
MIN_SIGNALS = 2
TITLE_MATCH_RATIO = 0.85
# Share of a title's meaningful words that must appear in the email for it to "reference" it.
MIN_TITLE_WORD_COVERAGE = 0.5
MIN_MEANINGFUL_WORD_LENGTH = 4


@dataclass(frozen=True)
class ValidationContext:
    address_as: str
    recent_titles: tuple[str, ...]
    allowed_numbers: frozenset[str]
    limits: PersonalizationSettings


def count_words(text: str) -> int:
    return len(WORD_PATTERN.findall(text))


def find_issues(draft: OutreachDraft, context: ValidationContext) -> list[str]:
    issues = [
        *_length_issues(draft, context.limits),
        *_addressing_issues(draft, context.address_as),
        *_reference_issues(draft, context.recent_titles),
        *_content_issues(draft, context.allowed_numbers),
    ]
    if len(set(draft.signals_used)) < MIN_SIGNALS:
        issues.append(f"Use at least {MIN_SIGNALS} personalization signals")
    return issues


def _length_issues(draft: OutreachDraft, limits: PersonalizationSettings) -> list[str]:
    issues = []
    email_words = count_words(draft.email_body)
    if not limits.email_words_min <= email_words <= limits.email_words_max:
        issues.append(
            f"Email body has {email_words} words; it must be "
            f"{limits.email_words_min}-{limits.email_words_max}"
        )
    dm_words = count_words(draft.instagram_dm)
    if not limits.dm_words_min <= dm_words <= limits.dm_words_max:
        issues.append(
            f"Instagram DM has {dm_words} words; it must be "
            f"{limits.dm_words_min}-{limits.dm_words_max}"
        )
    if len(draft.email_subject) > MAX_SUBJECT_CHARS:
        issues.append(f"Subject is longer than {MAX_SUBJECT_CHARS} characters")
    return issues


def _addressing_issues(draft: OutreachDraft, address_as: str) -> list[str]:
    name = address_as.lower()
    issues = []
    if name not in draft.email_body.lower():
        issues.append(f'Email must address the creator as "{address_as}"')
    if name not in draft.instagram_dm.lower():
        issues.append(f'DM must address the creator as "{address_as}"')
    return issues


def matching_title(claimed: str, recent_titles: tuple[str, ...]) -> str | None:
    for title in recent_titles:
        if SequenceMatcher(None, claimed.lower(), title.lower()).ratio() >= TITLE_MATCH_RATIO:
            return title
    return None


def _meaningful_words(text: str) -> set[str]:
    words = WORD_PATTERN.findall(text.lower())
    return {word for word in words if len(word) >= MIN_MEANINGFUL_WORD_LENGTH}


def _reference_issues(draft: OutreachDraft, recent_titles: tuple[str, ...]) -> list[str]:
    if not draft.referenced_video_title:
        return ["Reference one of the creator's recent videos and set referenced_video_title"]
    title = matching_title(draft.referenced_video_title, recent_titles)
    if title is None:
        return [
            f'"{draft.referenced_video_title}" is not one of the recent videos in the brief; '
            "reference a video from the brief only"
        ]
    title_words = _meaningful_words(title)
    if not title_words:
        return []
    covered = len(title_words & _meaningful_words(draft.email_body)) / len(title_words)
    if covered < MIN_TITLE_WORD_COVERAGE:
        return [f'The email does not clearly mention the video "{title}"']
    return []


def _content_issues(draft: OutreachDraft, allowed_numbers: frozenset[str]) -> list[str]:
    text = f"{draft.email_subject}\n{draft.email_body}\n{draft.instagram_dm}"
    lowered = text.lower()
    issues = [f'Remove the cliché "{phrase}"' for phrase in BANNED_PHRASES if phrase in lowered]
    placeholders = PLACEHOLDER_PATTERN.findall(text)
    if placeholders:
        issues.append(f"Remove placeholders: {', '.join(placeholders)}")
    invented = sorted({n for n in NUMBER_PATTERN.findall(text) if n not in allowed_numbers})
    if invented:
        issues.append(f"Numbers not in the brief (do not invent facts): {', '.join(invented)}")
    return issues
