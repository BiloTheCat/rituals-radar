from __future__ import annotations

import dataclasses

import pytest
import requests

from conftest import NOW, FakeResponse, FakeSession, fixture_text
from radar import rituals
from radar.http_client import BlockedByRobots
from radar.records import CATALOG_FIELDS, STOCK_FIELDS

URL_IN = "https://www.rituals.com/nl-nl/the-ritual-of-sakura-foaming-shower-gel-1115587.html"
URL_OUT = "https://www.rituals.com/nl-nl/the-ritual-of-sakura-foaming-shower-gel-1116149.html"


# --- page parsing -----------------------------------------------------------
def test_in_stock_page():
    facts = rituals.parse_product_page(fixture_text("product_in_stock.html"))
    assert facts["parse_status"] == "ok"
    assert facts["availability"] == "InStock"
    assert facts["product_name"] == "The Ritual of Sakura Foaming Shower Gel 200 ml"
    assert facts["sku"] == "1115587"
    assert facts["price"] == 10.9
    assert facts["list_price"] == 10.9
    assert facts["currency"] == "EUR"
    assert facts["category_path"] == "Body > Bad & Douche > Douchegels"


def test_sold_out_text_on_in_stock_page_is_ignored():
    """Regression test: 'tijdelijk uitverkocht' is in every page's translation bundle."""
    html = fixture_text("product_in_stock.html")
    assert "tijdelijk uitverkocht" in html
    assert rituals.parse_product_page(html)["availability"] == "InStock"


def test_out_of_stock_page_with_discount():
    facts = rituals.parse_product_page(fixture_text("product_out_of_stock.html"))
    assert facts["availability"] == "OutOfStock"
    assert facts["price"] == 8.72
    assert facts["list_price"] == 10.9  # single-quoted attributes are supported too
    assert facts["category_path"] == "Body > Bad & Douche > Douchegels"


def test_page_without_product_jsonld():
    facts = rituals.parse_product_page(fixture_text("product_no_jsonld.html"))
    assert facts["parse_status"] == "no_jsonld"
    assert facts["availability"] == "Unknown"


@pytest.mark.parametrize("html", [
    # @graph container
    '<script type="application/ld+json">{"@graph":[{"@type":"WebPage"},{"@type":"Product","name":"X",'
    '"offers":{"availability":"http://schema.org/InStock","price":"12,50"}}]}</script>',
    # top-level list + list of @type values + AggregateOffer
    '<script type="application/ld+json">[{"@type":["Product","Thing"],"name":"X","offers":{"@type":"AggregateOffer",'
    '"lowPrice":12.5,"offers":[{"availability":"https://schema.org/OutOfStock"},'
    '{"availability":"https://schema.org/InStock","price":12.5}]}}]</script>',
])
def test_jsonld_variants(html):
    facts = rituals.parse_product_page(html)
    assert facts["availability"] == "InStock"
    assert facts["price"] == 12.5


def test_product_without_offers_is_not_ok():
    facts = rituals.parse_product_page('<script type="application/ld+json">{"@type":"Product","name":"X"}</script>')
    assert facts["parse_status"] == "unknown_availability"


@pytest.mark.parametrize("raw, expected", [
    ("https://schema.org/InStock", "InStock"),
    ("http://schema.org/OutOfStock", "OutOfStock"),
    ("https://schema.org/LimitedAvailability/", "LimitedAvailability"),
    ("PreOrder", "PreOrder"),
    ("SoldOut", "SoldOut"),
    ("something else", "Unknown"),
    (None, "Unknown"),
])
def test_normalise_availability(raw, expected):
    assert rituals.normalise_availability(raw) == expected


@pytest.mark.parametrize("text, expected", [
    ("€10,90", 10.9), ("€ 1.234,56", 1234.56), ("10.90", 10.9), ("8", 8.0), ("", None), ("gratis", None),
])
def test_parse_euro(text, expected):
    assert rituals.parse_euro(text) == expected


def test_slug_to_name():
    assert rituals.slug_to_name("the-ritual-of-sakura---small-gift-set-2026") == "The Ritual of Sakura Small Gift Set 2026"


