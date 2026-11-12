# Gold: stock & price history as a Slowly Changing Dimension type 2.
#
# The collector checks every product every few hours, but availability rarely
# changes. AUTO CDC turns that stream of snapshots into a compact history: a new
# version is only created when availability or price changes, and the
# __START_AT / __END_AT columns tell exactly how long each state lasted.
# Late or out-of-order files are handled by sequencing on scraped_at.

from pyspark import pipelines as dp
from pyspark.sql import functions as F

dp.create_streaming_table(
    name="gold_stock_history",
    comment=(
        "Stock and price history per product (SCD type 2). One row per state; "
        "__END_AT IS NULL marks the current state. scraped_at = last check in that state."
    ),
)

dp.create_auto_cdc_flow(
    target="gold_stock_history",
    source="silver_stock_snapshots",
    keys=["product_id"],
    sequence_by=F.col("scraped_at"),
    stored_as_scd_type=2,
    # Only these columns create a new version; others (name, last check time) are updated in place.
    track_history_column_list=["availability", "is_available", "price", "list_price"],
    except_column_list=[
        "snapshot_id",
        "run_id",
        "source_file",
        "ingested_at",
        "http_status",
        "parse_status",
        "error_message",
        "snapshot_date",
    ],
)
