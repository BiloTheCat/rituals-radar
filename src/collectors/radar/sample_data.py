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
from radar.records import CATALOG_FIELDS, STOCK_FIELDS, conform
from radar.rituals import PRODUCT_ID_RE, PRODUCT_URL_RE, slug_to_name
from radar.util import iso, utc_now

STOCK_HOURS = (0, 3, 6, 9, 12, 15, 18, 21)  # UTC check times, like the GitHub schedule
SEASONAL_HINTS = ("advent", "gift-set", "surprise-box", "value-pack")

# ---------------------------------------------------------------------------
def _watch_products(cfg: Config, matcher: Matcher) -> list[dict]:
    products = []
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
    rules = [("premium-advent", 149.0), ("advent", 99.0), ("medium-gift-set", 39.9), ("small-gift-set", 24.9),
             ("surprise-box", 49.9), ("value-pack", 29.9), ("50ml", 5.9), ("bath-foam", 19.9),
             ("fragrance-sticks", 34.9), ("shower", 10.9)]
    return next((price for key, price in rules if key in slug), 19.9)


# ---------------------------------------------------------------------------
def generate(cfg: Config, *, days: int = 30, seed: int = 42, now: datetime | None = None) -> dict[str, list[dict]]:
    """Return {"rituals_catalog": [...], "rituals_stock": [...]}."""
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

    return dict(out)


def write_sample_data(cfg: Config, writer: LandingWriter, *, days: int = 30, seed: int = 42,
                      now: datetime | None = None) -> dict[str, int]:
    """Generate sample data and write one file per dataset per day. Returns record counts."""
    data = generate(cfg, days=days, seed=seed, now=now)
    counts: dict[str, int] = {}
    for key, records in data.items():
        time_field = {"rituals_stock": "scraped_at", "rituals_catalog": "crawled_at"}[key]
        by_day: dict[str, list[dict]] = defaultdict(list)
        for record in records:
            by_day[record[time_field][:10]].append(record)
        for day, day_records in sorted(by_day.items()):
            when = datetime.fromisoformat(day + "T23:59:00+00:00")
            writer.write(key, day_records, run_id=f"sample{day.replace('-', '')}", when=when)
        counts[key] = len(records)
    return counts
