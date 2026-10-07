-- Silver layer: one row per review_id. Bronze is at-least-once (producer retries and
-- Pub/Sub redeliveries share a review_id), so deduplication happens here, once.
-- Insert-only MERGE: running it again, or over an overlapping window, changes nothing.
-- ${lookback_days} bounds the bronze scan (bronze requires a partition filter).
CREATE TABLE IF NOT EXISTS `${project}.silver.reviews` (
  review_id STRING NOT NULL,
  product_id STRING NOT NULL,
  customer_id STRING NOT NULL,
  rating INT64 NOT NULL,
  title STRING NOT NULL,
  body STRING NOT NULL,
  channel STRING NOT NULL,
  event_ts TIMESTAMP NOT NULL OPTIONS (description = 'When the review was written. Partition column.'),
  first_ingest_ts TIMESTAMP NOT NULL OPTIONS (description = 'ingest_ts of the first copy that arrived.'),
  loaded_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(event_ts)
CLUSTER BY product_id;

MERGE `${project}.silver.reviews` AS t
USING (
  SELECT * EXCEPT (rn)
  FROM (
    SELECT
      review_id, product_id, customer_id, rating, title, body, channel, event_ts,
      ingest_ts AS first_ingest_ts,
      ROW_NUMBER() OVER (PARTITION BY review_id ORDER BY ingest_ts, message_id) AS rn
    FROM `${project}.bronze.reviews_raw`
    WHERE ingest_ts >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL ${lookback_days} DAY)
  )
  WHERE rn = 1
) AS s
-- event_ts is part of the key so BigQuery can prune silver partitions; copies of one
-- review always carry the same event_ts.
ON t.review_id = s.review_id AND t.event_ts = s.event_ts
WHEN NOT MATCHED THEN
  INSERT (review_id, product_id, customer_id, rating, title, body, channel, event_ts, first_ingest_ts, loaded_at)
  VALUES (s.review_id, s.product_id, s.customer_id, s.rating, s.title, s.body, s.channel, s.event_ts, s.first_ingest_ts, CURRENT_TIMESTAMP());
