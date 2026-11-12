"""TikTok via the official oEmbed endpoint.

TikTok's Research API is only open to non-profit academic researchers, and
scraping TikTok breaks its terms. What *is* open to everyone is oEmbed: give it
the URL of a public video and it returns the caption and author. So: you
collect interesting video links in config/tiktok_links.txt, this collector
enriches them. No view/like counts are available this way.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from radar.social import SocialContext, build_post, log_relevance

log = logging.getLogger(__name__)

OEMBED_URL = "https://www.tiktok.com/oembed"
VIDEO_ID_RE = re.compile(r"/video/(\d{8,25})")


def load_links(path: Path) -> list[str]:
    if not path.exists():
        return []
    links = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "tiktok.com" in line:
            links.append(line)
    return list(dict.fromkeys(links))


def created_at_from_video_id(video_id: str | None) -> datetime | None:
    """TikTok video ids start with a 32-bit Unix timestamp, so the upload time
    can be recovered from the id itself (oEmbed doesn't return a date)."""
    if not video_id or not video_id.isdigit():
        return None
    seconds = int(video_id) >> 32
    moment = datetime.fromtimestamp(seconds, tz=timezone.utc)
    return moment if 2016 <= moment.year <= 2100 else None


def collect(ctx: SocialContext) -> list[dict]:
    links = load_links(ctx.cfg.resolve(ctx.cfg.tiktok.links_file))
    if not links:
        log.info("No TikTok links in %s - skipping TikTok", ctx.cfg.tiktok.links_file)
        return []
    records = []
    for url in links:
        response = ctx.session.get(OEMBED_URL, params={"url": url})
        if response.status_code != 200:
            log.warning("TikTok oEmbed failed for %s: HTTP %s", url, response.status_code)
            continue
        data = response.json()
        match = VIDEO_ID_RE.search(url)
        video_id = str(data.get("embed_product_id") or (match.group(1) if match else "")) or None
        if not video_id:
            log.warning("Could not determine the TikTok video id for %s", url)
            continue
        records.append(build_post(
            ctx,
            source="tiktok",
            id_kind="video",
            content_type="short",
            native_id=video_id,
            text=data.get("title"),
            url=url,
            author=data.get("author_unique_id") or data.get("author_name"),
            created_at=created_at_from_video_id(video_id),
            search_query="manual link",
            force_relevant=True,  # you picked these links yourself
        ))
    log_relevance("TikTok", records)
    return records
