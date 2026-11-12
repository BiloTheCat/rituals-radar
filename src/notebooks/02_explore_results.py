# Databricks notebook source
# MAGIC %md
# MAGIC # 02 · Explore the results
# MAGIC
# MAGIC A few questions you can answer with the gold tables. Run cell by cell after the pipeline
# MAGIC has finished at least once.

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Which products are sold out right now, and since when?
# MAGIC SELECT product_name, collection, availability, status_since, hours_in_status, price
# MAGIC FROM workspace.rituals_radar.gold_stock_current
# MAGIC WHERE NOT is_available
# MAGIC ORDER BY hours_in_status DESC

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Full history of one product: every change in availability or price (SCD type 2)
# MAGIC SELECT product_name, availability, price, list_price, __START_AT AS valid_from, __END_AT AS valid_to
# MAGIC FROM workspace.rituals_radar.gold_stock_history
# MAGIC WHERE product_id = '1121936'  -- Premium Advent Calendar 2026
# MAGIC ORDER BY __START_AT

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Do collections with more buzz sell out more often?
# MAGIC SELECT collection, mentions_30d, share_of_voice, net_sentiment_30d,
# MAGIC        availability_rate_30d, stockouts_30d, avg_stockout_hours_30d
# MAGIC FROM workspace.rituals_radar.gold_collection_scorecard
# MAGIC ORDER BY buzz_rank

# COMMAND ----------

# MAGIC %sql
# MAGIC -- What do people complain about? Latest negative posts per collection.
# MAGIC SELECT post_date, source, matched_collections, sentiment, text, url
# MAGIC FROM workspace.rituals_radar.gold_social_posts
# MAGIC WHERE sentiment IN ('negative', 'mixed')
# MAGIC ORDER BY created_at DESC
# MAGIC LIMIT 25

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Data quality: what did the pipeline drop or flag? (expectations in the event log)
# MAGIC SELECT
# MAGIC   timestamp,
# MAGIC   details:flow_progress.data_quality.expectations AS expectations
# MAGIC FROM event_log(TABLE(workspace.rituals_radar.silver_stock_snapshots))
# MAGIC WHERE event_type = 'flow_progress' AND details:flow_progress.data_quality IS NOT NULL
# MAGIC ORDER BY timestamp DESC
# MAGIC LIMIT 20

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Failed stock checks (blocked requests, HTTP errors, page layout changes)
# MAGIC SELECT scraped_at, product_id, http_status, parse_status, error_message
# MAGIC FROM workspace.rituals_radar.silver_stock_quarantine
# MAGIC ORDER BY scraped_at DESC
# MAGIC LIMIT 50

# COMMAND ----------

# MAGIC %sql
# MAGIC -- New products that appeared in the sitemap since tracking started
# MAGIC SELECT product_id, slug, collection, first_seen_at, is_listed
# MAGIC FROM workspace.rituals_radar.gold_catalog_products
# MAGIC WHERE is_new_launch
# MAGIC ORDER BY first_seen_at DESC