# --- product checks (fake HTTP) --------------------------------------------
@pytest.mark.parametrize("response, availability, is_available, status", [
    (FakeResponse(200, fixture_text("product_in_stock.html")), "InStock", True, "ok"),
    (FakeResponse(200, fixture_text("product_out_of_stock.html")), "OutOfStock", False, "ok"),
    (FakeResponse(404, "gone"), "Delisted", False, "not_found"),
    (FakeResponse(503, "busy"), "Unknown", None, "error"),
    (FakeResponse(200, fixture_text("product_no_jsonld.html")), "Unknown", None, "no_jsonld"),
    (requests.ConnectionError("boom"), "Unknown", None, "error"),
    (BlockedByRobots(URL_IN), "Unknown", None, "blocked_by_robots"),
])
def test_check_product(matcher, response, availability, is_available, status):
    session = FakeSession({URL_IN: response})
    record = rituals.check_product(URL_IN, session, matcher, now=NOW, run_id="r1")
    assert list(record) == list(STOCK_FIELDS)
    assert record["product_id"] == "1115587"
    assert record["availability"] == availability
    assert record["is_available"] is is_available
    assert record["parse_status"] == status
    assert record["collection"] == "sakura"
    assert record["snapshot_id"] == "1115587-20261112T180000Z"
    assert record["scraped_at"] == "2026-11-12T18:00:00Z"
    assert record["product_name"]  # falls back to the URL slug when the page can't be parsed


# --- sitemap / catalog ------------------------------------------------------
def test_parse_sitemap_index_and_urlset():
    kind, entries = rituals.parse_sitemap(fixture_text("sitemap_index.xml"))
    assert kind == "index" and len(entries) == 4
    kind, entries = rituals.parse_sitemap(fixture_text("sitemap_nl_nl.xml"))
    assert kind == "urlset"
    assert ("https://www.rituals.com/nl-nl/the-ritual-of-sakura-foaming-shower-gel-1115587.html",
            "2026-11-11T23:06:08Z") in entries


def _catalog_session():
    return FakeSession({
        "https://www.rituals.com/sitemap.xml": FakeResponse(200, fixture_text("sitemap_index.xml")),
        "https://www.rituals.com/nl-nl/sitemap-nl-nl.xml": FakeResponse(200, fixture_text("sitemap_nl_nl.xml")),
    })


def test_fetch_catalog(cfg, matcher):
    records = rituals.fetch_catalog(cfg, _catalog_session(), matcher, now=NOW, run_id="r1")
    assert all(list(r) == list(CATALOG_FIELDS) for r in records)
    by_id = {r["product_id"]: r for r in records}
    assert set(by_id) == {"1115587", "1117148", "1121936", "1121933", "1122493"}  # deduped, nl-nl only
    assert by_id["1117148"]["collection"] == "yozakura"  # whole-word match: not "sakura"
    assert by_id["1115587"]["collection"] == "sakura"
    assert by_id["1121936"]["collection"] == "advent"
    assert by_id["1122493"]["collection"] == "other"
    assert by_id["1115587"]["lastmod"] == "2026-11-11T23:06:08Z"


def test_resolve_watchlist_with_ids_and_collections(cfg, matcher):
    catalog = rituals.fetch_catalog(cfg, _catalog_session(), matcher, now=NOW, run_id="r1")
    custom = dataclasses.replace(cfg, watchlist=dataclasses.replace(
        cfg.watchlist, products=(URL_OUT, "1117148", "9999999", "not a product"), collections=("advent",),
        max_products=3))
    urls = rituals.resolve_watchlist(custom, catalog)
    assert urls == [
        URL_OUT,
        "https://www.rituals.com/nl-nl/the-ritual-of-yozakura-foaming-shower-gel-1117148.html",
        "https://www.rituals.com/nl-nl/premium-advent-calendar-2026-1121936.html",  # newest lastmod first
    ]


def test_default_watchlist_is_valid(cfg):
    assert cfg.watchlist.products, "watchlist should not be empty"
    for item in cfg.watchlist.products:
        assert rituals.PRODUCT_URL_RE.match(item) or rituals.PRODUCT_ID_RE.match(item), item
    assert not rituals.needs_catalog(cfg)  # only URLs -> no sitemap download needed every 3 hours
    assert len(rituals.resolve_watchlist(cfg, None)) == len(cfg.watchlist.products)


def test_breadcrumb_keeps_collection_crumbs():
    html = ('<script type="application/ld+json">{"@type":"Product","name":"The Ritual of Sakura Body Cream 220 ml",'
            '"offers":{"availability":"https://schema.org/InStock","price":29.9}}</script>'
            '<script type="application/ld+json">{"@type":"BreadcrumbList","itemListElement":['
            '{"position":1,"name":"Home"},{"position":2,"name":"Collecties"},{"position":3,"name":"The Ritual of Sakura"},'
            '{"position":4,"item":{"name":"The Ritual of Sakura Body Cream"}}]}</script>')
    assert rituals.parse_product_page(html)["category_path"] == "Collecties > The Ritual of Sakura"
