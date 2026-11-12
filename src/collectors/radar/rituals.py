"""Rituals.com: product discovery (sitemap) and stock checks (product pages).

How stock is detected
---------------------
Every product page contains schema.org JSON-LD (the same data Google Shopping
uses), e.g. ``"offers": {"availability": "https://schema.org/OutOfStock",
"price": 8.72, "priceCurrency": "EUR"}``. That is a stable, documented contract,
so we read stock and price from there.

We deliberately do NOT look for texts like "tijdelijk uitverkocht": that text is
part of the translation bundle embedded in *every* product page, including pages
of products that are in stock (verified on 12 Nov 2026). Text matching would
report every product as sold out.

Politeness: only product pages and the sitemap are fetched (both allowed by
robots.txt), one request every few seconds, with an honest User-Agent.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable, Iterator
from datetime import datetime

import requests

from radar.config import Config
from radar.http_client import BlockedByRobots, PoliteSession
from radar.matching import Matcher
from radar.records import CATALOG_FIELDS, STOCK_FIELDS, conform
from radar.util import iso, parse_iso

log = logging.getLogger(__name__)

PRODUCT_URL_RE = re.compile(
    r"^https://www\.rituals\.com/(?P<locale>[a-z]{2}-[a-z]{2})/(?P<slug>[a-z0-9-]+?)-(?P<pid>\d{7})\.html$"
)
PRODUCT_ID_RE = re.compile(r"^\d{7}$")
_LDJSON_RE = re.compile(
    r"<script\b[^>]*\btype\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script\s*>",
    re.IGNORECASE | re.DOTALL,
)
_ORIGINAL_PRICE_RE = re.compile(
    r"<(?P<tag>[a-z0-9]+)\b[^>]*\bdata-testid\s*=\s*[\"']original-price[\"'][^>]*>(?P<body>.*?)</(?P=tag)\s*>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_SITEMAP_BLOCK_RE = re.compile(r"<(url|sitemap)>(.*?)</\1>", re.DOTALL)
_LOC_RE = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.DOTALL)
_LASTMOD_RE = re.compile(r"<lastmod>\s*(.*?)\s*</lastmod>", re.DOTALL)

# schema.org ItemAvailability values -> our normalised value
_AVAILABILITY = {
    "instock": "InStock",
    "limitedavailability": "LimitedAvailability",
    "onlineonly": "OnlineOnly",
    "instoreonly": "InStoreOnly",
    "outofstock": "OutOfStock",
    "soldout": "SoldOut",
    "discontinued": "Discontinued",
    "preorder": "PreOrder",
    "presale": "PreSale",
    "backorder": "BackOrder",
    "madetoorder": "MadeToOrder",
    "reserved": "Reserved",
}
# "In stock" = can be ordered online and ships now.
AVAILABLE_STATES = frozenset({"InStock", "LimitedAvailability", "OnlineOnly"})


# ---------------------------------------------------------------------------
# Parsing (pure functions, unit tested with fixtures)
# ---------------------------------------------------------------------------
def normalise_availability(value: object) -> str:
    if not value or not isinstance(value, str):
        return "Unknown"
    token = value.strip().rstrip("/").rsplit("/", 1)[-1].replace(" ", "").lower()
    return _AVAILABILITY.get(token, "Unknown")


def parse_euro(text: str | None) -> float | None:
    """'€10,90' -> 10.9 ; '€ 1.234,56' -> 1234.56 ; '10.90' -> 10.9"""
    if not text:
        return None
    cleaned = re.sub(r"[^\d,.]", "", text)
    if not cleaned:
        return None
    if "," in cleaned:  # Dutch notation: '.' = thousands, ',' = decimals
        cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return round(float(cleaned), 2)
    except ValueError:
        return None


def _to_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    if isinstance(value, str):
        return parse_euro(value)
    return None


def _offer_price(offer: dict) -> float | None:
    for key in ("price", "lowPrice"):
        value = _to_float(offer.get(key))
        if value is not None:
            return value
    return None


def _iter_ld_objects(html: str) -> Iterator[dict]:
    for raw in _LDJSON_RE.findall(html):
        raw = raw.strip()
        if raw.startswith("<!--"):
            raw = raw.removeprefix("<!--").removesuffix("-->")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        stack = data if isinstance(data, list) else [data]
        for obj in stack:
            if not isinstance(obj, dict):
                continue
            if isinstance(obj.get("@graph"), list):
                yield from (o for o in obj["@graph"] if isinstance(o, dict))
            else:
                yield obj


def _has_type(obj: dict, type_name: str) -> bool:
    types = obj.get("@type")
    return type_name in types if isinstance(types, list) else types == type_name


def _offers(product: dict) -> list[dict]:
    offers = product.get("offers")
    if isinstance(offers, dict):
        if _has_type(offers, "AggregateOffer") and isinstance(offers.get("offers"), list):
            return [o for o in offers["offers"] if isinstance(o, dict)] or [offers]
        return [offers]
    if isinstance(offers, list):
        return [o for o in offers if isinstance(o, dict)]
    return []


def _breadcrumb(objects: Iterable[dict], product_name: str | None) -> str | None:
    for obj in objects:
        if not _has_type(obj, "BreadcrumbList"):
            continue
        items = [i for i in obj.get("itemListElement", []) if isinstance(i, dict)]
        items.sort(key=lambda i: int(i.get("position", 0) or 0))
        names = []
        for position, item in enumerate(items, start=1):
            name = item.get("name")
            if not name and isinstance(item.get("item"), dict):
                name = item["item"].get("name")
            name = str(name or "").strip()
            if not name or name.lower() == "home":
                continue
            is_last = position == len(items)
            if is_last and product_name and product_name.lower().startswith(name.lower()):
                continue  # the last crumb is the product itself
            names.append(name)
        return " > ".join(names) or None
    return None


def parse_product_page(html: str) -> dict:
    """Extract product facts from a product page. Never raises.

    Returns a dict with product_name, sku, availability, price, currency,
    list_price, category_path and parse_status ('ok', 'no_jsonld' or
    'unknown_availability').
    """
    objects = list(_iter_ld_objects(html))
    product = next((o for o in objects if _has_type(o, "Product")), None)
    if product is None:
        return {"parse_status": "no_jsonld", "availability": "Unknown",
                "error_message": "No schema.org Product JSON-LD found (page layout changed?)"}

    offers = _offers(product)
    states = [normalise_availability(o.get("availability")) for o in offers] or ["Unknown"]
    # Several offers: the product is available if any offer is.
    availability = next((s for s in states if s in AVAILABLE_STATES), states[0])
    # Price: the offer behind that availability, else any priced offer, else the AggregateOffer itself.
    priced = [(o, s) for o, s in zip(offers, states) if _offer_price(o) is not None]
    offer = next((o for o, s in priced if s == availability), priced[0][0] if priced else {})
    container = product.get("offers") if isinstance(product.get("offers"), dict) else {}
    price = _offer_price(offer) if offer else _offer_price(container)
    currency = offer.get("priceCurrency") or container.get("priceCurrency") or None

    list_price = None
    match = _ORIGINAL_PRICE_RE.search(html)
    if match:
        list_price = parse_euro(_TAG_RE.sub("", match.group("body")))

    name = str(product.get("name") or "").strip() or None
    known = availability != "Unknown"
    return {
        "parse_status": "ok" if known else "unknown_availability",
        "error_message": None if known else "JSON-LD Product has no recognised offers.availability",
        "product_name": name,
        "sku": str(product.get("sku") or "").strip() or None,
        "availability": availability,
        "price": price,
        "currency": currency,
        "list_price": list_price,
        "category_path": _breadcrumb(objects, name),
    }


def parse_sitemap(xml: str) -> tuple[str, list[tuple[str, str | None]]]:
    """Return ('index'|'urlset', [(loc, lastmod), ...]).

    Regex instead of an XML parser: the structure is trivial and this avoids
    XML entity-expansion risks on remote input.
    """
    kind = "index" if "<sitemapindex" in xml[:2000] else "urlset"
    entries = []
    for _, block in _SITEMAP_BLOCK_RE.findall(xml):
        loc = _LOC_RE.search(block)
        if not loc:
            continue
        lastmod = _LASTMOD_RE.search(block)
        entries.append((loc.group(1).strip(), lastmod.group(1).strip() if lastmod else None))
    return kind, entries


_SMALL_WORDS = {"of", "and", "for", "with", "in", "the", "a"}


def slug_to_name(slug: str) -> str:
    """'the-ritual-of-sakura---small-gift-set' -> 'The Ritual of Sakura Small Gift Set'
    (fallback name when a page can't be parsed; real names come from JSON-LD)."""
    words = [w for w in slug.split("-") if w]
    return " ".join(w if (i and w in _SMALL_WORDS) else w.capitalize() for i, w in enumerate(words))


# ---------------------------------------------------------------------------
# Collectors (network)
# ---------------------------------------------------------------------------
def make_session(cfg: Config) -> PoliteSession:
    return PoliteSession(
        cfg.site.user_agent,
        delay_seconds=cfg.site.delay_seconds,
        timeout_seconds=cfg.site.timeout_seconds,
        respect_robots_txt=cfg.site.respect_robots_txt,
        accept_language="nl-NL,nl;q=0.9,en;q=0.8",
    )


def fetch_catalog(cfg: Config, session: PoliteSession, matcher: Matcher, *, now: datetime, run_id: str,
                  is_sample: bool = False) -> list[dict]:
    """All product URLs of the configured locale, from the sitemap."""
    index_url = f"{cfg.site.base_url}/sitemap.xml"
    response = session.get(index_url)
    response.raise_for_status()
    kind, entries = parse_sitemap(response.text)
    locale_marker = f"/{cfg.site.locale}/"
    if kind == "index":
        children = [loc for loc, _ in entries if locale_marker in loc]
        if not children:
            raise RuntimeError(f"No sitemap for locale {cfg.site.locale} in {index_url}")
        response = session.get(children[0])
        response.raise_for_status()
        _, entries = parse_sitemap(response.text)

    records = []
    seen = set()
    for loc, lastmod in entries:
        match = PRODUCT_URL_RE.match(loc)
        if not match or match.group("locale") != cfg.site.locale or match.group("pid") in seen:
            continue
        seen.add(match.group("pid"))
        records.append(conform({
            "run_id": run_id,
            "crawled_at": iso(now),
            "product_id": match.group("pid"),
            "url": loc,
            "slug": match.group("slug"),
            "lastmod": iso(parse_iso(lastmod)),
            "collection": matcher.product_collection(match.group("slug")),
            "is_sample": is_sample,
        }, CATALOG_FIELDS))
    log.info("Sitemap: %d product URLs for %s", len(records), cfg.site.locale)
    return records


def resolve_watchlist(cfg: Config, catalog: list[dict] | None) -> list[str]:
    """Turn the configured watchlist (URLs, product ids, collections) into product URLs."""
    by_id = {r["product_id"]: r for r in (catalog or [])}
    urls: dict[str, str] = {}

    for item in cfg.watchlist.products:
        match = PRODUCT_URL_RE.match(item)
        if match:
            urls.setdefault(match.group("pid"), item)
        elif PRODUCT_ID_RE.match(item):
            if item in by_id:
                urls.setdefault(item, by_id[item]["url"])
            else:
                log.warning("Product %s not found in the sitemap; skipped", item)
        else:
            log.warning("Watchlist entry is neither a product URL nor a 7-digit id: %r", item)

    for collection in cfg.watchlist.collections:
        members = sorted((r for r in by_id.values() if r["collection"] == collection),
                         key=lambda r: r["lastmod"] or "", reverse=True)
        for record in members:
            urls.setdefault(record["product_id"], record["url"])

    if len(urls) > cfg.watchlist.max_products:
        log.warning("Watchlist has %d products; only the first %d are checked (max_products)",
                    len(urls), cfg.watchlist.max_products)
    return list(urls.values())[: cfg.watchlist.max_products]


def needs_catalog(cfg: Config) -> bool:
    return bool(cfg.watchlist.collections) or any(PRODUCT_ID_RE.match(p) for p in cfg.watchlist.products)


def check_product(url: str, session: PoliteSession, matcher: Matcher, *, now: datetime, run_id: str) -> dict:
    """Fetch one product page and return a stock snapshot record (never raises)."""
    match = PRODUCT_URL_RE.match(url)
    product_id = match.group("pid") if match else None
    slug = match.group("slug") if match else ""
    base = {
        "snapshot_id": f"{product_id}-{now:%Y%m%dT%H%M%SZ}",
        "run_id": run_id,
        "scraped_at": iso(now),
        "product_id": product_id,
        "url": url,
        "is_sample": False,
    }
    try:
        response = session.get(url, headers={"Accept": "text/html"})
    except BlockedByRobots:
        return conform({**base, "availability": "Unknown", "parse_status": "blocked_by_robots",
                        "error_message": "Disallowed by robots.txt", "product_name": slug_to_name(slug),
                        "collection": matcher.product_collection(slug)}, STOCK_FIELDS)
    except requests.RequestException as exc:
        return conform({**base, "availability": "Unknown", "parse_status": "error",
                        "error_message": f"{type(exc).__name__}: {exc}"[:500], "product_name": slug_to_name(slug),
                        "collection": matcher.product_collection(slug)}, STOCK_FIELDS)

    base["http_status"] = response.status_code
    if response.status_code in (404, 410):
        return conform({**base, "availability": "Delisted", "is_available": False, "parse_status": "not_found",
                        "product_name": slug_to_name(slug), "collection": matcher.product_collection(slug)},
                       STOCK_FIELDS)
    if response.status_code != 200:
        return conform({**base, "availability": "Unknown", "parse_status": "error",
                        "error_message": f"HTTP {response.status_code}", "product_name": slug_to_name(slug),
                        "collection": matcher.product_collection(slug)}, STOCK_FIELDS)

    facts = parse_product_page(response.text)
    name = facts.get("product_name") or slug_to_name(slug)
    availability = facts["availability"]
    return conform({
        **base,
        "product_id": product_id or facts.get("sku"),
        "product_name": name,
        "collection": matcher.product_collection(name, slug),
        "category_path": facts.get("category_path"),
        "availability": availability,
        "is_available": (availability in AVAILABLE_STATES) if availability != "Unknown" else None,
        "price": facts.get("price"),
        "list_price": facts.get("list_price"),
        "currency": facts.get("currency"),
        "parse_status": facts["parse_status"],
        "error_message": facts.get("error_message"),
    }, STOCK_FIELDS)


def collect_stock(cfg: Config, session: PoliteSession, matcher: Matcher, *, now: datetime, run_id: str) -> list[dict]:
    catalog = fetch_catalog(cfg, session, matcher, now=now, run_id=run_id) if needs_catalog(cfg) else None
    urls = resolve_watchlist(cfg, catalog)
    log.info("Checking stock for %d products (≈%.0f s with a %.1f s delay)",
             len(urls), len(urls) * cfg.site.delay_seconds, cfg.site.delay_seconds)
    records = [check_product(url, session, matcher, now=now, run_id=run_id) for url in urls]
    summary: dict[str, int] = {}
    for r in records:
        summary[r["availability"]] = summary.get(r["availability"], 0) + 1
    log.info("Stock summary: %s", summary)
    return records
