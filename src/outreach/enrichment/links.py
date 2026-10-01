"""Classify the links creators publish, to collect their socials and their own website.

The creator's website is only ever taken from the CHANNEL description. Video descriptions are
full of sponsor and affiliate links, and crawling a sponsor's site would return the sponsor's
email — a wrong contact presented as the creator's.
"""

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse

URL_PATTERN = re.compile(r"https?://[^\s<>\"'()\[\]]+", re.IGNORECASE)
TRAILING_PUNCTUATION = ".,;:!?"
MIN_REPEATS_ACROSS_VIDEOS = 2


class LinkKind(StrEnum):
    INSTAGRAM = "INSTAGRAM"
    TIKTOK = "TIKTOK"
    X = "X"
    LINKEDIN = "LINKEDIN"
    LINK_HUB = "LINK_HUB"
    OTHER_PLATFORM = "OTHER_PLATFORM"
    WEBSITE = "WEBSITE"


SOCIAL_KIND_BY_DOMAIN = {
    "instagram.com": LinkKind.INSTAGRAM,
    "tiktok.com": LinkKind.TIKTOK,
    "twitter.com": LinkKind.X,
    "x.com": LinkKind.X,
    "linkedin.com": LinkKind.LINKEDIN,
}
LINK_HUB_DOMAINS = {"linktr.ee", "bio.link", "beacons.ai", "linkin.bio", "solo.to", "bento.me"}
# Platforms, stores, shorteners and course marketplaces: never the creator's own website.
OTHER_PLATFORM_DOMAINS = {
    "youtube.com", "youtu.be", "facebook.com", "fb.com", "fb.me", "threads.net",
    "discord.gg", "discord.com", "t.me", "telegram.me", "wa.me", "whatsapp.com",
    "github.com", "gitlab.com", "medium.com", "reddit.com", "pinterest.com", "snapchat.com",
    "amazon.com", "amazon.in", "amzn.to", "amzn.in", "amzn.eu", "flipkart.com", "geni.us",
    "bit.ly", "tinyurl.com", "goo.gl", "rb.gy", "cutt.ly",
    "play.google.com", "apps.apple.com", "google.com", "forms.gle",
    "patreon.com", "buymeacoffee.com", "ko-fi.com", "paypal.me", "razorpay.me",
    "udemy.com", "coursera.org", "skillshare.com", "spotify.com",
}  # fmt: skip
NON_PROFILE_PATH_SEGMENTS = {
    "p", "reel", "reels", "explore", "stories", "tv", "intent", "share", "hashtag",
    "home", "search", "in", "company", "posts", "video",
}  # fmt: skip


@dataclass(frozen=True)
class ProfileLinks:
    instagram: str | None = None
    tiktok: str | None = None
    x: str | None = None
    linkedin: str | None = None
    website: str | None = None
    link_hub: str | None = None


def find_urls(text: str) -> list[str]:
    return list(
        dict.fromkeys(url.rstrip(TRAILING_PUNCTUATION) for url in URL_PATTERN.findall(text))
    )


def _host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host.removeprefix("www.").removeprefix("m.")


def _matches(host: str, domains: Iterable[str]) -> str | None:
    return next((d for d in domains if host == d or host.endswith(f".{d}")), None)


def classify_link(url: str) -> LinkKind:
    host = _host(url)
    social_domain = _matches(host, SOCIAL_KIND_BY_DOMAIN)
    if social_domain:
        return SOCIAL_KIND_BY_DOMAIN[social_domain]
    if _matches(host, LINK_HUB_DOMAINS):
        return LinkKind.LINK_HUB
    if _matches(host, OTHER_PLATFORM_DOMAINS):
        return LinkKind.OTHER_PLATFORM
    return LinkKind.WEBSITE


def social_profile_url(url: str) -> str | None:
    """Canonical profile URL, or None for links to a post/share page rather than a profile."""
    parsed = urlparse(url)
    segments = [s for s in parsed.path.split("/") if s]
    if classify_link(url) == LinkKind.LINKEDIN and len(segments) >= 2 and segments[0] == "in":
        return f"https://www.linkedin.com/in/{segments[1]}"
    if not segments or segments[0].lower() in NON_PROFILE_PATH_SEGMENTS:
        return None
    return f"https://{_host(url)}/{segments[0]}"


def _social_links_by_kind(urls: Sequence[str]) -> dict[LinkKind, str]:
    found: dict[LinkKind, str] = {}
    for url in urls:
        kind = classify_link(url)
        profile = social_profile_url(url) if kind in SOCIAL_KIND_BY_DOMAIN.values() else None
        if profile:
            found.setdefault(kind, profile)
    return found


def _repeated_social_links(video_descriptions: Sequence[str]) -> dict[LinkKind, str]:
    counts: Counter[tuple[LinkKind, str]] = Counter()
    for description in video_descriptions:
        counts.update(_social_links_by_kind(find_urls(description)).items())
    repeated: dict[LinkKind, str] = {}
    for (kind, profile), count in counts.most_common():
        if count >= MIN_REPEATS_ACROSS_VIDEOS:
            repeated.setdefault(kind, profile)
    return repeated


def collect_profile_links(
    channel_description: str, video_descriptions: Sequence[str]
) -> ProfileLinks:
    channel_urls = find_urls(channel_description)
    socials = _repeated_social_links(video_descriptions) | _social_links_by_kind(channel_urls)
    website = next((u for u in channel_urls if classify_link(u) == LinkKind.WEBSITE), None)
    link_hub = next((u for u in channel_urls if classify_link(u) == LinkKind.LINK_HUB), None)
    return ProfileLinks(
        instagram=socials.get(LinkKind.INSTAGRAM),
        tiktok=socials.get(LinkKind.TIKTOK),
        x=socials.get(LinkKind.X),
        linkedin=socials.get(LinkKind.LINKEDIN),
        website=website,
        link_hub=link_hub,
    )
