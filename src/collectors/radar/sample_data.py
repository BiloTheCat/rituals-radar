"""Generate realistic *sample* stock data so the pipeline and dashboard work on day one.

All records carry is_sample = true. The pipeline keeps or drops them via the
`include_sample_data` setting, so switching to real data is a config change.

The simulation is deterministic (seeded) and has a few deliberate patterns, so
the dashboard has something to show:
  * seasonal products (advent calendars, gift sets) sell out more often, and
    more so as the season approaches;
  * Sakura runs a 20% promotion for a few days;
  * two new products appear in the catalog halfway through the period.
"""

from __future__ import annotations

import random
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone

from radar.config import Config
from radar.landing import LandingWriter
from radar.matching import Matcher, matcher_from_config
from radar.records import CATALOG_FIELDS, PC4_FIELDS, STOCK_FIELDS, STORE_FIELDS, conform
from radar.rituals import PRODUCT_ID_RE, PRODUCT_URL_RE, slug_to_name
from radar.util import iso, utc_now

STOCK_HOURS = (0, 3, 6, 9, 12, 15, 18, 21)  # UTC check times, like the GitHub schedule
SEASONAL_HINTS = ("uukumwe", "advent", "gift-set", "surprise-box", "value-pack")  # limited / seasonal

# Real product URLs of the Uukumwe Collection (the Limited Edition on 13 Nov 2026),
# used when the watchlist follows that collection. Sample data can't look in the
# sitemap (no network), so other collections get a few made-up products.
KNOWN_COLLECTION_SLUGS = {
    "uukumwe": [
        "uukumwe-collection-creamy-foaming-shower-gel-200ml-1121636",
        "uukumwe-collection-rich-bath-and-shower-oil-200ml-1121639",
        "uukumwe-collection-warming-body-scrub-220g-1121640",
        "uukumwe-collection-rich-body-butter-220ml-1121645",
        "uukumwe-collection-hydrating-2-phase-body-oil-180ml-1121646",
        "uukumwe-collection-all-over-glow-balm-9g-1121648",
        "uukumwe-collection-edp-50ml-1121649",
        "uukumwe-collection-edp-15ml-1121650",
        "uukumwe-collection-fragrance-sticks-400ml-1121652",
        "uukumwe-collection-scented-candle-290g-1121653",
        "uukumwe-collection-hand-wash-300ml-1121654",
        "uukumwe-collection-tea-1121655",
        "uukumwe-collection-all-over-fragrance-mist-100ml-1121940",
        "uukumwe-collection-gift-set-1122351",
    ],
}


# A handful of made-up stores in big cities, and the 4-digit postcodes of those
# city centres (approximate coordinates). Only used until the real store pages
# and CBS postcode areas have been collected (python -m radar reference).
SAMPLE_STORES = [
    ("Amsterdam Centrum (sample)", "Kalverstraat 1", "1012 NX", "Amsterdam", 52.3702, 4.8913),
    ("Amsterdam Zuid (sample)", "Beethovenstraat 1", "1077 HM", "Amsterdam", 52.3456, 4.8774),
    ("Rotterdam Lijnbaan (sample)", "Lijnbaan 1", "3012 EA", "Rotterdam", 51.9198, 4.4747),
    ("Utrecht Centrum (sample)", "Oudegracht 1", "3511 AA", "Utrecht", 52.0907, 5.1214),
    ("Den Haag Centrum (sample)", "Spuistraat 1", "2511 BC", "Den Haag", 52.0786, 4.3125),
    ("Eindhoven (sample)", "Demer 1", "5611 AA", "Eindhoven", 51.4386, 5.4787),
    ("Groningen (sample)", "Herestraat 1", "9711 LA", "Groningen", 53.2147, 6.5672),
    ("Arnhem (sample)", "Ketelstraat 1", "6811 EE", "Arnhem", 51.9817, 5.9105),
    ("Zwolle (sample)", "Diezerstraat 1", "8011 RA", "Zwolle", 52.5130, 6.0937),
    ("Maastricht (sample)", "Grote Staat 1", "6211 CT", "Maastricht", 50.8497, 5.6912),
]
SAMPLE_PC4 = {
    "1012": (52.3731, 4.8926), "1053": (52.3690, 4.8650), "1077": (52.3470, 4.8780), "1102": (52.3130, 4.9530),
    "3011": (51.9200, 4.4840), "3511": (52.0920, 5.1180), "2511": (52.0800, 4.3150), "5611": (51.4400, 5.4780),
    "9711": (53.2190, 6.5660), "6811": (51.9830, 5.9100), "8011": (52.5110, 6.0940), "6211": (50.8510, 5.6900),
    "2011": (52.3810, 4.6360), "3811": (52.1560, 5.3880), "4811": (51.5870, 4.7760), "7511": (52.2210, 6.8940),
}


