"""YouTube Data API v3 client with explicit quota accounting."""

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

API_BASE_URL = "https://www.googleapis.com/youtube/v3"
YOUTUBE_BASE_URL = "https://www.youtube.com"
REQUEST_TIMEOUT_SECONDS = 20
MAX_IDS_PER_REQUEST = 50
MAX_TRANSIENT_ATTEMPTS = 4

# Official unit costs: https://developers.google.com/youtube/v3/determine_quota_cost
UNIT_COST_BY_ENDPOINT = {"search": 100, "channels": 1, "playlistItems": 1, "videos": 1}
QUOTA_EXHAUSTED_REASONS = {"quotaExceeded", "dailyLimitExceeded"}
TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}

ISO_DURATION_PATTERN = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


class YouTubeApiError(RuntimeError):
    def __init__(self, status_code: int, reason: str, message: str) -> None:
        super().__init__(f"YouTube API {status_code} ({reason}): {message}")
        self.status_code = status_code
        self.reason = reason


class TransientYouTubeError(YouTubeApiError):
    """Worth retrying: rate limits, server errors, network failures."""


class QuotaBudgetExceeded(RuntimeError):
    """Our own ceiling, or Google's daily quota, has been reached. Ends the stage cleanly."""


class QuotaTracker:
    def __init__(self, ceiling_units: int) -> None:
        self._ceiling_units = ceiling_units
        self._used_units = 0

    @property
    def used_units(self) -> int:
        return self._used_units

    def reserve(self, units: int) -> None:
        if self._used_units + units > self._ceiling_units:
            raise QuotaBudgetExceeded(
                f"Quota ceiling reached ({self._used_units}/{self._ceiling_units} units used)"
            )
        self._used_units += units


@dataclass(frozen=True)
class VideoSearch:
    text: str
    published_after: datetime
    max_results: int
    order: str
    relevance_language: str
    region_code: str | None = None


@dataclass(frozen=True)
class ChannelSnapshot:
    channel_id: str
    title: str
    handle: str | None
    description: str
    country: str | None
    subscriber_count: int | None
    subscribers_hidden: bool
    video_count: int | None
    total_view_count: int | None
    uploads_playlist_id: str | None
    topic_categories: list[str]

    @property
    def profile_url(self) -> str:
        if self.handle:
            return f"{YOUTUBE_BASE_URL}/{self.handle}"
        return f"{YOUTUBE_BASE_URL}/channel/{self.channel_id}"


@dataclass(frozen=True)
class VideoSnapshot:
    video_id: str
    channel_id: str
    title: str
    description: str
    published_at: datetime
    duration_seconds: int
    view_count: int | None
    like_count: int | None
    comment_count: int | None
    live_status: str


def parse_iso8601_duration(duration: str) -> int:
    """'PT1H2M3S' -> 3723. YouTube durations never use years/months, so those are rejected."""
    match = ISO_DURATION_PATTERN.match(duration)
    if not match:
        raise ValueError(f"Unsupported ISO-8601 duration: {duration!r}")
    parts = {name: int(value or 0) for name, value in match.groupdict().items()}
    return parts["days"] * 86400 + parts["hours"] * 3600 + parts["minutes"] * 60 + parts["seconds"]


