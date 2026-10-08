"""The agent's two tools. Both run fixed, parameterized SQL: the model only supplies
values for the parameters, never SQL text, and every value is validated here first."""

import re
from datetime import date

from google.cloud import bigquery

SENTIMENTS = {"positive", "neutral", "negative"}
PRODUCT_ID = re.compile(r"^P-\d{4}$")
MAX_K = 10
CONTENT_CHARS = 300


def _day(value: str, name: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an ISO date like 2026-10-07, got {value!r}") from None


def _run(bq: bigquery.Client, sql: str, params: list) -> list[dict]:
    job = bq.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params), location="us-central1")
    return [dict(row) for row in job.result()]


def get_metrics(bq: bigquery.Client, project: str, product_name: str, start_date: str, end_date: str) -> list[dict]:
    """Daily rating metrics for every product whose name contains product_name."""
    start, end = _day(start_date, "start_date"), _day(end_date, "end_date")
    if not product_name or not product_name.strip():
        raise ValueError("product_name is required")
    if start > end:
        raise ValueError("start_date must not be after end_date")
    sql = f"""
        SELECT CAST(metric_date AS STRING) AS metric_date, product_id, product_name, n_reviews,
               avg_rating, n_negative, share_negative, top_negative_topic
        FROM `{project}.gold.product_daily_metrics`
        WHERE CONTAINS_SUBSTR(product_name, @name) AND metric_date BETWEEN @start AND @end
        ORDER BY product_id, metric_date
        LIMIT 200"""
    return _run(bq, sql, [
        bigquery.ScalarQueryParameter("name", "STRING", product_name.strip()),
        bigquery.ScalarQueryParameter("start", "DATE", start),
        bigquery.ScalarQueryParameter("end", "DATE", end),
    ])


def compare_periods(
    bq: bigquery.Client, project: str, product_name: str,
    before_start: str, before_end: str, after_start: str, after_end: str,
) -> list[dict]:
    """One row per product and period, with averages weighted by the number of reviews.

    Averaging the daily averages would give a day with 3 reviews the same weight as a day with
    300; summing first and dividing once is the exact figure."""
    b_start, b_end = _day(before_start, "before_start"), _day(before_end, "before_end")
    a_start, a_end = _day(after_start, "after_start"), _day(after_end, "after_end")
    if not product_name or not product_name.strip():
        raise ValueError("product_name is required")
    if b_start > b_end or a_start > a_end:
        raise ValueError("each period must start on or before its end")
    if b_end >= a_start:
        raise ValueError("the 'before' period must end before the 'after' period starts")
    sql = f"""
        SELECT product_id, product_name, period, SUM(n_reviews) AS n_reviews,
               ROUND(SAFE_DIVIDE(SUM(avg_rating * n_reviews), SUM(n_reviews)), 2) AS avg_rating,
               SUM(n_negative) AS n_negative,
               ROUND(SAFE_DIVIDE(SUM(n_negative), SUM(n_reviews)), 3) AS share_negative
        FROM (
          SELECT *, IF(metric_date BETWEEN @b_start AND @b_end, 'before', 'after') AS period
          FROM `{project}.gold.product_daily_metrics`
          WHERE CONTAINS_SUBSTR(product_name, @name)
            AND (metric_date BETWEEN @b_start AND @b_end OR metric_date BETWEEN @a_start AND @a_end))
        GROUP BY product_id, product_name, period
        ORDER BY product_id, period DESC"""
    return _run(bq, sql, [
        bigquery.ScalarQueryParameter("name", "STRING", product_name.strip()),
        bigquery.ScalarQueryParameter("b_start", "DATE", b_start),
        bigquery.ScalarQueryParameter("b_end", "DATE", b_end),
        bigquery.ScalarQueryParameter("a_start", "DATE", a_start),
        bigquery.ScalarQueryParameter("a_end", "DATE", a_end),
    ])


def search_reviews(
    bq: bigquery.Client, project: str, query: str, product_id: str | None = None,
    sentiment: str | None = None, start_date: str | None = None, end_date: str | None = None, k: int = 5,
) -> list[dict]:
    """The k reviews closest in meaning to the query, optionally filtered."""
    if not query or not query.strip():
        raise ValueError("query is required")
    if product_id is not None and not PRODUCT_ID.match(product_id):
        raise ValueError(f"product_id must look like P-0001, got {product_id!r}")
    if sentiment is not None and sentiment not in SENTIMENTS:
        raise ValueError(f"sentiment must be one of {sorted(SENTIMENTS)}, got {sentiment!r}")
    start = _day(start_date, "start_date") if start_date else None
    end = _day(end_date, "end_date") if end_date else None
    k = max(1, min(int(k), MAX_K))
    # The embedding options must match sql/enrichment/review_embeddings.sql, or the vectors are
    # not comparable. top_k is large because identical texts collapse to one row below.
    sql = f"""
        SELECT base.review_id, base.product_id, base.topic, base.sentiment,
               CAST(DATE(base.event_ts) AS STRING) AS review_date,
               SUBSTR(base.content, 1, {CONTENT_CHARS}) AS content, ROUND(distance, 3) AS distance
        FROM VECTOR_SEARCH(
          (SELECT * FROM `{project}.gold.review_embeddings`
           WHERE (@product_id IS NULL OR product_id = @product_id)
             AND (@sentiment IS NULL OR sentiment = @sentiment)
             AND (@start IS NULL OR DATE(event_ts) >= @start)
             AND (@end IS NULL OR DATE(event_ts) <= @end)),
          'embedding',
          (SELECT ml_generate_embedding_result AS embedding
           FROM ML.GENERATE_EMBEDDING(
             MODEL `{project}.gold.embedder`, (SELECT @query AS content),
             STRUCT(TRUE AS flatten_json_output, 768 AS output_dimensionality, 'SEMANTIC_SIMILARITY' AS task_type))),
          top_k => 300, distance_type => 'COSINE')
        QUALIFY ROW_NUMBER() OVER (PARTITION BY base.content ORDER BY distance) = 1
        ORDER BY distance
        LIMIT @k"""
    return _run(bq, sql, [
        bigquery.ScalarQueryParameter("query", "STRING", query.strip()),
        bigquery.ScalarQueryParameter("product_id", "STRING", product_id),
        bigquery.ScalarQueryParameter("sentiment", "STRING", sentiment),
        bigquery.ScalarQueryParameter("start", "DATE", start),
        bigquery.ScalarQueryParameter("end", "DATE", end),
        bigquery.ScalarQueryParameter("k", "INT64", k),
    ])
