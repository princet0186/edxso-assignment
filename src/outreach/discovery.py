"""Discovery stage: find candidate channels through YouTube search, then collect their recent
uploads. Channels outside the subscriber range are stored (so the dataset can show why they
were rejected) but their videos are never fetched — that saves quota for real candidates."""

import logging
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlmodel import Session, col, select

from outreach.config import DiscoverySettings, require
from outreach.models import Creator, Platform, SearchQueryLog, Video, utc_now
from outreach.sources.youtube import (
    ChannelSnapshot,
    TransientYouTubeError,
    VideoSearch,
    VideoSnapshot,
    YouTubeApiError,
    YouTubeClient,
)
from outreach.stage import StageContext, StageReport

STAGE_NAME = "discover"
logger = logging.getLogger(__name__)


def build_youtube_client(context: StageContext) -> YouTubeClient:
    api_key = require(context.secrets.youtube_data_api_key, "YOUTUBE_DATA_API_KEY")
    return YouTubeClient(api_key, context.youtube_quota)


def discover_creators(context: StageContext) -> StageReport:
    client = build_youtube_client(context)
    searched_queries, new_creators = _search_unsearched_queries(context, client)
    fetched, failed = _fetch_videos_for_pending_creators(context, client)
    return StageReport(
        {
            "queries_searched": searched_queries,
            "new_creators": new_creators,
            "creators_with_videos_fetched": fetched,
            "video_fetch_failures": failed,
        }
    )


def _search_unsearched_queries(context: StageContext, client: YouTubeClient) -> tuple[int, int]:
    session = context.session
    already_searched = set(session.exec(select(SearchQueryLog.query)).all())
    pending_queries = [q for q in context.settings.discovery.queries if q not in already_searched]
    queries_to_run = context.limited(pending_queries)
    published_after = utc_now() - timedelta(days=context.settings.discovery.published_within_days)

    new_creator_total = 0
    for query in queries_to_run:
        search = _build_search(query, published_after, context.settings.discovery)
        channel_ids = list(dict.fromkeys(client.search_channel_ids(search)))
        new_ids = _unknown_channel_ids(session, channel_ids)
        channels = client.fetch_channels(new_ids) if new_ids else []
        _save_new_creators(session, channels, query, context.run_id)
        session.add(SearchQueryLog(query=query, channel_ids_found=len(channel_ids)))
        session.commit()
        new_creator_total += len(channels)
        logger.info("Query %r: %d channels, %d new", query, len(channel_ids), len(channels))
    return len(queries_to_run), new_creator_total


def _build_search(query: str, published_after: datetime, rules: DiscoverySettings) -> VideoSearch:
    return VideoSearch(
        text=query,
        published_after=published_after,
        max_results=rules.results_per_query,
        order=rules.search_order,
        relevance_language=rules.relevance_language,
        region_code=rules.region_code,
    )


def _unknown_channel_ids(session: Session, channel_ids: list[str]) -> list[str]:
    known = set(
        session.exec(
            select(Creator.platform_id).where(
                Creator.platform == Platform.YOUTUBE, col(Creator.platform_id).in_(channel_ids)
            )
        ).all()
    )
    return [channel_id for channel_id in channel_ids if channel_id not in known]


def _save_new_creators(
    session: Session, channels: Sequence[ChannelSnapshot], query: str, run_id: int
) -> None:
    for channel in channels:
        session.add(
            Creator(
                platform=Platform.YOUTUBE,
                platform_id=channel.channel_id,
                name=channel.title,
                handle=channel.handle,
                profile_url=channel.profile_url,
                country=channel.country,
                subscriber_count=channel.subscriber_count,
                subscribers_hidden=channel.subscribers_hidden,
                video_count=channel.video_count,
                total_view_count=channel.total_view_count,
                description=channel.description,
                uploads_playlist_id=channel.uploads_playlist_id,
                topic_categories=channel.topic_categories,
                discovered_via_query=query,
                first_seen_run_id=run_id,
            )
        )


def _creators_pending_video_fetch(context: StageContext) -> list[Creator]:
    rules = context.settings.filters
    unfetched = context.session.exec(
        select(Creator).where(
            col(Creator.videos_fetched_at).is_(None),
            col(Creator.subscriber_count).is_not(None),
        )
    ).all()
    return [c for c in unfetched if rules.subscriber_range_contains(c.subscriber_count or 0)]


def _fetch_videos_for_pending_creators(
    context: StageContext, client: YouTubeClient
) -> tuple[int, int]:
    limit = context.settings.discovery.recent_videos_per_channel
    fetched = failed = 0
    for creator in context.limited(_creators_pending_video_fetch(context)):
        try:
            videos = _fetch_recent_videos(client, creator, limit)
        except TransientYouTubeError as exc:
            # Left unmarked so the next run retries it.
            context.record_error(STAGE_NAME, str(exc), creator.id)
            failed += 1
        except YouTubeApiError as exc:
            context.record_error(STAGE_NAME, str(exc), creator.id)
            creator.videos_fetched_at = utc_now()
            failed += 1
        else:
            _save_videos(context.session, creator, videos)
            creator.videos_fetched_at = utc_now()
            fetched += 1
        context.session.commit()
    return fetched, failed


def _fetch_recent_videos(
    client: YouTubeClient, creator: Creator, limit: int
) -> list[VideoSnapshot]:
    if not creator.uploads_playlist_id:
        return []
    video_ids = client.fetch_recent_upload_ids(creator.uploads_playlist_id, limit)
    return client.fetch_videos(video_ids) if video_ids else []


def _save_videos(session: Session, creator: Creator, videos: Sequence[VideoSnapshot]) -> None:
    for video in videos:
        session.merge(
            Video(
                video_id=video.video_id,
                creator_id=creator.id,
                title=video.title,
                description=video.description,
                published_at=video.published_at,
                duration_seconds=video.duration_seconds,
                view_count=video.view_count,
                like_count=video.like_count,
                comment_count=video.comment_count,
                live_status=video.live_status,
            )
        )
