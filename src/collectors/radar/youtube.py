"""YouTube Data API v3: recent videos (incl. Shorts) about Rituals + their comments.

Free official API. Quota (default 10,000 units/day per Google Cloud project):
search.list is the expensive call, videos.list and commentThreads.list cost
1 unit each. With the default config a daily run uses well under the quota.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta

from radar.social import SocialContext, build_post, log_relevance
from radar.util import iso, parse_iso, truncate

log = logging.getLogger(__name__)

API = "https://www.googleapis.com/youtube/v3"
SHORT_MAX_SECONDS = 180  # YouTube Shorts are at most 3 minutes
_DURATION_RE = re.compile(r"^P(?:(?P<d>\d+)D)?(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?)?$")


class QuotaExceeded(Exception):
    pass


def parse_duration(value: str | None) -> int | None:
    """ISO-8601 duration ('PT1M5S') -> seconds."""
    if not value:
        return None
    match = _DURATION_RE.match(value)
    if not match:
        return None
    parts = {k: int(v) for k, v in match.groupdict(default="0").items()}
    return parts["d"] * 86400 + parts["h"] * 3600 + parts["m"] * 60 + parts["s"]


def _error_reason(response) -> str:
    try:
        return response.json()["error"]["errors"][0].get("reason", "")
    except (ValueError, KeyError, IndexError, TypeError):
        return ""


def _get(ctx: SocialContext, api_key: str, resource: str, params: dict) -> dict | None:
    response = ctx.session.get(f"{API}/{resource}", params={**params, "key": api_key})
    if response.status_code == 200:
        return response.json()
    reason = _error_reason(response)
    if reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
        raise QuotaExceeded(reason)
    if reason in ("commentsDisabled", "videoNotFound", "forbidden"):
        log.debug("YouTube %s skipped: %s", resource, reason)
        return None
    log.warning("YouTube %s failed: HTTP %s %s", resource, response.status_code, reason or response.text[:200])
    if response.status_code in (400, 401):
        response.raise_for_status()  # bad key / request: stop, this needs fixing
    return None


def video_record(ctx: SocialContext, item: dict, search_query: str | None) -> dict:
    snippet = item.get("snippet", {})
    stats = item.get("statistics", {})
    seconds = parse_duration(item.get("contentDetails", {}).get("duration"))
    is_short = seconds is not None and seconds <= SHORT_MAX_SECONDS
    video_id = item["id"]
    text = (snippet.get("title") or "").strip()
    description = truncate(snippet.get("description"), 1500)
    if description:
        text = f"{text}\n\n{description}"
    return build_post(
        ctx,
        source="youtube",
        id_kind="video",
        content_type="short" if is_short else "video",
        native_id=video_id,
        text=text,
        url=f"https://www.youtube.com/shorts/{video_id}" if is_short else f"https://www.youtube.com/watch?v={video_id}",
        author=snippet.get("channelId"),
        lang=snippet.get("defaultAudioLanguage") or snippet.get("defaultLanguage"),
        created_at=parse_iso(snippet.get("publishedAt")),
        like_count=stats.get("likeCount"),
        reply_count=stats.get("commentCount"),
        view_count=stats.get("viewCount"),
        search_query=search_query,
    )


def comment_record(ctx: SocialContext, thread: dict, video: dict) -> dict:
    top = thread.get("snippet", {}).get("topLevelComment", {})
    snippet = top.get("snippet", {})
    video_id = video["native_id"]
    author = (snippet.get("authorChannelId") or {}).get("value") or snippet.get("authorDisplayName")
    return build_post(
        ctx,
        source="youtube",
        id_kind="comment",
        content_type="comment",
        native_id=thread.get("id") or top.get("id"),
        text=snippet.get("textOriginal") or snippet.get("textDisplay"),
        url=f"https://www.youtube.com/watch?v={video_id}&lc={thread.get('id')}",
        parent_id=video_id,
        author=author,
        created_at=parse_iso(snippet.get("publishedAt")),
        like_count=snippet.get("likeCount"),
        reply_count=thread.get("snippet", {}).get("totalReplyCount"),
        search_query=video["search_query"],
        inherited=video["matched_collections"] if video["is_relevant"] else (),
    )


def collect(ctx: SocialContext, api_key: str | None) -> list[dict]:
    if not api_key:
        log.warning("YOUTUBE_API_KEY not set - skipping YouTube")
        return []
    cfg = ctx.cfg.youtube
    published_after = iso(ctx.now - timedelta(days=ctx.cfg.lookback_days))
    records: list[dict] = []
    try:
        found: dict[str, str] = {}  # video id -> search term that found it
        for term in cfg.search_terms:
            params = {
                "part": "snippet",
                "q": term,
                "type": "video",
                "order": "date",
                "maxResults": max(1, min(50, cfg.max_videos_per_term)),
                "publishedAfter": published_after,
            }
            if cfg.region_code:
                params["regionCode"] = cfg.region_code
            if cfg.relevance_language:
                params["relevanceLanguage"] = cfg.relevance_language
            data = _get(ctx, api_key, "search", params) or {}
            for item in data.get("items", []):
                video_id = (item.get("id") or {}).get("videoId")
                if video_id:
                    found.setdefault(video_id, term)

        ids = list(found)
        videos = []
        for start in range(0, len(ids), 50):
            batch = ids[start:start + 50]
            data = _get(ctx, api_key, "videos",
                        {"part": "snippet,statistics,contentDetails", "id": ",".join(batch)}) or {}
            for item in data.get("items", []):
                videos.append(video_record(ctx, item, found.get(item["id"])))
        records.extend(videos)

        if cfg.max_comments_per_video > 0:
            for video in videos:
                if not video["is_relevant"]:
                    continue  # don't spend quota on comments under unrelated videos
                data = _get(ctx, api_key, "commentThreads", {
                    "part": "snippet",
                    "videoId": video["native_id"],
                    "maxResults": max(1, min(100, cfg.max_comments_per_video)),
                    "order": "relevance",
                    "textFormat": "plainText",
                }) or {}
                records.extend(comment_record(ctx, thread, video) for thread in data.get("items", []))
    except QuotaExceeded as exc:
        log.warning("YouTube quota exhausted (%s); keeping the %d records collected so far", exc, len(records))
    log_relevance("YouTube", records)
    return records