# ---------------------------------------------------------------------------
def _watch_products(cfg: Config, matcher: Matcher) -> list[dict]:
    products = []
    for key in cfg.watchlist.collections:
        slugs = KNOWN_COLLECTION_SLUGS.get(key) or [f"{key}-sample-product-{i}-11990{i:02d}" for i in range(1, 6)]
        for slug_id in slugs:
            slug, pid = slug_id.rsplit("-", 1)
            url = f"{cfg.site.base_url}/{cfg.site.locale}/{slug}-{pid}.html"
            products.append({"product_id": pid, "slug": slug, "url": url, "name": slug_to_name(slug),
                             "collection": key})
    for item in cfg.watchlist.products:
        match = PRODUCT_URL_RE.match(item)
        if match:
            slug, pid, url = match.group("slug"), match.group("pid"), item
        elif PRODUCT_ID_RE.match(item):
            slug, pid = f"product-{item}", item
            url = f"{cfg.site.base_url}/{cfg.site.locale}/{slug}-{pid}.html"
        else:
            continue
        products.append({"product_id": pid, "slug": slug, "url": url, "name": slug_to_name(slug),
                         "collection": matcher.product_collection(slug)})
    return products


def _base_price(slug: str) -> float:
    rules = [("edp-50ml", 69.9), ("edp-15ml", 29.9), ("gift-set", 49.9), ("candle", 34.9), ("tea", 9.9),
             ("glow-balm", 24.9), ("body-oil", 32.9), ("body-butter", 29.9), ("body-scrub", 24.9), ("hand-wash", 15.9),
             ("premium-advent", 149.0), ("advent", 99.0), ("medium-gift-set", 39.9), ("small-gift-set", 24.9),
             ("surprise-box", 49.9), ("value-pack", 29.9), ("50ml", 5.9), ("bath-foam", 19.9),
             ("fragrance-sticks", 34.9), ("shower", 10.9)]
    return next((price for key, price in rules if key in slug), 19.9)


