"""Generate realistic *sample* data so the pipeline and dashboard work on day one.

All records carry is_sample = true. The pipeline keeps or drops them via the
`include_sample_data` setting, so switching to real data is a config change.

The simulation is deterministic (seeded) and has a few deliberate patterns, so
the dashboard has something to show:
  * seasonal products (advent calendars, gift sets) sell out more often, and
    more so as the season approaches;
  * a stock-out in a collection triggers extra negative "sold out again?!" posts;
  * Sakura runs a 20% promotion for a few days;
  * two new products appear in the catalog halfway through the period.
Texts are invented for this project; they are not real posts.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone

from radar.config import Config
from radar.landing import LandingWriter
from radar.matching import GENERAL, Matcher, matcher_from_config
from radar.records import CATALOG_FIELDS, STOCK_FIELDS, conform
from radar.rituals import PRODUCT_ID_RE, PRODUCT_URL_RE, slug_to_name
from radar.social import SocialContext, build_post
from radar.util import iso, utc_now

STOCK_HOURS = (0, 3, 6, 9, 12, 15, 18, 21)  # UTC check times, like the GitHub schedule
SEASONAL_HINTS = ("advent", "gift-set", "surprise-box", "value-pack")

TEMPLATES = {
    "positive": [
        ("nl", "Net {name} gekocht en wauw, wat een heerlijke geur 😍"),
        ("nl", "{name} blijft mijn favoriet. De hele badkamer ruikt er dagen naar!"),
        ("nl", "Cadeautje gekregen: {name}. Echt superfijn, ik ben er zo blij mee ✨"),
        ("nl", "Ik word elke ochtend vrolijk van {name}. Aanrader!"),
        ("en", "Obsessed with {name}, it smells absolutely amazing"),
        ("en", "{name} was the perfect present, she loved it!"),
        ("en", "{name} is pure self-care in a bottle 🥰"),
    ],
    "negative": [
        ("nl", "Vind {name} echt veel te duur geworden voor wat je krijgt."),
        ("nl", "Teleurgesteld in {name}, de geur is na een uur al weg."),
        ("nl", "Pakket met {name} kwam lekkend binnen. Jammer."),
        ("en", "Not a fan of the new {name} scent, way too strong for me"),
        ("en", "{name} irritated my skin, really disappointed"),
    ],
    "stockout": [
        ("nl", "Alweer uitverkocht?! Ik wil gewoon {name} bestellen 😤"),
        ("nl", "Drie keer gecheckt deze week, {name} is nog steeds niet leverbaar. Frustrerend."),
        ("en", "Why is {name} always sold out online? So annoying"),
    ],
    "neutral": [
        ("nl", "Iemand ervaring met {name}? Twijfel nog."),
        ("nl", "{name} ligt vanaf deze week in de winkel bij mij in de buurt."),
        ("en", "Does anyone know if {name} comes in a travel size?"),
        ("en", "Comparing {name} with the older version in this video."),
    ],
    "mixed": [
        ("nl", "De geur van {name} is top, maar de prijs vind ik eerlijk gezegd te hoog."),
        ("en", "Love the smell of {name} but the packaging feels cheap now."),
    ],
}

# Relative share of voice per collection key (unknown keys get 0.05).
POPULARITY = {
    "sakura": 1.0, "advent": 0.8, "karma": 0.6, "jing": 0.4, "yozakura": 0.35, "ayurveda": 0.35,
    "hammam": 0.3, "homme": 0.3, "mehr": 0.25, "amsterdam_collection": 0.2, "oudh": 0.15, GENERAL: 0.4,
}
# Baseline sentiment mix per collection: (positive, negative, neutral, mixed)
SENTIMENT_MIX = {"sakura": (0.62, 0.12, 0.18, 0.08), "karma": (0.55, 0.15, 0.22, 0.08),
                 "advent": (0.45, 0.25, 0.2, 0.1)}
DEFAULT_MIX = (0.5, 0.18, 0.24, 0.08)
SOURCES = (("youtube", 0.45), ("bluesky", 0.25), ("x", 0.15), ("tiktok", 0.15))


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


def _display_name(cfg: Config, key: str, lang: str) -> str:
    if key == GENERAL:
        return "de nieuwe Rituals-collectie" if lang == "nl" else "the new Rituals collection"
    if key == "advent":
        return "de Rituals adventskalender" if lang == "nl" else "the Rituals advent calendar"
    name = cfg.collections[key].display_name if key in cfg.collections else key.replace("_", " ").title()
    lowered = name.lower()
    return name if ("rituals" in lowered or "the ritual of" in lowered) else f"Rituals {name}"


def _pick(rng: random.Random, weighted) -> str:
    keys, weights = zip(*weighted)
    return rng.choices(keys, weights=weights, k=1)[0]


# ---------------------------------------------------------------------------
def generate(cfg: Config, *, days: int = 30, seed: int = 42, now: datetime | None = None) -> dict[str, list[dict]]:
    """Return {"rituals_catalog": [...], "rituals_stock": [...], "social:<source>": [...]}."""
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
    oos_days: dict[str, set] = defaultdict(set)  # collection -> days with a stock-out
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
                if not in_stock:
                    oos_days[p["collection"]].add(d)
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

    # -- social: share of voice per collection, sentiment mix, stock-out effect -
    collections = [k for k in POPULARITY if k == GENERAL or k in cfg.collections]
    weights = [(k, POPULARITY[k]) for k in collections]
    counter = 0
    for d in range(days):
        day = start_day + timedelta(days=d)
        collected = datetime.combine(day, time(23, 45), tzinfo=timezone.utc)
        if datetime.combine(day, time(0, 0), tzinfo=timezone.utc) > now:
            break
        collected = min(collected, now)
        ctx = SocialContext(cfg=cfg, matcher=matcher, now=collected, run_id=f"sample-{day:%Y%m%d}", is_sample=True)
        volume = max(4, int(rng.gauss(14, 3) * (1 + 0.5 * math.sin(d / 3))))
        planned = [_pick(rng, weights) for _ in range(volume)]
        for key, days_out in oos_days.items():
            if d in days_out and key in collections:
                planned += [f"{key}|stockout"] * rng.randint(1, 3)
        videos_today: dict[str, list[dict]] = defaultdict(list)
        for item in planned:
            key, _, forced = item.partition("|")
            mix = SENTIMENT_MIX.get(key, DEFAULT_MIX)
            mood = forced or _pick(rng, list(zip(("positive", "negative", "neutral", "mixed"), mix)))
            lang, template = rng.choice(TEMPLATES[mood])
            text = template.format(name=_display_name(cfg, key, lang))
            source = _pick(rng, SOURCES)
            counter += 1
            created = datetime.combine(day, time(rng.randint(6, 23), rng.randint(0, 59)), tzinfo=timezone.utc)
            created = min(created, collected)
            likes = int(rng.lognormvariate(1.6, 1.1))
            if source == "youtube" and videos_today[key] and rng.random() < 0.7:
                video = rng.choice(videos_today[key])
                record = build_post(ctx, source="youtube", id_kind="comment", content_type="comment",
                                    native_id=f"sample-{counter}", text=text, parent_id=video["native_id"],
                                    url=f"{video['url']}&lc=sample-{counter}", author=f"sample-user-{rng.randint(1, 400)}",
                                    created_at=created, like_count=likes, reply_count=rng.randint(0, 3),
                                    search_query=video["search_query"], inherited=video["matched_collections"])
            elif source == "youtube":
                is_short = rng.random() < 0.4
                native = f"sample-{counter}"
                record = build_post(ctx, source="youtube", id_kind="video", content_type="short" if is_short else "video",
                                    native_id=native, text=text,
                                    url=f"https://www.youtube.com/watch?v={native}", author=f"sample-channel-{rng.randint(1, 60)}",
                                    lang=lang, created_at=created, like_count=likes * 20,
                                    reply_count=rng.randint(0, 40), view_count=int(rng.lognormvariate(7.5, 1.3)),
                                    search_query="rituals" if key == GENERAL else f"rituals {key}")
                videos_today[key].append(record)
            else:
                record = build_post(ctx, source=source, id_kind="video" if source == "tiktok" else "post",
                                    content_type="short" if source == "tiktok" else "post",
                                    native_id=f"sample-{counter}", text=text, author=f"sample-user-{rng.randint(1, 400)}",
                                    lang=lang, created_at=created,
                                    like_count=None if source == "tiktok" else likes,
                                    reply_count=None if source == "tiktok" else rng.randint(0, 5),
                                    share_count=None if source == "tiktok" else rng.randint(0, 4),
                                    search_query="manual link" if source == "tiktok"
                                    else ("rituals" if key == GENERAL else f"rituals {key}"))
            out[f"social:{source}"].append(record)
    return dict(out)


def write_sample_data(cfg: Config, writer: LandingWriter, *, days: int = 30, seed: int = 42,
                      now: datetime | None = None) -> dict[str, int]:
    """Generate sample data and write one file per dataset per day. Returns record counts."""
    data = generate(cfg, days=days, seed=seed, now=now)
    counts: dict[str, int] = {}
    for key, records in data.items():
        dataset, _, source = key.partition(":")
        time_field = {"rituals_stock": "scraped_at", "rituals_catalog": "crawled_at"}.get(dataset, "collected_at")
        by_day: dict[str, list[dict]] = defaultdict(list)
        for record in records:
            by_day[record[time_field][:10]].append(record)
        for day, day_records in sorted(by_day.items()):
            when = datetime.fromisoformat(day + "T23:59:00+00:00")
            writer.write(dataset, day_records, run_id=f"sample{day.replace('-', '')}", when=when,
                         subfolder=source or None)
        counts[key] = len(records)
    return counts
