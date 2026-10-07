-- One embedding per enriched review, built incrementally like reviews_enriched.
-- The text embedded is title + body. 768 dimensions (the model's default is 3072):
-- a quarter of the storage and a faster search, with little loss for short reviews.
CREATE TABLE IF NOT EXISTS `${project}.gold.review_embeddings` (
  review_id STRING NOT NULL,
  product_id STRING NOT NULL,
  event_ts TIMESTAMP NOT NULL,
  sentiment STRING,
  topic STRING,
  content STRING,
  embedding ARRAY<FLOAT64>,
  embedded_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(event_ts)
CLUSTER BY product_id;

INSERT INTO `${project}.gold.review_embeddings`
SELECT
  review_id, product_id, event_ts, sentiment, topic, content,
  ml_generate_embedding_result AS embedding,
  CURRENT_TIMESTAMP() AS embedded_at
FROM ML.GENERATE_EMBEDDING(
  MODEL `${project}.gold.embedder`,
  (
    SELECT e.*, CONCAT(e.title, '. ', e.body) AS content
    FROM `${project}.gold.reviews_enriched` AS e
    LEFT JOIN `${project}.gold.review_embeddings` AS v USING (review_id, event_ts)
    WHERE v.review_id IS NULL
    LIMIT ${enrich_limit}
  ),
  STRUCT(TRUE AS flatten_json_output, 768 AS output_dimensionality, 'SEMANTIC_SIMILARITY' AS task_type)
)
WHERE ml_generate_embedding_status = '';
