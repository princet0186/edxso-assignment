"""Find and validate contact emails in public text. Addresses are only ever found, never
constructed or guessed — if nothing valid is found the record says "Not Found"."""

import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache

from email_validator import EmailNotValidError, caching_resolver, validate_email

from outreach.models import EmailSource, Video

EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
OBFUSCATED_AT = re.compile(r"\s*[\[({<]\s*at\s*[\])}>]\s*", re.IGNORECASE)
OBFUSCATED_DOT = re.compile(r"\s*[\[({<]\s*dot\s*[\])}>]\s*", re.IGNORECASE)
BUSINESS_CONTEXT = re.compile(
    r"business|collab|sponsor|inquir|enquir|contact|partner|promotion|\be-?mail\b", re.IGNORECASE
)
# Template defaults and tooling addresses. Some of these domains really exist and accept mail
# (mysite.com does), so a DNS check alone would wrongly accept them.
PLACEHOLDER_DOMAINS = {
    "example.com", "domain.com", "email.com", "yourdomain.com", "mysite.com", "yoursite.com",
    "website.com", "company.com", "yourcompany.com", "test.com",
    "sentry.io", "wixpress.com", "sentry.wixpress.com",
}  # fmt: skip
NON_PERSONAL_PREFIXES = ("noreply", "no-reply", "donotreply")
FILE_EXTENSION_TLDS = {"png", "jpg", "jpeg", "gif", "webp", "svg", "css", "js"}
# Characters before an address that are searched for business wording ("For business: ...").
CONTEXT_WINDOW_CHARS = 80
# An address repeated across uploads is the creator's standing contact, not a one-off mention.
MIN_REPEATS_ACROSS_VIDEOS = 2
DNS_TIMEOUT_SECONDS = 5
YOUTUBE_WATCH_URL = "https://www.youtube.com/watch?v="


@dataclass(frozen=True)
class EmailCandidate:
    address: str
    source: EmailSource
    source_url: str


@dataclass(frozen=True)
class EmailCheck:
    is_valid: bool
    reason: str | None = None


EmailChecker = Callable[[str], EmailCheck]


def deobfuscate(text: str) -> str:
    return OBFUSCATED_DOT.sub(".", OBFUSCATED_AT.sub("@", text))


def is_junk_email(address: str) -> bool:
    local_part, _, domain = address.partition("@")
    return (
        domain in PLACEHOLDER_DOMAINS
        or local_part.startswith(NON_PERSONAL_PREFIXES)
        or domain.rsplit(".", 1)[-1] in FILE_EXTENSION_TLDS
    )


def extract_emails(text: str) -> list[str]:
    """Distinct, lower-cased, non-junk addresses in order of first appearance."""
    found = (match.lower().rstrip(".") for match in EMAIL_PATTERN.findall(deobfuscate(text)))
    return [address for address in dict.fromkeys(found) if not is_junk_email(address)]


def has_business_context(text: str, address: str) -> bool:
    plain = deobfuscate(text).lower()
    position = plain.find(address)
    if position < 0:
        return False
    window = plain[max(position - CONTEXT_WINDOW_CHARS, 0) : position]
    return bool(BUSINESS_CONTEXT.search(window))


def candidates_from_channel_description(description: str, profile_url: str) -> list[EmailCandidate]:
    addresses = extract_emails(description)
    # Business-labelled addresses first: "Business: x@y.com" beats a stray mention.
    addresses.sort(key=lambda address: not has_business_context(description, address))
    return [EmailCandidate(a, EmailSource.CHANNEL_DESCRIPTION, profile_url) for a in addresses]


def candidates_from_video_descriptions(videos: Sequence[Video]) -> list[EmailCandidate]:
    occurrences: Counter[str] = Counter()
    first_video: dict[str, Video] = {}
    labelled: set[str] = set()
    for video in videos:
        for address in extract_emails(video.description):
            occurrences[address] += 1
            first_video.setdefault(address, video)
            if has_business_context(video.description, address):
                labelled.add(address)
    return [
        EmailCandidate(
            address,
            EmailSource.VIDEO_DESCRIPTION,
            YOUTUBE_WATCH_URL + first_video[address].video_id,
        )
        for address, count in occurrences.most_common()
        if count >= MIN_REPEATS_ACROSS_VIDEOS or address in labelled
    ]


@lru_cache(maxsize=1)
def _dns_resolver():
    return caching_resolver(timeout=DNS_TIMEOUT_SECONDS)


def check_deliverability(address: str) -> EmailCheck:
    """Syntax check plus a DNS lookup that the domain can actually receive mail."""
    try:
        validate_email(address, check_deliverability=True, dns_resolver=_dns_resolver())
    except EmailNotValidError as exc:
        return EmailCheck(is_valid=False, reason=str(exc))
    return EmailCheck(is_valid=True)
