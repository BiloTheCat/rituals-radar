"""Bluesky (AT Protocol): public posts that mention Rituals.

Free and open. Post search works without an account on the public AppView
(api.bsky.app) but only returns the first page; with BLUESKY_HANDLE and a
BLUESKY_APP_PASSWORD (Settings -> Privacy and security -> App passwords) we
search through your PDS, which is the officially supported route.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from radar.social import SocialContext, build_post, log_relevance
from radar.util import iso, parse_iso

log = logging.getLogger(__name__)

PDS = "https://bsky.social"
PUBLIC_APPVIEW = "https://api.bsky.app"


def login(ctx: SocialContext, handle: str, app_password: str) -> str | None:
    response = ctx.session.post(f"{PDS}/xrpc/com.atproto.server.createSession",
                                json={"identifier": handle, "password": app_password})
    if response.status_code != 200:
        log.warning("Bluesky login failed (HTTP %s); falling back to the public API", response.status_code)
        return None
    return response.json().get("accessJwt")


def post_record(ctx: SocialContext, post: dict, search_query: str) -> dict:
    uri = post.get("uri", "")
    author = post.get("author") or {}
    record = post.get("record") or {}
    langs = record.get("langs") or []
    rkey = uri.rsplit("/", 1)[-1] if uri else ""
    return build_post(
        ctx,
        source="bluesky",
        id_kind="post",
        content_type="post",
        native_id=uri,
        text=record.get("text"),
        url=f"https://bsky.app/profile/{author.get('handle') or author.get('did')}/post/{rkey}" if rkey else None,
        author=author.get("did"),
        lang=langs[0] if langs else None,
        created_at=parse_iso(record.get("createdAt")),
        like_count=post.get("likeCount"),
        reply_count=post.get("replyCount"),
        share_count=(post.get("repostCount") or 0) + (post.get("quoteCount") or 0),
        search_query=search_query,
    )


def collect(ctx: SocialContext, handle: str | None, app_password: str | None) -> list[dict]:
    cfg = ctx.cfg.bluesky
    token = login(ctx, handle, app_password) if handle and app_password else None
    base = PDS if token else PUBLIC_APPVIEW
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    since = iso(ctx.now - timedelta(days=ctx.cfg.lookback_days))

    posts: dict[str, dict] = {}
    for term in cfg.search_terms:
        params = {"q": term, "limit": max(1, min(100, cfg.max_posts_per_term)), "sort": "latest", "since": since}
        response = ctx.session.get(f"{base}/xrpc/app.bsky.feed.searchPosts", params=params, headers=headers)
        if response.status_code != 200:
            log.warning("Bluesky search %r failed: HTTP %s %s", term, response.status_code, response.text[:200])
            continue
        for post in response.json().get("posts", []):
            if post.get("uri") and post["uri"] not in posts:
                posts[post["uri"]] = post_record(ctx, post, term)

    records = list(posts.values())
    log_relevance("Bluesky", records)
    return records
