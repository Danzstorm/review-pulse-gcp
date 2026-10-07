-- Daily metrics per product, rebuilt from reviews_enriched on every run (it is a pure
-- aggregation, so a full rebuild is simpler than incremental and cannot drift).
-- The agent's get_metrics tool reads this table.
CREATE OR REPLACE TABLE `${project}.gold.product_daily_metrics`
PARTITION BY metric_date
CLUSTER BY product_id AS
SELECT
  DATE(event_ts) AS metric_date,
  product_id,
  COUNT(*) AS n_reviews,
  ROUND(AVG(rating), 2) AS avg_rating,
  COUNTIF(sentiment = 'negative') AS n_negative,
  ROUND(SAFE_DIVIDE(COUNTIF(sentiment = 'negative'), COUNT(*)), 3) AS share_negative,
  -- the topic that concentrates the most negative reviews that day
  APPROX_TOP_COUNT(IF(sentiment = 'negative', topic, NULL), 1)[SAFE_OFFSET(0)].value AS top_negative_topic
FROM `${project}.gold.reviews_enriched`
GROUP BY metric_date, product_id;
