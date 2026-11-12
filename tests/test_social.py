"""Social collectors, tested against recorded-shape API responses (no network)."""

from __future__ import annotations

import dataclasses

import pytest

from conftest import FakeResponse
from radar import bluesky, tiktok, x_api, youtube
from radar.records import SOCIAL_FIELDS

YT_SEARCH = {"items": [{"id": {"kind": "youtube#video", "videoId": "vid1"}},
                       {"id": {"kind": "youtube#video", "videoId": "vid2"}}]}
YT_VIDEOS = {"items": [
    {"id": "vid1",
     "snippet": {"title": "Rituals Sakura review", "description": "Mijn eerlijke mening over The Ritual of Sakura.",
                 "channelId": "UC123", "publishedAt": "2026-11-10T09:30:00Z", "defaultAudioLanguage": "nl"},
     "statistics": {"viewCount": "1520", "likeCount": "88", "commentCount": "12"},
     "contentDetails": {"duration": "PT8M3S"}},
    {"id": "vid2",
     "snippet": {"title": "My morning routine", "description": "coffee and karma", "channelId": "UC999",
                 "publishedAt": "2026-11-11T07:00:00Z"},
     "statistics": {"viewCount": "40"}, "contentDetails": {"duration": "PT45S"}},
]}
YT_COMMENTS = {"items": [
    {"id": "thread1", "snippet": {"totalReplyCount": 2, "topLevelComment": {"id": "c1", "snippet": {
        "textOriginal": "Ruikt echt heerlijk!", "authorChannelId": {"value": "UCfan"},
        "likeCount": 5, "publishedAt": "2026-11-10T12:00:00.123456789Z"}}}},
]}


def test_parse_duration():
    assert youtube.parse_duration("PT8M3S") == 483
    assert youtube.parse_duration("PT1H") == 3600
    assert youtube.parse_duration("P1DT2S") == 86402
    assert youtube.parse_duration("garbage") is None


def test_youtube_collect(social_ctx):
    ctx = social_ctx({
        f"{youtube.API}/search": FakeResponse(200, payload=YT_SEARCH),
        f"{youtube.API}/videos": FakeResponse(200, payload=YT_VIDEOS),
        f"{youtube.API}/commentThreads": FakeResponse(200, payload=YT_COMMENTS),
    })
    records = youtube.collect(ctx, "fake-key")
    assert all(list(r) == list(SOCIAL_FIELDS) for r in records)
    by_id = {r["record_id"]: r for r in records}

    video = by_id["youtube:video:vid1"]
    assert video["content_type"] == "video" and video["view_count"] == 1520 and video["like_count"] == 88
    assert video["matched_collections"] == ["sakura"] and video["is_relevant"]
    assert video["author_hash"] and "UC123" not in video["author_hash"]  # pseudonymised

    short = by_id["youtube:video:vid2"]
    assert short["content_type"] == "short" and short["url"].endswith("/shorts/vid2")
    assert not short["is_relevant"]  # "karma" without the brand

    comment = by_id["youtube:comment:thread1"]
    assert comment["parent_id"] == "vid1" and comment["matched_collections"] == ["sakura"] and comment["is_relevant"]
    assert comment["created_at"] == "2026-11-10T12:00:00Z"  # nanosecond timestamp parsed
    # comments are only fetched for relevant videos (saves quota)
    comment_calls = [c for c in ctx.session.calls if c[1].endswith("/commentThreads")]
    assert [c[2]["videoId"] for c in comment_calls] == ["vid1"]
    assert all(c[2]["key"] == "fake-key" for c in ctx.session.calls)


def test_youtube_quota_exhausted_keeps_partial_results(social_ctx):
    quota = FakeResponse(403, payload={"error": {"errors": [{"reason": "quotaExceeded"}]}})
    ctx = social_ctx({
        f"{youtube.API}/search": FakeResponse(200, payload=YT_SEARCH),
        f"{youtube.API}/videos": FakeResponse(200, payload=YT_VIDEOS),
        f"{youtube.API}/commentThreads": quota,
    })
    records = youtube.collect(ctx, "fake-key")
    assert {r["record_id"] for r in records} == {"youtube:video:vid1", "youtube:video:vid2"}


def test_youtube_without_key_is_skipped(social_ctx):
    ctx = social_ctx({})
    assert youtube.collect(ctx, None) == [] and ctx.session.calls == []


BSKY_SEARCH = {"posts": [{
    "uri": "at://did:plc:abc/app.bsky.feed.post/3kxyz", "cid": "bafy",
    "author": {"did": "did:plc:abc", "handle": "fan.bsky.social"},
    "record": {"text": "Weer uitverkocht, de Rituals adventskalender 😤", "createdAt": "2026-11-12T08:15:00.000Z",
               "langs": ["nl"]},
    "likeCount": 3, "replyCount": 1, "repostCount": 2, "quoteCount": 1}]}


