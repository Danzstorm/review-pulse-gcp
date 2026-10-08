-- Daily metrics per product, rebuilt from reviews_enriched on every run (it is a pure
-- aggregation, so a full rebuild is simpler than incremental and cannot drift).
-- The agent's get_metrics tool reads this table, so it carries product_name: the agent
-- looks products up by name and never needs access to silver.
CREATE OR REPLACE TABLE `${project}.gold.product_daily_metrics`
PARTITION BY metric_date
CLUSTER BY product_id AS
SELECT
  DATE(e.event_ts) AS metric_date,
  e.product_id,
  ANY_VALUE(p.name) AS product_name,
  COUNT(*) AS n_reviews,
  ROUND(AVG(e.rating), 2) AS avg_rating,
  COUNTIF(e.sentiment = 'negative') AS n_negative,
  ROUND(SAFE_DIVIDE(COUNTIF(e.sentiment = 'negative'), COUNT(*)), 3) AS share_negative,
  -- the topic that concentrates the most negative reviews that day
  APPROX_TOP_COUNT(IF(e.sentiment = 'negative', e.topic, NULL), 1)[SAFE_OFFSET(0)].value AS top_negative_topic
FROM `${project}.gold.reviews_enriched` AS e
LEFT JOIN `${project}.silver.products` AS p USING (product_id)
GROUP BY metric_date, product_id;