# ---------------------------------------------------------------------------
def generate(cfg: Config, *, days: int = 30, seed: int = 42, now: datetime | None = None) -> dict[str, list[dict]]:
    """Return {"rituals_catalog": [...], "rituals_stock": [...], "rituals_stores": [...], "reference_pc4": [...]}."""
    rng = random.Random(seed)
    now = (now or utc_now()).astimezone(timezone.utc).replace(microsecond=0)
    matcher = matcher_from_config(cfg)
    products = _watch_products(cfg, matcher)
    start_day = (now - timedelta(days=days - 1)).date()
    out: dict[str, list[dict]] = defaultdict(list)

    # -- catalog: daily sitemap snapshot, two launches halfway ----------------
    launches = [
        {"product_id": "1199001", "slug": "the-ritual-of-sakura-limited-edition-gift-box-2026", "day": days // 2},
        {"product_id": "1199002", "slug": "the-ritual-of-karma-travel-set-2026", "day": (2 * days) // 3},
    ]
    for d in range(days):
        crawled = datetime.combine(start_day + timedelta(days=d), time(5, 45), tzinfo=timezone.utc)
        if crawled > now:
            break
        listed = [*products]
        for launch in launches:
            if d >= launch["day"]:
                url = f"{cfg.site.base_url}/{cfg.site.locale}/{launch['slug']}-{launch['product_id']}.html"
                listed.append({"product_id": launch["product_id"], "slug": launch["slug"], "url": url,
                               "collection": matcher.product_collection(launch["slug"])})
        for p in listed:
            out["rituals_catalog"].append(conform({
                "run_id": f"sample-{crawled:%Y%m%d}", "crawled_at": iso(crawled), "product_id": p["product_id"],
                "url": p["url"], "slug": p["slug"], "lastmod": iso(crawled - timedelta(hours=rng.randint(1, 48))),
                "collection": p["collection"], "is_sample": True,
            }, CATALOG_FIELDS))

    # -- stock: Markov chain per product, every 3 hours ------------------------
    promo_days = set(range(max(0, days // 3), max(0, days // 3) + 4))
    for p in products:
        seasonal = any(h in p["slug"] for h in SEASONAL_HINTS)
        in_stock = True
        base = _base_price(p["slug"])
        for d in range(days):
            day = start_day + timedelta(days=d)
            season = d / max(1, days - 1)  # 0 -> 1 over the period
            # Per 3-hour check. Long-run share out of stock ≈ p_out / (p_out + p_back):
            # seasonal items climb towards ~20% as the season nears, the rest stays ~2%.
            p_out = (0.005 + 0.035 * season) if seasonal else 0.004
            p_back = 0.15 if seasonal else 0.25
            for hour in STOCK_HOURS:
                scraped = datetime.combine(day, time(hour, 15), tzinfo=timezone.utc)
                if scraped > now:
                    break
                in_stock = (rng.random() >= p_out) if in_stock else (rng.random() < p_back)
                price = round(base * 0.8, 2) if (p["collection"] == "sakura" and d in promo_days) else base
                out["rituals_stock"].append(conform({
                    "snapshot_id": f"{p['product_id']}-{scraped:%Y%m%dT%H%M%SZ}",
                    "run_id": f"sample-{scraped:%Y%m%d%H}",
                    "scraped_at": iso(scraped),
                    "product_id": p["product_id"],
                    "product_name": p["name"],
                    "url": p["url"],
                    "collection": p["collection"],
                    "category_path": "Sample > " + p["collection"].replace("_", " ").title(),
                    "availability": "InStock" if in_stock else "OutOfStock",
                    "is_available": in_stock,
                    "price": price,
                    "list_price": base,
                    "currency": "EUR",
                    "http_status": 200,
                    "parse_status": "ok",
                    "error_message": None,
                    "is_sample": True,
                }, STOCK_FIELDS))

    # -- stores and postcode areas (static reference data) ----------------------
    fetched = now.replace(hour=4, minute=30, second=0)
    for i, (name, street, postcode, city, lat, lon) in enumerate(SAMPLE_STORES, start=1):
        out["rituals_stores"].append(conform({
            "run_id": "sample-reference", "crawled_at": iso(fetched), "store_id": f"sample-{i}",
            "store_name": name, "street_address": street, "postal_code": postcode, "city": city,
            "country": "Nederland", "latitude": lat, "longitude": lon, "phone": None,
            "opening_hours": "ma 12:00-18:00 · di 10:00-18:00 · wo 10:00-18:00 · do 10:00-21:00 · "
                             "vr 10:00-18:00 · za 10:00-18:00 · zo 12:00-17:00",
            "url": f"{cfg.site.base_url}/{cfg.site.locale}/stores", "is_sample": True,
        }, STORE_FIELDS))
    for pc4, (lat, lon) in sorted(SAMPLE_PC4.items()):
        out["reference_pc4"].append(conform({
            "run_id": "sample-reference", "fetched_at": iso(fetched), "pc4": pc4, "latitude": lat,
            "longitude": lon, "source_year": None, "is_sample": True,
        }, PC4_FIELDS))
    return dict(out)


def write_sample_data(cfg: Config, writer: LandingWriter, *, days: int = 30, seed: int = 42,
                      now: datetime | None = None) -> dict[str, int]:
    """Generate sample data and write one file per dataset per day. Returns record counts."""
    data = generate(cfg, days=days, seed=seed, now=now)
    counts: dict[str, int] = {}
    for key, records in data.items():
        time_field = {"rituals_stock": "scraped_at", "rituals_catalog": "crawled_at",
                      "rituals_stores": "crawled_at", "reference_pc4": "fetched_at"}[key]
        by_day: dict[str, list[dict]] = defaultdict(list)
        for record in records:
            by_day[record[time_field][:10]].append(record)
        for day, day_records in sorted(by_day.items()):
            when = datetime.fromisoformat(day + "T23:59:00+00:00")
            writer.write(key, day_records, run_id=f"sample{day.replace('-', '')}", when=when)
        counts[key] = len(records)
    return counts
