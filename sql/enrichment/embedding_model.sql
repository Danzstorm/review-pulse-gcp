-- Remote embedding model: turns text into a vector of numbers so that reviews that mean
-- the same thing end up close together, even when they share no words.
CREATE OR REPLACE MODEL `${project}.gold.embedder`
  REMOTE WITH CONNECTION `${project}.${region}.review-pulse-vertex`
  OPTIONS (endpoint = '${embedding_endpoint}');
