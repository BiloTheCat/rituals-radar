"""X (Twitter) API v2 recent search.

PAID: since February 2026 the X API is pay-per-use (about $0.005 per post read,
check developer.x.com for current rates). This collector therefore:
  * only runs when X_BEARER_TOKEN is set,
  * stops at social.x.max_posts_per_run posts (your cost cap),
  * requests no user expansions (those are billed separately).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from radar.social import SocialContext, build_post, log_relevance
from radar.util import iso, parse_iso

log = logging.getLogger(__name__)

SEARCH_URL = "https://api.x.com/2/tweets/search/recent"
PRICE_PER_POST_USD = 0.005  # for the log line only; X bills according to its own rate card


def post_record(ctx: SocialContext, tweet: dict) -> dict:
    metrics = tweet.get("public_metrics") or {}
    return build_post(
        ctx,
        source="x",
        id_kind="post",
        content_type="post",
        native_id=tweet["id"],
        text=tweet.get("text"),
        url=f"https://x.com/i/web/status/{tweet['id']}",
        author=tweet.get("author_id"),
        lang=tweet.get("lang"),
        created_at=parse_iso(tweet.get("created_at")),
        like_count=metrics.get("like_count"),
        reply_count=metrics.get("reply_count"),
        share_count=(metrics.get("retweet_count") or 0) + (metrics.get("quote_count") or 0),
        view_count=metrics.get("impression_count"),
        search_query=ctx.cfg.x.query,
    )


def collect(ctx: SocialContext, bearer_token: str | None) -> list[dict]:
    cfg = ctx.cfg.x
    if not bearer_token:
        log.info("X_BEARER_TOKEN not set - skipping X (paid API)")
        return []
    if not cfg.query:
        log.warning("social.x.query is empty - skipping X")
        return []

    budget = max(10, cfg.max_posts_per_run)  # the API returns at least 10 posts per page
    hours = min(max(1, cfg.lookback_hours), 167)  # recent search covers the last 7 days
    params = {
        "query": cfg.query,
        "start_time": iso(ctx.now - timedelta(hours=hours)),
        "tweet.fields": "created_at,lang,public_metrics,author_id",
    }
    headers = {"Authorization": f"Bearer {bearer_token}"}
    records: list[dict] = []
    next_token = None
    while len(records) < budget:
        params["max_results"] = max(10, min(100, budget - len(records)))
        if next_token:
            params["next_token"] = next_token
        response = ctx.session.get(SEARCH_URL, params=params, headers=headers)
        if response.status_code != 200:
            log.error("X search failed: HTTP %s %s", response.status_code, response.text[:300])
            if response.status_code in (401, 402, 403):
                break  # bad token / no credits / no access: retrying won't help
            response.raise_for_status()
        data = response.json()
        records.extend(post_record(ctx, tweet) for tweet in data.get("data", []))
        next_token = (data.get("meta") or {}).get("next_token")
        if not next_token:
            break

    log.info("X: read %d posts (≈ $%.2f at $%.3f/post)", len(records), len(records) * PRICE_PER_POST_USD,
             PRICE_PER_POST_USD)
    log_relevance("X", records)
    return records
