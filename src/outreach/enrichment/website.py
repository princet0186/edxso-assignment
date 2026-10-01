"""Polite crawler that looks for a contact email on a creator's own website.

Respects robots.txt, waits between requests to the same host, visits at most a few pages
(home, then contact/about/collab-style links), and never submits forms or solves captchas.
"""

import logging
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from outreach.config import EnrichmentSettings
from outreach.enrichment.emails import EmailChecker, extract_emails

CONTACT_LINK_HINTS = ("contact", "about", "collab", "work-with", "workwith", "business",
                      "sponsor", "partner", "hire")  # fmt: skip
HTML_CONTENT_TYPES = ("text/html", "application/xhtml+xml")
MAILTO_PREFIX = "mailto:"
CLIENT_ERROR_STATUS = 400
SERVER_ERROR_STATUS = 500

logger = logging.getLogger(__name__)


@dataclass
class WebsiteSearchResult:
    email: str | None = None
    source_url: str | None = None
    pages_visited: int = 0
    notes: list[str] = field(default_factory=list)


def emails_on_page(soup: BeautifulSoup) -> list[str]:
    """mailto: links first — they are deliberate — then addresses in visible text."""
    mailto = [
        anchor["href"][len(MAILTO_PREFIX) :].split("?")[0]
        for anchor in soup.select("a[href]")
        if str(anchor["href"]).lower().startswith(MAILTO_PREFIX)
    ]
    return list(
        dict.fromkeys(extract_emails(" ".join(mailto)) + extract_emails(soup.get_text(" ")))
    )


def contact_page_links(soup: BeautifulSoup, page_url: str) -> list[str]:
    host = urlparse(page_url).hostname
    links = []
    for anchor in soup.select("a[href]"):
        target = urljoin(page_url, str(anchor["href"])).split("#")[0]
        label = f"{target} {anchor.get_text(' ')}".lower()
        if urlparse(target).hostname == host and any(hint in label for hint in CONTACT_LINK_HINTS):
            links.append(target)
    return list(dict.fromkeys(links))


class WebsiteEmailFinder:
    def __init__(
        self,
        settings: EnrichmentSettings,
        check_email: EmailChecker,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._settings = settings
        self._check_email = check_email
        self._http = http_client or httpx.Client(
            timeout=settings.request_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": settings.user_agent},
        )
        self._last_request_at: dict[str, float] = {}

    def find_email(self, start_url: str) -> WebsiteSearchResult:
        result = WebsiteSearchResult()
        robots = self._robots_for(start_url, result)
        queue, visited = [start_url], set()
        while queue and len(visited) < self._settings.max_pages_per_site:
            url = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)
            if not robots.can_fetch(self._settings.user_agent, url):
                result.notes.append(f"robots.txt disallows {url}")
                continue
            soup = self._fetch_page(url, result)
            if soup is None:
                continue
            for address in emails_on_page(soup):
                check = self._check_email(address)
                if check.is_valid:
                    result.email, result.source_url = address, url
                    result.pages_visited = len(visited)
                    return result
                result.notes.append(f"{address} rejected: {check.reason}")
            queue.extend(link for link in contact_page_links(soup, url) if link not in visited)
        result.pages_visited = len(visited)
        return result

    def _robots_for(self, url: str, result: WebsiteSearchResult) -> RobotFileParser:
        parsed = urlparse(url)
        robots = RobotFileParser()
        try:
            response = self._polite_get(f"{parsed.scheme}://{parsed.netloc}/robots.txt")
        except httpx.HTTPError as exc:
            # RFC 9309: if robots.txt is unreachable, assume everything is disallowed.
            result.notes.append(f"robots.txt unreachable ({type(exc).__name__}); site skipped")
            robots.disallow_all = True
            return robots
        if response.status_code >= SERVER_ERROR_STATUS:
            result.notes.append("robots.txt server error; site skipped")
            robots.disallow_all = True
        elif response.status_code >= CLIENT_ERROR_STATUS:
            robots.allow_all = True  # no robots.txt means no restrictions
        else:
            robots.parse(response.text.splitlines())
        return robots

    def _fetch_page(self, url: str, result: WebsiteSearchResult) -> BeautifulSoup | None:
        try:
            response = self._polite_get(url)
        except httpx.HTTPError as exc:
            result.notes.append(f"{url} failed: {type(exc).__name__}")
            return None
        content_type = response.headers.get("content-type", "")
        if not response.is_success or not content_type.startswith(HTML_CONTENT_TYPES):
            result.notes.append(f"{url} skipped: HTTP {response.status_code} {content_type}")
            return None
        return BeautifulSoup(response.text, "html.parser")

    def _polite_get(self, url: str) -> httpx.Response:
        host = urlparse(url).hostname or ""
        elapsed = time.monotonic() - self._last_request_at.get(host, 0.0)
        wait = self._settings.seconds_between_requests_per_domain - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_request_at[host] = time.monotonic()
        return self._http.get(url)
