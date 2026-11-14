from __future__ import annotations

from conftest import NOW, FakeResponse, FakeSession, fixture_text
from radar import postcodes, rituals
from radar.records import PC4_FIELDS, STORE_FIELDS

STORE_URL = "https://www.rituals.com/nl-nl/stores/amsterdam-kinkerstraat-2506"


def test_parse_store_page():
    facts = rituals.parse_store_page(fixture_text("store_page.html"))
    assert facts["store_name"] == "Amsterdam Kinkerstraat"
    assert facts["postal_code"] == "1053 DS" and facts["city"] == "Amsterdam"
    assert (facts["latitude"], facts["longitude"]) == (52.364908, 4.863715)
    assert facts["opening_hours"] == "ma 11:00-18:00 · di 10:00-18:00 · zo 12:00-18:00"  # sorted by weekday


def test_parse_store_page_without_jsonld():
    assert rituals.parse_store_page("<html><body>no data</body></html>") is None


def test_collect_stores(cfg):
    sitemap = f"""<urlset>
      <url><loc>{STORE_URL}</loc></url>
      <url><loc>https://www.rituals.com/nl-nl/stores/gone-9999</loc></url>
      <url><loc>https://www.rituals.com/en-nl/stores/amsterdam-kinkerstraat-2506</loc></url>
      <url><loc>https://www.rituals.com/nl-nl/the-ritual-of-sakura-foaming-shower-gel-1115587.html</loc></url>
    </urlset>"""
    session = FakeSession({
        "https://www.rituals.com/sitemap.xml": FakeResponse(200, fixture_text("sitemap_index.xml")),
        "https://www.rituals.com/nl-nl/sitemap-nl-nl.xml": FakeResponse(200, sitemap),
        STORE_URL: FakeResponse(200, fixture_text("store_page.html")),
        "https://www.rituals.com/nl-nl/stores/gone-9999": FakeResponse(404, "gone"),
    })
    records = rituals.collect_stores(cfg, session, now=NOW, run_id="r1")
    assert len(records) == 1
    record = records[0]
    assert list(record) == list(STORE_FIELDS)
    assert record["store_id"] == "2506" and record["url"] == STORE_URL and record["crawled_at"] == "2026-11-12T18:00:00Z"


def test_centroid_of_a_square_and_a_multipolygon():
    square = {"type": "Polygon", "coordinates": [[[4.0, 52.0], [5.0, 52.0], [5.0, 53.0], [4.0, 53.0], [4.0, 52.0]]]}
    assert postcodes.centroid(square) == (4.5, 52.5)
    # a big and a tiny square: the centre lies (almost) in the big one
    multi = {"type": "MultiPolygon", "coordinates": [
        square["coordinates"],
        [[[6.0, 52.0], [6.01, 52.0], [6.01, 52.01], [6.0, 52.01], [6.0, 52.0]]],
    ]}
    lon, lat = postcodes.centroid(multi)
    assert 4.5 < lon < 4.51 and 52.49 < lat < 52.51
    assert postcodes.centroid({"type": "Point", "coordinates": [1, 2]}) is None


def _feature(code, x):
    ring = [[x, 52.0], [x + 0.02, 52.0], [x + 0.02, 52.02], [x, 52.02], [x, 52.0]]
    return {"type": "Feature", "properties": {"postcode": code, "jaarcode": 2025},
            "geometry": {"type": "Polygon", "coordinates": [ring]}}


def test_collect_postcodes_follows_pages_and_falls_back_a_year():
    pages = {
        2025: [
            FakeResponse(200, payload={"features": [_feature(1011, 4.9), _feature(1012, 4.95)],
                                       "links": [{"rel": "next", "href": postcodes.ITEMS_URL + "?page=2"}]}),
            FakeResponse(200, payload={"features": [_feature(1013, 5.0)], "links": []}),
        ],
    }
    calls = []

    def respond(url, params):
        year = (params or {}).get("jaarcode", 2025)
        calls.append(year)
        if year != 2025:
            return FakeResponse(200, payload={"features": [], "links": []})  # 2026 not published yet
        return pages[2025].pop(0)

    session = FakeSession({postcodes.ITEMS_URL: respond})
    records = postcodes.collect_postcodes(session, now=NOW, run_id="r1")
    assert [r["pc4"] for r in records] == ["1011", "1012", "1013"]
    assert all(list(r) == list(PC4_FIELDS) and r["source_year"] == 2025 for r in records)
    assert round(records[0]["latitude"], 2) == 52.01 and round(records[0]["longitude"], 2) == 4.91
    assert calls[0] == 2025  # starts with last year (NOW is in 2026)
