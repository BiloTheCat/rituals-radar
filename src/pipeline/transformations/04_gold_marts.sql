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
-- Social
-- ---------------------------------------------------------------------------
CREATE OR REFRESH MATERIALIZED VIEW gold_social_posts
COMMENT "One row per post/video/comment with sentiment and the latest known engagement."
AS WITH posts AS (
  SELECT *
  FROM silver_social_sentiment
  QUALIFY ROW_NUMBER() OVER (PARTITION BY record_id ORDER BY collected_at) = 1
)
SELECT
  p.record_id,
  p.source,
  p.content_type,
  p.url,
  p.text,
  p.lang,
  p.created_at,
  p.post_date,
  p.matched_collections,
  p.sentiment,
  p.sentiment_score,
  p.sentiment_engine,
  COALESCE(e.like_count, p.like_count, 0) AS like_count,
  COALESCE(e.reply_count, p.reply_count, 0) AS reply_count,
  COALESCE(e.share_count, p.share_count, 0) AS share_count,
  COALESCE(e.view_count, p.view_count) AS view_count,
  COALESCE(e.like_count, p.like_count, 0)
    + COALESCE(e.reply_count, p.reply_count, 0)
    + COALESCE(e.share_count, p.share_count, 0) AS engagement,
  p.author_hash,
  p.is_sample
FROM posts p
LEFT JOIN silver_social_engagement_latest e ON e.record_id = p.record_id;


CREATE OR REFRESH MATERIALIZED VIEW gold_collection_buzz_daily
COMMENT "Per collection, day and source: mentions, engagement and sentiment (a post can mention several collections)."
AS WITH mentions AS (
  SELECT post_date, source, sentiment, engagement, explode(matched_collections) AS collection
  FROM gold_social_posts
)
SELECT
  post_date,
  collection,
  source,
  COUNT(*) AS mentions,
  SUM(engagement) AS engagement,
  SUM(CASE WHEN sentiment = 'positive' THEN 1 ELSE 0 END) AS positive,
  SUM(CASE WHEN sentiment = 'negative' THEN 1 ELSE 0 END) AS negative,
  SUM(CASE WHEN sentiment = 'neutral' THEN 1 ELSE 0 END) AS neutral,
  SUM(CASE WHEN sentiment = 'mixed' THEN 1 ELSE 0 END) AS mixed,
  ROUND(
    (SUM(CASE WHEN sentiment = 'positive' THEN 1 ELSE 0 END) - SUM(CASE WHEN sentiment = 'negative' THEN 1 ELSE 0 END))
    / NULLIF(SUM(CASE WHEN sentiment <> 'unknown' THEN 1 ELSE 0 END), 0), 3) AS net_sentiment
FROM mentions
GROUP BY post_date, collection, source;


-- ---------------------------------------------------------------------------
-- Scorecards: stock and buzz side by side (last 30 days)
-- ---------------------------------------------------------------------------
CREATE OR REFRESH MATERIALIZED VIEW gold_collection_scorecard
COMMENT "Per collection, last 30 days: availability, stock-outs, mentions, net sentiment, share of voice."
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
  SELECT collection, COUNT(*) AS stockouts_30d, ROUND(AVG(duration_hours), 1) AS avg_stockout_hours_30d
  FROM gold_stockout_events
  WHERE stockout_start >= date_sub(current_date(), 30)
  GROUP BY collection
),
buzz AS (
  SELECT
    collection,
    SUM(mentions) AS mentions_30d,
    SUM(engagement) AS engagement_30d,
    ROUND((SUM(positive) - SUM(negative)) / NULLIF(SUM(positive + negative + neutral + mixed), 0), 3)
      AS net_sentiment_30d
  FROM gold_collection_buzz_daily
  WHERE post_date >= date_sub(current_date(), 30)
  GROUP BY collection
),
combined AS (
  SELECT
    COALESCE(s.collection, b.collection) AS collection,
    s.products_tracked,
    s.availability_rate_30d,
    COALESCE(b.mentions_30d, 0) AS mentions_30d,
    COALESCE(b.engagement_30d, 0) AS engagement_30d,
    b.net_sentiment_30d
  FROM stock s
  FULL OUTER JOIN buzz b ON b.collection = s.collection
)
SELECT
  c.collection,
  c.products_tracked,
  c.availability_rate_30d,
  COALESCE(o.stockouts_30d, 0) AS stockouts_30d,
  o.avg_stockout_hours_30d,
  c.mentions_30d,
  c.engagement_30d,
  c.net_sentiment_30d,
  ROUND(c.mentions_30d / NULLIF(SUM(c.mentions_30d) OVER (), 0), 4) AS share_of_voice,
  DENSE_RANK() OVER (ORDER BY c.mentions_30d DESC, c.engagement_30d DESC) AS buzz_rank
FROM combined c
LEFT JOIN stockouts o ON o.collection = c.collection;


CREATE OR REFRESH MATERIALIZED VIEW gold_product_scorecard
COMMENT "Per product: current status, availability 7/30 days, stock-outs, and the buzz around its collection."
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
  b.mentions_30d AS collection_mentions_30d,
  b.net_sentiment_30d AS collection_net_sentiment_30d,
  c.url
FROM gold_stock_current c
LEFT JOIN daily d ON d.product_id = c.product_id
LEFT JOIN stockouts o ON o.product_id = c.product_id
LEFT JOIN gold_collection_scorecard b ON b.collection = c.collection;
