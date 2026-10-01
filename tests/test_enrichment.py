from datetime import UTC, datetime

import httpx
import pytest

from outreach.config import load_settings
from outreach.enrichment.emails import (
    EmailCheck,
    candidates_from_channel_description,
    candidates_from_video_descriptions,
    extract_emails,
    has_business_context,
)
from outreach.enrichment.links import LinkKind, classify_link, collect_profile_links
from outreach.enrichment.website import WebsiteEmailFinder
from outreach.models import EmailSource, Video

ENRICHMENT = load_settings().enrichment.model_copy(
    update={"seconds_between_requests_per_domain": 0}
)


def always_valid(address: str) -> EmailCheck:
    return EmailCheck(is_valid=True)


def video(video_id: str, description: str) -> Video:
    return Video(
        video_id=video_id,
        creator_id=1,
        title="t",
        description=description,
        published_at=datetime(2026, 9, 1, tzinfo=UTC),
        duration_seconds=600,
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Business: Asha.Codes@Gmail.com.", ["asha.codes@gmail.com"]),
        ("mail asha [at] gmail [dot] com", ["asha@gmail.com"]),
        ("collab: asha(at)studio(dot)in", ["asha@studio.in"]),
        ("icon logo@2x.png and noreply@service.com and you@example.com", []),
        ("Template footer: info@mysite.com", []),
    ],
)
def test_extract_emails_normalises_deobfuscates_and_drops_junk(text, expected) -> None:
    assert extract_emails(text) == expected


def test_business_labelled_channel_email_is_preferred() -> None:
    description = "Fan mail: fans@asha.dev. For business inquiries: deals@asha.dev"

    candidates = candidates_from_channel_description(description, "https://youtube.com/@asha")

    assert [c.address for c in candidates] == ["deals@asha.dev", "fans@asha.dev"]
    assert candidates[0].source == EmailSource.CHANNEL_DESCRIPTION


@pytest.mark.parametrize(
    ("text", "labelled"),
    [
        ("Email: me@asha.dev", True),
        ("For business inquiries me@asha.dev", True),
        ("Fan mail: me@asha.dev", False),
        ("Thanks me@asha.dev for the tip", False),
    ],
)
def test_business_context_detection(text, labelled) -> None:
    assert has_business_context(text, "me@asha.dev") is labelled


def test_video_email_needs_repetition_or_a_business_label() -> None:
    videos = [
        video("v1", "Thanks to guest@other.io for joining"),
        video("v2", "Contact me: me@asha.dev"),
        video("v3", "Code on GitHub. me@asha.dev"),
    ]

    candidates = candidates_from_video_descriptions(videos)

    assert [c.address for c in candidates] == ["me@asha.dev"]
    assert candidates[0].source_url == "https://www.youtube.com/watch?v=v2"


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        ("https://www.instagram.com/asha.codes/", LinkKind.INSTAGRAM),
        ("https://x.com/asha", LinkKind.X),
        ("https://linktr.ee/asha", LinkKind.LINK_HUB),
        ("https://amzn.to/3abc", LinkKind.OTHER_PLATFORM),
        ("https://asha.dev/blog", LinkKind.WEBSITE),
    ],
)
def test_classify_link(url, kind) -> None:
    assert classify_link(url) == kind


def test_website_comes_only_from_channel_description_not_sponsor_links() -> None:
    links = collect_profile_links(
        channel_description="Blog: https://asha.dev  Insta: https://instagram.com/asha.codes",
        video_descriptions=[
            "Sponsored by https://sponsor-hosting.com",
            "https://sponsor-hosting.com",
        ],
    )

    assert links.website == "https://asha.dev"
    assert links.instagram == "https://instagram.com/asha.codes"


def test_instagram_post_links_are_not_mistaken_for_profiles() -> None:
    links = collect_profile_links("Latest reel https://instagram.com/reel/Cx12", [])
    assert links.instagram is None


def site(pages: dict[str, tuple[int, str]]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        status, body = pages.get(request.url.path, (404, ""))
        return httpx.Response(status, text=body, headers={"content-type": "text/html"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_finder_follows_contact_link_to_mailto_address() -> None:
    client = site(
        {
            "/robots.txt": (200, "User-agent: *\nAllow: /"),
            "/": (200, '<a href="/contact">Contact</a>'),
            "/contact": (200, '<a href="mailto:hello@asha.dev?subject=Hi">Email</a>'),
        }
    )

    result = WebsiteEmailFinder(ENRICHMENT, always_valid, client).find_email("https://asha.dev/")

    assert result.email == "hello@asha.dev"
    assert result.source_url == "https://asha.dev/contact"


def test_finder_respects_robots_disallow() -> None:
    client = site(
        {
            "/robots.txt": (200, "User-agent: *\nDisallow: /"),
            "/": (200, "write to hello@asha.dev"),
        }
    )

    result = WebsiteEmailFinder(ENRICHMENT, always_valid, client).find_email("https://asha.dev/")

    assert result.email is None
    assert any("robots.txt disallows" in note for note in result.notes)


def test_finder_records_why_an_address_was_rejected() -> None:
    client = site({"/": (200, "write to hello@dead-domain.dev")})

    def rejecting(address: str) -> EmailCheck:
        return EmailCheck(is_valid=False, reason="domain has no mail server")

    result = WebsiteEmailFinder(ENRICHMENT, rejecting, client).find_email("https://asha.dev/")

    assert result.email is None
    assert "hello@dead-domain.dev rejected: domain has no mail server" in result.notes
