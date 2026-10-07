-- Remote model: BigQuery forwards ML.GENERATE_TEXT calls to Gemini on Vertex AI using
-- the connection created in infra/enrichment.tf.
CREATE OR REPLACE MODEL `${project}.gold.gemini`
  REMOTE WITH CONNECTION `${project}.${region}.review-pulse-vertex`
  OPTIONS (endpoint = '${gemini_endpoint}');
