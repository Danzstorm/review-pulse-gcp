-- Micro-batch enrichment: classify each silver review once with Gemini and keep the
-- result in gold.reviews_enriched. Only reviews not yet enriched are sent to the model,
-- so re-running costs nothing for old rows. ${enrich_limit} caps one run's model calls.
-- Failed or out-of-list answers are not stored, so the next run retries them. The topic
-- comes from a closed list: free text drifts ("battery" vs "battery life") and breaks
-- GROUP BY. What made the model respect the list: instruction AFTER the review text,
-- "MUST ... nothing else", temperature 0 and a token cap (the first prompt got 15 % in
-- list and some runaway repeated text). The WHERE below is the backstop, not the fix.
CREATE TABLE IF NOT EXISTS `${project}.gold.reviews_enriched` (
  review_id STRING NOT NULL,
  product_id STRING NOT NULL,
  rating INT64 NOT NULL,
  event_ts TIMESTAMP NOT NULL,
  title STRING,
  body STRING,
  sentiment STRING OPTIONS (description = 'positive | neutral | negative'),
  topic STRING OPTIONS (description = 'One of a closed list, see the prompt below.'),
  summary STRING,
  enriched_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(event_ts)
CLUSTER BY product_id;

INSERT INTO `${project}.gold.reviews_enriched`
WITH pending AS (
  SELECT r.*
  FROM `${project}.silver.reviews` AS r
  LEFT JOIN `${project}.gold.reviews_enriched` AS e USING (review_id, event_ts)
  WHERE e.review_id IS NULL
  LIMIT ${enrich_limit}
),
generated AS (
  SELECT *
  FROM AI.GENERATE_TABLE(
    MODEL `${project}.gold.gemini`,
    (SELECT *, CONCAT(
        'Review (Spanish):\nTitle: ', title, '\nBody: ', body, '\nRating: ', CAST(rating AS STRING), ' of 5\n\n',
        'Task: classify. sentiment: positive, neutral or negative. topic MUST be exactly one of these ',
        'values, nothing else: battery, connectivity, sound_quality, comfort, build_quality, shipping, ',
        'price, support, other. Do not invent other topics. summary: at most 15 words, in Spanish.') AS prompt
     FROM pending),
    STRUCT('sentiment STRING, topic STRING, summary STRING' AS output_schema,
           0 AS temperature, 200 AS max_output_tokens)
  )
)
SELECT
  review_id, product_id, rating, event_ts, title, body,
  LOWER(sentiment) AS sentiment, LOWER(topic) AS topic, summary,
  CURRENT_TIMESTAMP() AS enriched_at
FROM generated
WHERE status = ''
  AND LOWER(sentiment) IN ('positive', 'neutral', 'negative')
  AND LOWER(topic) IN ('battery', 'connectivity', 'sound_quality', 'comfort', 'build_quality',
                       'shipping', 'price', 'support', 'other');