def chunked(items: Sequence[str], size: int) -> Iterator[Sequence[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _optional_int(value: str | None) -> int | None:
    return int(value) if value is not None else None


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _topic_name(wikipedia_url: str) -> str:
    return wikipedia_url.rsplit("/", 1)[-1].replace("_", " ")


def parse_channel(item: dict[str, Any]) -> ChannelSnapshot:
    snippet = item.get("snippet", {})
    statistics = item.get("statistics", {})
    subscribers_hidden = bool(statistics.get("hiddenSubscriberCount", False))
    return ChannelSnapshot(
        channel_id=item["id"],
        title=snippet.get("title", ""),
        handle=snippet.get("customUrl"),
        description=snippet.get("description", ""),
        country=snippet.get("country"),
        subscriber_count=None
        if subscribers_hidden
        else _optional_int(statistics.get("subscriberCount")),
        subscribers_hidden=subscribers_hidden,
        video_count=_optional_int(statistics.get("videoCount")),
        total_view_count=_optional_int(statistics.get("viewCount")),
        uploads_playlist_id=item.get("contentDetails", {})
        .get("relatedPlaylists", {})
        .get("uploads"),
        topic_categories=[
            _topic_name(url) for url in item.get("topicDetails", {}).get("topicCategories", [])
        ],
    )


def parse_video(item: dict[str, Any]) -> VideoSnapshot:
    snippet = item["snippet"]
    statistics = item.get("statistics", {})
    # A missing likeCount means the creator hid likes; it is NOT zero, so it stays None.
    return VideoSnapshot(
        video_id=item["id"],
        channel_id=snippet["channelId"],
        title=snippet.get("title", ""),
        description=snippet.get("description", ""),
        published_at=_parse_timestamp(snippet["publishedAt"]),
        duration_seconds=parse_iso8601_duration(item["contentDetails"]["duration"]),
        view_count=_optional_int(statistics.get("viewCount")),
        like_count=_optional_int(statistics.get("likeCount")),
        comment_count=_optional_int(statistics.get("commentCount")),
        live_status=snippet.get("liveBroadcastContent", "none"),
    )


def _error_from_response(response: httpx.Response) -> Exception:
    try:
        error = response.json().get("error", {})
    except ValueError:
        error = {}
    reasons = [detail.get("reason", "") for detail in error.get("errors", [])]
    reason = reasons[0] if reasons else "unknown"
    message = error.get("message", response.text[:200])
    if reason in QUOTA_EXHAUSTED_REASONS:
        return QuotaBudgetExceeded(f"Google daily quota exhausted: {message}")
    if response.status_code in TRANSIENT_STATUS_CODES:
        return TransientYouTubeError(response.status_code, reason, message)
    return YouTubeApiError(response.status_code, reason, message)


class YouTubeClient:
    def __init__(
        self, api_key: str, quota: QuotaTracker, http_client: httpx.Client | None = None
    ) -> None:
        # The key travels in a header, not the query string, so it never appears in URLs,
        # logs or exception messages.
        self._http = http_client or httpx.Client(
            base_url=API_BASE_URL,
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"X-Goog-Api-Key": api_key},
        )
        self._quota = quota

    def search_channel_ids(self, search: VideoSearch) -> list[str]:
        params: dict[str, Any] = {
            "part": "snippet",
            "type": "video",
            "q": search.text,
            "maxResults": search.max_results,
            "order": search.order,
            "publishedAfter": search.published_after.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "relevanceLanguage": search.relevance_language,
            "fields": "items(snippet/channelId)",
        }
        if search.region_code:
            params["regionCode"] = search.region_code
        payload = self._get("search", params)
        return [item["snippet"]["channelId"] for item in payload.get("items", [])]

    def fetch_channels(self, channel_ids: Sequence[str]) -> list[ChannelSnapshot]:
        channels: list[ChannelSnapshot] = []
        for batch in chunked(channel_ids, MAX_IDS_PER_REQUEST):
            payload = self._get(
                "channels",
                {"part": "snippet,statistics,contentDetails,topicDetails", "id": ",".join(batch)},
            )
            channels.extend(parse_channel(item) for item in payload.get("items", []))
        return channels

    def fetch_recent_upload_ids(self, uploads_playlist_id: str, limit: int) -> list[str]:
        payload = self._get(
            "playlistItems",
            {
                "part": "contentDetails",
                "playlistId": uploads_playlist_id,
                "maxResults": limit,
                "fields": "items(contentDetails/videoId)",
            },
        )
        return [item["contentDetails"]["videoId"] for item in payload.get("items", [])]

    def fetch_videos(self, video_ids: Sequence[str]) -> list[VideoSnapshot]:
        videos: list[VideoSnapshot] = []
        for batch in chunked(video_ids, MAX_IDS_PER_REQUEST):
            payload = self._get(
                "videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(batch)}
            )
            videos.extend(parse_video(item) for item in payload.get("items", []))
        return videos

    @retry(
        retry=retry_if_exception_type(TransientYouTubeError),
        stop=stop_after_attempt(MAX_TRANSIENT_ATTEMPTS),
        wait=wait_exponential(multiplier=1, max=20),
        reraise=True,
    )
    def _get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        self._quota.reserve(UNIT_COST_BY_ENDPOINT[endpoint])
        try:
            response = self._http.get(f"/{endpoint}", params=params)
        except httpx.TransportError as exc:
            raise TransientYouTubeError(0, "network", str(exc)) from exc
        if response.is_success:
            return response.json()
        raise _error_from_response(response)
