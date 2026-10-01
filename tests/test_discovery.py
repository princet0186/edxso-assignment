from datetime import UTC, datetime

from sqlmodel import Session, select

from outreach import discovery
from outreach.models import Creator, SearchQueryLog, Video
from outreach.sources.youtube import ChannelSnapshot, VideoSearch, VideoSnapshot
from outreach.stage import StageContext


def channel(channel_id: str, subscribers: int | None) -> ChannelSnapshot:
    return ChannelSnapshot(
        channel_id=channel_id,
        title=f"Channel {channel_id}",
        handle=None,
        description="",
        country="IN",
        subscriber_count=subscribers,
        subscribers_hidden=subscribers is None,
        video_count=10,
        total_view_count=1000,
        uploads_playlist_id=f"UU{channel_id}",
        topic_categories=[],
    )


class FakeYouTubeClient:
    """Stands in for the real client: same methods, canned data, records calls."""

    def __init__(self) -> None:
        self.searches: list[str] = []
        self.channels = {
            "in_range": channel("in_range", 20_000),
            "too_big": channel("too_big", 500_000),
            "hidden": channel("hidden", None),
        }

    def search_channel_ids(self, search: VideoSearch) -> list[str]:
        self.searches.append(search.text)
        return ["in_range", "too_big", "hidden", "in_range"]

    def fetch_channels(self, channel_ids: list[str]) -> list[ChannelSnapshot]:
        return [self.channels[channel_id] for channel_id in channel_ids]

    def fetch_recent_upload_ids(self, uploads_playlist_id: str, limit: int) -> list[str]:
        return ["video1"]

    def fetch_videos(self, video_ids: list[str]) -> list[VideoSnapshot]:
        return [
            VideoSnapshot(
                video_id="video1",
                channel_id="in_range",
                title="Python basics",
                description="",
                published_at=datetime(2026, 9, 1, tzinfo=UTC),
                duration_seconds=600,
                view_count=1000,
                like_count=50,
                comment_count=5,
                live_status="none",
            )
        ]


def run_discovery(context: StageContext, monkeypatch, fake: FakeYouTubeClient):
    monkeypatch.setattr(discovery, "build_youtube_client", lambda _context: fake)
    context.item_limit = 2
    return discovery.discover_creators(context)


def test_discovery_stores_all_channels_but_fetches_videos_only_in_range(
    context: StageContext, session: Session, monkeypatch
) -> None:
    report = run_discovery(context, monkeypatch, FakeYouTubeClient())

    creators = {c.platform_id: c for c in session.exec(select(Creator)).all()}
    assert set(creators) == {"in_range", "too_big", "hidden"}
    assert creators["in_range"].videos_fetched_at is not None
    assert creators["too_big"].videos_fetched_at is None
    assert len(session.exec(select(Video)).all()) == 1
    assert report.counts["new_creators"] == 3


def test_rerun_skips_searched_queries_and_creates_no_duplicates(
    context: StageContext, session: Session, monkeypatch
) -> None:
    fake = FakeYouTubeClient()
    run_discovery(context, monkeypatch, fake)
    searches_after_first_run = len(fake.searches)

    second_report = run_discovery(context, monkeypatch, fake)

    assert len(session.exec(select(Creator)).all()) == 3
    assert second_report.counts["new_creators"] == 0
    assert len(session.exec(select(SearchQueryLog)).all()) == 4
    assert len(fake.searches) == searches_after_first_run + 2
