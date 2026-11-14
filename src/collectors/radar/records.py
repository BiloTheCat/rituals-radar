"""Data contracts for the landing zone.

Every collector writes JSON Lines whose records contain exactly these fields.
The Databricks pipeline reads the files with an explicit schema
(src/pipeline/transformations/01_bronze.py). tests/test_contracts.py fails the
build when the two sides drift apart, so a renamed field can never silently
end up as NULL in Databricks.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

# One row per product page check (every few hours).
STOCK_FIELDS: tuple[str, ...] = (
    "snapshot_id",      # product_id + scrape timestamp, unique per check
    "run_id",
    "scraped_at",       # ISO-8601 UTC
    "product_id",       # 7-digit Rituals article number (also the schema.org sku)
    "product_name",
    "url",
    "collection",       # e.g. "sakura", derived from the product name/slug
    "category_path",    # breadcrumb, e.g. "Body > Bad & Douche > Douchegels"
    "availability",     # normalised schema.org value: InStock, OutOfStock, ... / Delisted / Unknown
    "is_available",     # True when the product can be ordered online right now
    "price",            # offer price from schema.org JSON-LD
    "list_price",       # regular price shown on the page (data-testid="original-price")
    "currency",
    "http_status",
    "parse_status",     # ok | not_found | no_jsonld | unknown_availability | error | blocked_by_robots
    "error_message",
    "is_sample",
)

# One row per product URL found in the sitemap (daily).
CATALOG_FIELDS: tuple[str, ...] = (
    "run_id",
    "crawled_at",
    "product_id",
    "url",
    "slug",
    "lastmod",
    "collection",
    "is_sample",
)

# One row per Rituals store page (weekly).
STORE_FIELDS: tuple[str, ...] = (
    "run_id",
    "crawled_at",
    "store_id",         # number at the end of the store URL, e.g. 2506
    "store_name",
    "street_address",
    "postal_code",
    "city",
    "country",
    "latitude",
    "longitude",
    "phone",
    "opening_hours",    # e.g. "ma 11:00-18:00 · di 10:00-18:00 · ..."
    "url",
    "is_sample",
)

# One row per 4-digit postcode area with its centre point (CBS/PDOK, monthly).
PC4_FIELDS: tuple[str, ...] = (
    "run_id",
    "fetched_at",
    "pc4",              # "1012"
    "latitude",
    "longitude",
    "source_year",      # CBS year of the postcode boundaries
    "is_sample",
)

DATASET_FIELDS: Mapping[str, tuple[str, ...]] = {
    "rituals_stock": STOCK_FIELDS,
    "rituals_catalog": CATALOG_FIELDS,
    "rituals_stores": STORE_FIELDS,
    "reference_pc4": PC4_FIELDS,
}


def conform(record: Mapping[str, object], fields: Iterable[str]) -> dict[str, object]:
    """Return a copy of `record` with exactly `fields` (missing keys become None).

    Unknown keys raise, so typos are caught in tests instead of in Databricks.
    """
    fields = tuple(fields)
    unknown = set(record) - set(fields)
    if unknown:
        raise ValueError(f"Unknown field(s) for contract: {sorted(unknown)}")
    return {name: record.get(name) for name in fields}
