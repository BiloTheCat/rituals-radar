-- Gold layer: business-ready tables for the dashboard (materialized views).
-- Every update of the pipeline refreshes these from the silver tables and the
-- SCD2 history. Dates are in Europe/Amsterdam (see the *_date columns in silver).

-- ---------------------------------------------------------------------------
-- Stock
-- ---------------------------------------------------------------------------
CREATE OR REFRESH MATERIALIZED VIEW gold_stock_current
COMMENT "Current stock status per product (the open version in gold_stock_history)."
AS SELECT
  product_id,
  product_name,
  collection,
  category_path,
  url,
  availability,
  is_available,
  price,
  list_price,
  discount_pct,
  currency,
  __START_AT AS status_since,
  scraped_at AS last_checked_at,
  ROUND((unix_timestamp(current_timestamp()) - unix_timestamp(__START_AT)) / 3600.0, 1) AS hours_in_status,
  is_sample
FROM gold_stock_history
WHERE __END_AT IS NULL;


CREATE OR REFRESH MATERIALIZED VIEW gold_stock_daily
COMMENT "Per product per day: share of checks in which the product could be ordered online."
AS SELECT
  snapshot_date,
  product_id,
  max_by(product_name, scraped_at) AS product_name,
  max_by(collection, scraped_at) AS collection,
  COUNT(*) AS checks,
  SUM(CASE WHEN is_available THEN 1 ELSE 0 END) AS checks_available,
  ROUND(AVG(CASE WHEN is_available THEN 1.0 ELSE 0.0 END), 4) AS availability_rate,
  MIN(price) AS min_price,
  MAX(price) AS max_price
FROM silver_stock_snapshots
GROUP BY snapshot_date, product_id;


CREATE OR REFRESH MATERIALIZED VIEW gold_collection_availability_daily
COMMENT "Per collection per day: share of product checks that were in stock."
AS SELECT
  snapshot_date,
  collection,
  COUNT(DISTINCT product_id) AS products,
  ROUND(AVG(CASE WHEN is_available THEN 1.0 ELSE 0.0 END), 4) AS availability_rate
FROM silver_stock_snapshots
GROUP BY snapshot_date, collection;