def test_bluesky_public_search(social_ctx):
    ctx = social_ctx({f"{bluesky.PUBLIC_APPVIEW}/xrpc/app.bsky.feed.searchPosts": FakeResponse(200, payload=BSKY_SEARCH)})
    records = bluesky.collect(ctx, None, None)
    assert len(records) == 1  # same post found by every search term -> deduplicated
    post = records[0]
    assert post["url"] == "https://bsky.app/profile/fan.bsky.social/post/3kxyz"
    assert post["share_count"] == 3 and post["lang"] == "nl"
    assert post["matched_collections"] == ["advent"] and post["is_relevant"]
    assert all(c[2]["since"] == "2026-11-05T18:00:00Z" for c in ctx.session.calls)


def test_bluesky_authenticated_search(social_ctx):
    ctx = social_ctx({
        f"{bluesky.PDS}/xrpc/com.atproto.server.createSession": FakeResponse(200, payload={"accessJwt": "jwt"}),
        f"{bluesky.PDS}/xrpc/app.bsky.feed.searchPosts": FakeResponse(200, payload=BSKY_SEARCH),
    })
    assert len(bluesky.collect(ctx, "me.bsky.social", "app-pass")) == 1
    assert any(c[1].startswith(bluesky.PDS + "/xrpc/app.bsky.feed.searchPosts") for c in ctx.session.calls)


def _x_page(start: int, count: int, next_token: str | None):
    data = [{"id": str(1000 + i), "text": f"Rituals Sakura post {i}", "created_at": "2026-11-12T10:00:00.000Z",
             "lang": "en", "author_id": "42",
             "public_metrics": {"like_count": 1, "reply_count": 0, "retweet_count": 2, "quote_count": 0,
                                "impression_count": 99}} for i in range(start, start + count)]
    return FakeResponse(200, payload={"data": data, "meta": {"next_token": next_token} if next_token else {}})


def test_x_respects_budget(social_ctx):
    pages = iter([_x_page(0, 30, "t2"), _x_page(30, 30, "t3"), _x_page(60, 30, None)])
    ctx = social_ctx({x_api.SEARCH_URL: lambda url, params: next(pages)})
    records = x_api.collect(ctx, "bearer")
    assert len(records) == 60  # cap = 50; stops after the page that crossed it (pages are paid per post)
    requested = [c[2]["max_results"] for c in ctx.session.calls]
    assert requested == [50, 20]
    assert records[0]["share_count"] == 2 and records[0]["view_count"] == 99


def test_x_without_token_is_skipped(social_ctx):
    ctx = social_ctx({})
    assert x_api.collect(ctx, None) == [] and ctx.session.calls == []


def test_x_stops_on_payment_required(social_ctx):
    ctx = social_ctx({x_api.SEARCH_URL: FakeResponse(402, '{"title": "CreditsDepleted"}')})
    assert x_api.collect(ctx, "bearer") == []


def test_tiktok_video_id_timestamp():
    # Example video from TikTok's oEmbed documentation, uploaded in July 2019
    moment = tiktok.created_at_from_video_id("6718335390845095173")
    assert moment.isoformat() == "2019-07-27T13:32:33+00:00"
    assert tiktok.created_at_from_video_id("abc") is None


def test_tiktok_collect(social_ctx, tmp_path, cfg):
    links = tmp_path / "links.txt"
    links.write_text("# comment\nhttps://www.tiktok.com/@scout2015/video/6718335390845095173\n\n", encoding="utf-8")
    ctx = social_ctx({tiktok.OEMBED_URL: FakeResponse(200, payload={
        "title": "Mijn favoriete douchefoam #sakura", "author_name": "Scout", "author_unique_id": "scout2015",
        "embed_product_id": "6718335390845095173"})})
    ctx.cfg = dataclasses.replace(cfg, tiktok=dataclasses.replace(cfg.tiktok, links_file=str(links)))
    records = tiktok.collect(ctx)
    assert len(records) == 1
    record = records[0]
    assert record["record_id"] == "tiktok:video:6718335390845095173"
    assert record["is_relevant"] and record["matched_collections"] == ["sakura"]  # curated link -> relevant
    assert record["created_at"] == "2019-07-27T13:32:33Z"


def test_tiktok_without_links_is_skipped(social_ctx):
    ctx = social_ctx({})
    assert tiktok.collect(ctx) == []


@pytest.mark.parametrize("module", [youtube, bluesky, x_api, tiktok])
def test_modules_document_their_source(module):
    assert module.__doc__ and len(module.__doc__) > 80
