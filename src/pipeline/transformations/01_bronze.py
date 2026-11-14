# Bronze layer: raw JSON Lines from the landing volume -> streaming tables.
#
# * Auto Loader picks up only new files on every pipeline update (incremental).
# * Each source has an explicit schema = the data contract with the collectors
#   (src/collectors/radar/records.py; tests/test_contracts.py keeps both in sync).
#   Values that don't fit the contract end up in _rescued_data instead of
#   silently disappearing.
# * We add lineage columns: which file a row came from and when it was ingested.
# * The "matches_contract" expectation only counts (doesn't drop) rows with
#   rescued data, so a collector change shows up in the pipeline's data quality tab.

from pyspark import pipelines as dp
from pyspark.sql import functions as F

LANDING_PATH = spark.conf.get("rituals_radar.landing_path")  # noqa: F821  (spark is provided by the pipeline)

STOCK_COLUMNS = (
    "snapshot_id STRING",
    "run_id STRING",
    "scraped_at TIMESTAMP",
    "product_id STRING",
    "product_name STRING",
    "url STRING",
    "collection STRING",
    "category_path STRING",
    "availability STRING",
    "is_available BOOLEAN",
    "price DOUBLE",
    "list_price DOUBLE",
    "currency STRING",
    "http_status INT",
    "parse_status STRING",
    "error_message STRING",
    "is_sample BOOLEAN",
)

CATALOG_COLUMNS = (
    "run_id STRING",
    "crawled_at TIMESTAMP",
    "product_id STRING",
    "url STRING",
    "slug STRING",
    "lastmod TIMESTAMP",
    "collection STRING",
    "is_sample BOOLEAN",
)
STORE_COLUMNS = (
    "run_id STRING",
    "crawled_at TIMESTAMP",
    "store_id STRING",
    "store_name STRING",
    "street_address STRING",
    "postal_code STRING",
    "city STRING",
    "country STRING",
    "latitude DOUBLE",
    "longitude DOUBLE",
    "phone STRING",
    "opening_hours STRING",
    "url STRING",
    "is_sample BOOLEAN",
)

PC4_COLUMNS = (
    "run_id STRING",
    "fetched_at TIMESTAMP",
    "pc4 STRING",
    "latitude DOUBLE",
    "longitude DOUBLE",
    "source_year INT",
    "is_sample BOOLEAN",
)


def read_landing(pattern: str, columns: tuple):
    return (
        spark.readStream.format("cloudFiles")  # noqa: F821
        .option("cloudFiles.format", "json")
        .option("rescuedDataColumn", "_rescued_data")
        .schema(", ".join(columns))
        .load(f"{LANDING_PATH}/{pattern}")
        .withColumn("source_file", F.col("_metadata.file_path"))
        .withColumn("ingested_at", F.current_timestamp())
    )


@dp.table(
    name="bronze_rituals_stock",
    comment="Raw stock checks of Rituals product pages, exactly as landed by the collector.",
)
@dp.expect("matches_contract", "_rescued_data IS NULL")
def bronze_rituals_stock():
    return read_landing("rituals_stock/*/*.jsonl", STOCK_COLUMNS)


@dp.table(
    name="bronze_rituals_catalog",
    comment="Raw daily snapshots of all product URLs in the rituals.com sitemap.",
)
@dp.expect("matches_contract", "_rescued_data IS NULL")
def bronze_rituals_catalog():
    return read_landing("rituals_catalog/*/*.jsonl", CATALOG_COLUMNS)


@dp.table(
    name="bronze_rituals_stores",
    comment="Raw weekly snapshots of all Rituals store pages (address, coordinates, opening hours).",
)
@dp.expect("matches_contract", "_rescued_data IS NULL")
def bronze_rituals_stores():
    return read_landing("rituals_stores/*/*.jsonl", STORE_COLUMNS)


@dp.table(
    name="bronze_reference_pc4",
    comment="Raw centre points of Dutch 4-digit postcode areas (source: CBS / PDOK, CC BY 4.0).",
)
@dp.expect("matches_contract", "_rescued_data IS NULL")
def bronze_reference_pc4():
    return read_landing("reference_pc4/*/*.jsonl", PC4_COLUMNS)
