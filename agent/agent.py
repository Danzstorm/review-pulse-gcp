"""Gemini with function calling over the two tools. One call to ask() = one question."""

import re
from datetime import date

from google import genai
from google.cloud import bigquery
from google.genai import types

import tools

MODEL = "gemini-2.5-flash"
MAX_TOOL_CALLS = 6  # a runaway loop costs money; a question needs two or three

SYSTEM_PROMPT = """You are an analyst for an e-commerce team. You answer questions about customer \
reviews in Spanish, using ONLY the two tools: get_metrics (numbers per product and day) and \
search_reviews (reviews closest in meaning to a text).

Rules:
- Today is {today}. Resolve relative dates ("this week", "last month") from it, and verify any \
change with get_metrics before claiming it.
- To say a metric rose or fell you need TWO periods from get_metrics to compare. If the data has \
only one period, say you cannot tell the trend and report the value you have.
- When you describe a change, state the numbers: the average rating and the share of negative \
reviews in each period, and the dates.
- To explain WHY, you MUST call search_reviews (filtered by product_id and sentiment) before \
saying anything about what customers wrote. Never quote or paraphrase a review you did not receive \
from search_reviews in this conversation.
- Cite each review you use by its review_id in square brackets, copied exactly from the tool result, \
like [3f2a9c1e-5b7d-4e21-9a0c-1d2e3f4a5b6c]. Never write an id a tool did not return.
- If the tools return nothing relevant, say so plainly instead of guessing.
- Be concise: a short paragraph plus the evidence."""


def ask(question: str, *, bq: bigquery.Client, client: genai.Client, project: str, today: date | None = None) -> dict:
    calls: list[dict] = []
    retrieved: dict[str, dict] = {}  # review_id -> review: everything the model may legitimately cite

    def _call(name: str, args: dict, run) -> dict:
        calls.append({"tool": name, "args": {k: v for k, v in args.items() if v is not None}})
        try:
            return {"rows": run()}
        except ValueError as e:  # bad arguments from the model: tell it, so it can correct them
            return {"error": str(e)}

    def get_metrics(product_name: str, start_date: str, end_date: str) -> dict:
        """Daily metrics (reviews, average rating, share of negative reviews, top negative topic) for
        every product whose name contains product_name, between two ISO dates (YYYY-MM-DD)."""
        return _call("get_metrics", dict(product_name=product_name, start_date=start_date, end_date=end_date),
                     lambda: tools.get_metrics(bq, project, product_name, start_date, end_date))

    def search_reviews(query: str, product_id: str = "", sentiment: str = "", start_date: str = "",
                       end_date: str = "", k: int = 5) -> dict:
        """Find reviews closest in meaning to query. Optional filters: product_id like P-0001,
        sentiment (positive, neutral or negative) and an ISO date range. k is at most 10."""
        args = dict(query=query, product_id=product_id or None, sentiment=sentiment or None,
                    start_date=start_date or None, end_date=end_date or None, k=k)
        result = _call("search_reviews", args, lambda: tools.search_reviews(bq, project, **args))
        for review in result.get("rows", []):
            retrieved[review["review_id"]] = review
        return result

    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT.format(today=(today or date.today()).isoformat()),
        tools=[get_metrics, search_reviews],
        temperature=0,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(maximum_remote_calls=MAX_TOOL_CALLS),
    )
    response = client.models.generate_content(model=MODEL, contents=question, config=config)
    answer = response.text or ""
    invented = _invented_ids(answer, retrieved)
    if invented:  # one retry, telling the model exactly what it got wrong
        retry = [types.Content(role="user", parts=[types.Part(text=question)]),
                 types.Content(role="model", parts=[types.Part(text=answer)]),
                 types.Content(role="user", parts=[types.Part(text=(
                     f"Your answer cites review ids no tool returned: {invented}. Call search_reviews and "
                     "answer again using only reviews it returns, copying their review_id exactly."))])]
        answer = client.models.generate_content(model=MODEL, contents=retry, config=config).text or ""
        invented = _invented_ids(answer, retrieved)
    if invented:  # still ungrounded: do not return text we cannot back up
        answer = "No pude respaldar la respuesta con reseñas reales. Intenta reformular la pregunta."
    # Only ids a tool really returned AND the answer really mentions count as citations.
    cited = [r for rid, r in retrieved.items() if rid in answer]
    return {"answer": answer, "verified": not invented, "tool_calls": calls, "cited_reviews": cited}


def _invented_ids(answer: str, retrieved: dict) -> list[str]:
    """Bracketed citations in the answer that no tool returned."""
    # The model may group several ids in one bracket: [id1, id2]. Only id-shaped tokens are checked,
    # so a bracketed product code like [P-0001] is not mistaken for a review id.
    cited = {t for group in re.findall(r"\[([^\]]+)\]", answer)
             for t in re.split(r"[,;\s]+", group) if re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{2,12})+", t)}
    return sorted(cited - retrieved.keys())
