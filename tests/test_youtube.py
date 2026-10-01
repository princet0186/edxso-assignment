import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from outreach.sources.youtube import (
    API_BASE_URL,
    QuotaBudgetExceeded,
    QuotaTracker,
    VideoSearch,
    YouTubeApiError,
    YouTubeClient,
    parse_iso8601_duration,
)

FIXTURES = Path(__file__).parent / "fixtures" / "youtube"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def client_returning(handler, ceiling: int = 1000) -> tuple[YouTubeClient, QuotaTracker]:
    quota = QuotaTracker(ceiling)
    http = httpx.Client(base_url=API_BASE_URL, transport=httpx.MockTransport(handler))
    return YouTubeClient("test-key", quota, http_client=http), quota


@pytest.mark.parametrize(
    ("duration", "seconds"),
    [("PT20M5S", 1205), ("PT45S", 45), ("PT1H", 3600), ("P1DT2H", 93600), ("P0D", 0)],
)
def test_parse_iso8601_duration(duration: str, seconds: int) -> None:
    assert parse_iso8601_duration(duration) == seconds


def test_parse_iso8601_duration_rejects_unknown_format() -> None:
    with pytest.raises(ValueError):
        parse_iso8601_duration("P1M")


def test_fetch_channels_parses_visible_and_hidden_subscribers() -> None:
    client, quota = client_returning(
        lambda request: httpx.Response(200, json=load_fixture("channels.json"))
    )

    visible, hidden = client.fetch_channels(["UC_visible", "UC_hidden"])

    assert visible.subscriber_count == 48200
    assert visible.profile_url == "https://www.youtube.com/@codewithasha"
    assert visible.topic_categories == ["Technology", "Knowledge"]
    assert hidden.subscriber_count is None
    assert hidden.subscribers_hidden
    assert hidden.profile_url == "https://www.youtube.com/channel/UC_hidden"
    assert quota.used_units == 1


def test_fetch_videos_keeps_hidden_likes_as_none_not_zero() -> None:
    client, _ = client_returning(
        lambda request: httpx.Response(200, json=load_fixture("videos.json"))
    )

    long_video, short_video = client.fetch_videos(["vid_long", "vid_hidden_likes"])

    assert long_video.duration_seconds == 1205
    assert long_video.like_count == 600
    assert short_video.like_count is None


def test_search_costs_100_units_and_sends_key_as_header() -> None:
    seen_headers = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(request.headers)
        assert "key=" not in str(request.url)
        return httpx.Response(200, json={"items": [{"snippet": {"channelId": "UC1"}}]})

    quota = QuotaTracker(1000)
    http = httpx.Client(
        base_url=API_BASE_URL,
        transport=httpx.MockTransport(handler),
        headers={"X-Goog-Api-Key": "test-key"},
    )
    search = VideoSearch("python", datetime(2026, 7, 1, tzinfo=UTC), 50, "relevance", "en")
    assert YouTubeClient("unused", quota, http_client=http).search_channel_ids(search) == ["UC1"]
    assert quota.used_units == 100
    assert seen_headers["x-goog-api-key"] == "test-key"


def test_quota_tracker_refuses_to_exceed_ceiling() -> None:
    quota = QuotaTracker(150)
    quota.reserve(100)
    with pytest.raises(QuotaBudgetExceeded):
        quota.reserve(100)
    assert quota.used_units == 100


def test_google_quota_exhaustion_is_reported_as_budget_exceeded() -> None:
    body = {"error": {"message": "quota", "errors": [{"reason": "quotaExceeded"}]}}
    client, _ = client_returning(lambda request: httpx.Response(403, json=body))

    with pytest.raises(QuotaBudgetExceeded):
        client.fetch_channels(["UC1"])


def test_non_transient_error_is_raised_without_retry() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        body = {"error": {"message": "not found", "errors": [{"reason": "playlistNotFound"}]}}
        return httpx.Response(404, json=body)

    client, _ = client_returning(handler)
    with pytest.raises(YouTubeApiError) as error:
        client.fetch_recent_upload_ids("UU_missing", 10)
    assert error.value.reason == "playlistNotFound"
    assert len(calls) == 1
