-- ELT variant of the pipeline's validation: Pub/Sub writes every message raw into
-- bronze.reviews_raw_bqsub, and this view classifies each one with the same rules,
-- in the same order, as validate() in pipelines/dataflow/pipeline.py.
-- Rule parity is the weak point of this approach: e.g. CAST(... AS TIMESTAMP) accepts
-- naive timestamps as UTC, while the Python rule rejects them; the regex restores parity.
WITH parsed AS (
  SELECT message_id, publish_time AS ingest_ts, data, SAFE.PARSE_JSON(data) AS j
  FROM `${project}.bronze.reviews_raw_bqsub`
),
checks AS (
  SELECT
    *,
    ARRAY_TO_STRING(ARRAY(
      SELECT f
      FROM UNNEST(['review_id', 'product_id', 'customer_id', 'rating', 'title', 'body', 'channel', 'event_ts']) AS f WITH OFFSET o
      WHERE j[f] IS NULL OR JSON_TYPE(j[f]) = 'null' OR (JSON_TYPE(j[f]) = 'string' AND JSON_VALUE(j[f]) = '')
      ORDER BY o
    ), ',') AS missing
  FROM parsed
)
SELECT
  message_id,
  ingest_ts,
  data,
  CASE
    WHEN j IS NULL THEN 'malformed_json'
    WHEN JSON_TYPE(j) != 'object' THEN 'not_an_object'
    WHEN missing != '' THEN CONCAT('missing_fields:', missing)
    WHEN NOT REGEXP_CONTAINS(TO_JSON_STRING(j.rating), r'^[1-5]$') THEN 'invalid_rating'
    WHEN EXISTS (
      SELECT 1 FROM UNNEST(['review_id', 'product_id', 'customer_id', 'title', 'body', 'channel', 'event_ts']) AS f
      WHERE JSON_TYPE(j[f]) != 'string'
    ) THEN 'invalid_type'
    WHEN NOT REGEXP_CONTAINS(JSON_VALUE(j.event_ts), r'(Z|[+-]\d{2}:\d{2})$')
      OR SAFE_CAST(JSON_VALUE(j.event_ts) AS TIMESTAMP) IS NULL THEN 'invalid_event_ts'
    ELSE 'valid'
  END AS outcome
FROM checks
