"""Shared building blocks for the social media collectors."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from radar.config import Config
from radar.http_client import PoliteSession
from radar.matching import GENERAL, Matcher
from radar.records import SOCIAL_FIELDS, conform
from radar.util import iso, pseudonymize, to_int, truncate

log = logging.getLogger(__name__)

MAX_TEXT_CHARS = 5000


@dataclass
class SocialContext:
    cfg: Config
    matcher: Matcher
    now: datetime
    run_id: str
    session: PoliteSession | None = None
    is_sample: bool = False


def api_session(cfg: Config) -> PoliteSession:
    """Session for official APIs: no robots.txt (not a website crawl), small delay."""
    return PoliteSession(cfg.site.user_agent, delay_seconds=0.3, timeout_seconds=cfg.site.timeout_seconds,
                         respect_robots_txt=False)


def build_post(
    ctx: SocialContext,
    *,
    source: str,
    id_kind: str,
    content_type: str,
    native_id: str,
    text: str | None,
    url: str | None = None,
    parent_id: str | None = None,
    author: str | None = None,
    lang: str | None = None,
    created_at: datetime | None = None,
    like_count: object = None,
    reply_count: object = None,
    share_count: object = None,
    view_count: object = None,
    search_query: str | None = None,
    inherited: Sequence[str] = (),
    force_relevant: bool = False,
    collected_at: datetime | None = None,
) -> dict:
    collections, relevant = ctx.matcher.classify(text, inherited)
    if force_relevant and not relevant:
        collections, relevant = (collections or [GENERAL]), True
    return conform({
        "record_id": f"{source}:{id_kind}:{native_id}",
        "run_id": ctx.run_id,
        "collected_at": iso(collected_at or ctx.now),
        "source": source,
        "content_type": content_type,
        "native_id": str(native_id),
        "parent_id": parent_id,
        "url": url,
        "author_hash": pseudonymize(source, author),
        "text": truncate(text, MAX_TEXT_CHARS),
        "lang": (lang or None) and str(lang)[:12],
        "created_at": iso(created_at),
        "like_count": to_int(like_count),
        "reply_count": to_int(reply_count),
        "share_count": to_int(share_count),
        "view_count": to_int(view_count),
        "search_query": search_query,
        "matched_collections": collections,
        "is_relevant": relevant,
        "is_sample": ctx.is_sample,
    }, SOCIAL_FIELDS)


def log_relevance(source: str, records: Sequence[dict]) -> None:
    relevant = sum(1 for r in records if r["is_relevant"])
    log.info("%s: %d records collected, %d relevant to Rituals", source, len(records), relevant)