-- One row per stock-out period. Consecutive SCD2 versions that are all
-- "not available" (e.g. the price changed while sold out) are merged with a
-- classic gaps-and-islands query.
CREATE OR REFRESH MATERIALIZED VIEW gold_stockout_events
COMMENT "Stock-out periods per product with start, end and duration (resolution = check interval)."
AS WITH versions AS (
  SELECT
    product_id,
    product_name,
    collection,
    is_available,
    __START_AT AS valid_from,
    __END_AT AS valid_to,
    CASE WHEN LAG(is_available) OVER (PARTITION BY product_id ORDER BY __START_AT) <=> is_available
         THEN 0 ELSE 1 END AS starts_new_period
  FROM gold_stock_history
),
periods AS (
  SELECT
    *,
    SUM(starts_new_period) OVER (
      PARTITION BY product_id ORDER BY valid_from ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS period_id
  FROM versions
),
stockouts AS (
  SELECT
    product_id,
    max_by(product_name, valid_from) AS product_name,
    max_by(collection, valid_from) AS collection,
    MIN(valid_from) AS stockout_start,
    CASE WHEN COUNT(*) = COUNT(valid_to) THEN MAX(valid_to) END AS stockout_end
  FROM periods
  WHERE NOT is_available
  GROUP BY product_id, period_id
)
SELECT
  product_id,
  product_name,
  collection,
  stockout_start,
  stockout_end,
  stockout_end IS NULL AS is_ongoing,
  ROUND((unix_timestamp(COALESCE(stockout_end, current_timestamp())) - unix_timestamp(stockout_start)) / 3600.0, 1)
    AS duration_hours
FROM stockouts;


-- ---------------------------------------------------------------------------
-- Catalog
-- ---------------------------------------------------------------------------
CREATE OR REFRESH MATERIALIZED VIEW gold_catalog_products
COMMENT "Every product seen in the sitemap: first/last seen, still listed, new since tracking started."
AS WITH crawls AS (
  SELECT MIN(crawled_at) AS first_crawl, MAX(crawled_at) AS last_crawl
  FROM silver_catalog_snapshots
),
products AS (
  SELECT
    product_id,
    max_by(url, crawled_at) AS url,
    max_by(slug, crawled_at) AS slug,
    max_by(collection, crawled_at) AS collection,
    MIN(crawled_at) AS first_seen_at,
    MAX(crawled_at) AS last_seen_at,
    max_by(lastmod, crawled_at) AS lastmod
  FROM silver_catalog_snapshots
  GROUP BY product_id
)
SELECT
  p.*,
  p.last_seen_at = c.last_crawl AS is_listed,
  p.first_seen_at > c.first_crawl + INTERVAL 1 DAY AS is_new_launch
FROM products p
CROSS JOIN crawls c;


-- ---------------------------------------------------------------------------
-- Scorecards (last 30 days)
-- ---------------------------------------------------------------------------
CREATE OR REFRESH MATERIALIZED VIEW gold_collection_scorecard
COMMENT "Per collection, last 30 days: products tracked, availability, stock-outs and hours sold out."
AS WITH stock AS (
  SELECT
    collection,
    COUNT(DISTINCT product_id) AS products_tracked,
    ROUND(AVG(CASE WHEN is_available THEN 1.0 ELSE 0.0 END), 4) AS availability_rate_30d
  FROM silver_stock_snapshots
  WHERE snapshot_date >= date_sub(current_date(), 30)
  GROUP BY collection
),
stockouts AS (
  SELECT
    collection,
    COUNT(*) AS stockouts_30d,
    ROUND(AVG(duration_hours), 1) AS avg_stockout_hours_30d,
    ROUND(SUM(duration_hours), 1) AS hours_sold_out_30d
  FROM gold_stockout_events
  WHERE stockout_start >= date_sub(current_date(), 30)
  GROUP BY collection
)
SELECT
  s.collection,
  s.products_tracked,
  s.availability_rate_30d,
  COALESCE(o.stockouts_30d, 0) AS stockouts_30d,
  o.avg_stockout_hours_30d,
  COALESCE(o.hours_sold_out_30d, 0) AS hours_sold_out_30d,
  DENSE_RANK() OVER (ORDER BY s.availability_rate_30d ASC) AS scarcity_rank
FROM stock s
LEFT JOIN stockouts o ON o.collection = s.collection;


CREATE OR REFRESH MATERIALIZED VIEW gold_product_scorecard
COMMENT "Per product: current status, price, availability 7/30 days and stock-outs."
AS WITH daily AS (
  SELECT
    product_id,
    ROUND(AVG(CASE WHEN snapshot_date >= date_sub(current_date(), 7) THEN availability_rate END), 4)
      AS availability_rate_7d,
    ROUND(AVG(CASE WHEN snapshot_date >= date_sub(current_date(), 30) THEN availability_rate END), 4)
      AS availability_rate_30d
  FROM gold_stock_daily
  GROUP BY product_id
),
stockouts AS (
  SELECT product_id, COUNT(*) AS stockouts_30d, ROUND(SUM(duration_hours), 1) AS hours_out_of_stock_30d
  FROM gold_stockout_events
  WHERE stockout_start >= date_sub(current_date(), 30)
  GROUP BY product_id
)
SELECT
  c.product_id,
  c.product_name,
  c.collection,
  c.availability AS current_availability,
  c.is_available,
  c.status_since,
  c.hours_in_status,
  c.price,
  c.list_price,
  c.discount_pct,
  d.availability_rate_7d,
  d.availability_rate_30d,
  COALESCE(o.stockouts_30d, 0) AS stockouts_30d,
  COALESCE(o.hours_out_of_stock_30d, 0) AS hours_out_of_stock_30d,
  c.url
FROM gold_stock_current c
LEFT JOIN daily d ON d.product_id = c.product_id
LEFT JOIN stockouts o ON o.product_id = c.product_id;


-- ---------------------------------------------------------------------------
-- Stores near a postcode
-- ---------------------------------------------------------------------------
CREATE OR REFRESH MATERIALIZED VIEW gold_stores
COMMENT "Every Rituals store with its latest address, coordinates and opening hours."
AS WITH latest_crawl AS (
  SELECT MAX(crawled_at) AS last_crawl FROM silver_store_snapshots
)
SELECT
  s.store_id,
  max_by(s.store_name, s.crawled_at) AS store_name,
  max_by(s.street_address, s.crawled_at) AS street_address,
  max_by(s.postal_code, s.crawled_at) AS postal_code,
  max_by(s.city, s.crawled_at) AS city,
  max_by(s.latitude, s.crawled_at) AS latitude,
  max_by(s.longitude, s.crawled_at) AS longitude,
  max_by(s.phone, s.crawled_at) AS phone,
  max_by(s.opening_hours, s.crawled_at) AS opening_hours,
  max_by(s.url, s.crawled_at) AS url,
  MAX(s.crawled_at) AS last_seen_at,
  MAX(s.crawled_at) = MAX(l.last_crawl) AS is_listed,
  max_by(s.is_sample, s.crawled_at) AS is_sample
FROM silver_store_snapshots s
CROSS JOIN latest_crawl l
GROUP BY s.store_id;


CREATE OR REFRESH MATERIALIZED VIEW gold_postcode4
COMMENT "Centre point per 4-digit postcode (latest download). Source: CBS / PDOK, CC BY 4.0."
AS SELECT
  pc4,
  max_by(latitude, fetched_at) AS latitude,
  max_by(longitude, fetched_at) AS longitude,
  max_by(source_year, fetched_at) AS source_year
FROM silver_pc4_snapshots
GROUP BY pc4;


-- The 10 nearest stores for every 4-digit postcode, as the crow flies
-- (haversine formula, earth radius 6371 km). Precomputed, so the dashboard
-- only has to filter on a postcode.
CREATE OR REFRESH MATERIALIZED VIEW gold_store_distance
COMMENT "Per 4-digit postcode: the 10 nearest Rituals stores with straight-line distance in km."
AS WITH pairs AS (
  SELECT
    p.pc4,
    s.store_id,
    s.store_name,
    s.street_address,
    s.postal_code,
    s.city,
    s.opening_hours,
    s.url,
    2 * 6371 * asin(sqrt(
      pow(sin(radians(s.latitude - p.latitude) / 2), 2)
      + cos(radians(p.latitude)) * cos(radians(s.latitude)) * pow(sin(radians(s.longitude - p.longitude) / 2), 2)
    )) AS distance_km
  FROM gold_postcode4 p
  CROSS JOIN gold_stores s
  WHERE s.is_listed
)
SELECT
  pc4,
  ROW_NUMBER() OVER (PARTITION BY pc4 ORDER BY distance_km) AS nearest_rank,
  store_id,
  store_name,
  street_address,
  postal_code,
  city,
  ROUND(distance_km, 1) AS distance_km,
  opening_hours,
  url
FROM pairs
QUALIFY ROW_NUMBER() OVER (PARTITION BY pc4 ORDER BY distance_km) <= 10;
