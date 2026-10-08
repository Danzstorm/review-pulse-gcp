-- Semantic search: embed the question with the SAME model and options used for the
-- reviews, then return the nearest reviews by cosine distance.
-- Try it: bash scripts/run_sql.sh sql/gold/search_reviews.sql
-- No vector index yet: CREATE VECTOR INDEX needs 5,000+ rows. Until then VECTOR_SEARCH
-- compares against every row (exact, and fast enough at this size).
SELECT base.review_id, base.product_id, base.topic, base.sentiment, ROUND(distance, 3) AS distance, base.content
FROM VECTOR_SEARCH(
  TABLE `${project}.gold.review_embeddings`,
  'embedding',
  (SELECT ml_generate_embedding_result AS embedding
   FROM ML.GENERATE_EMBEDDING(
     MODEL `${project}.gold.embedder`,
     (SELECT 'los audífonos se desconectan solos del celular' AS content),
     STRUCT(TRUE AS flatten_json_output, 768 AS output_dimensionality, 'SEMANTIC_SIMILARITY' AS task_type))),
  top_k => 500,
  distance_type => 'COSINE'
)
-- Identical texts (a review pasted twice, or the old template data) count once.
QUALIFY ROW_NUMBER() OVER (PARTITION BY base.content ORDER BY distance) = 1
ORDER BY distance
LIMIT 5;
