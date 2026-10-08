"""Gemini with function calling over three tools. One call to ask() = one question, optionally
with the earlier turns of the conversation (the service keeps no state between requests)."""

import re
from datetime import date

from google import genai
from google.cloud import bigquery
from google.genai import types

import tools

MODEL = "gemini-2.5-flash"
MAX_TOOL_CALLS = 8  # a runaway loop costs money; a question needs two to four
ID_SHAPE = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{2,12})+")

SYSTEM_PROMPT = """You are an analyst for an e-commerce team. You answer questions about customer \
reviews in Spanish, using ONLY these tools: compare_periods (exact totals and averages for two \
periods), get_metrics (numbers per product and day) and search_reviews (reviews closest in \
meaning to a text).

Rules:
- Today is {today}. Resolve relative dates ("this week", "last month") from it.
- To say a metric rose or fell, call compare_periods with a "before" and an "after" period; its \
averages are exact, so never average the daily rows yourself. Use get_metrics only when you need \
the day-by-day shape. If the data has only one period, say you cannot tell the trend.
- When you describe a change, state the numbers and the dates of each period.
- To explain WHY, you MUST call search_reviews (filtered by product_id and sentiment) before \
saying anything about what customers wrote. Never quote or paraphrase a review you did not receive \
from search_reviews in this conversation.
- Cite each review you use by its review_id in square brackets, copied exactly from the tool result, \
like [3f2a9c1e-5b7d-4e21-9a0c-1d2e3f4a5b6c]. Never write an id a tool did not return.
- If the tools return nothing relevant, say so plainly instead of guessing.
- This may be a follow-up: use the earlier turns for context, and call tools again when the new \
question needs data you do not have.
- Be concise: a short paragraph plus the evidence."""


def ask(question: str, *, bq: bigquery.Client, client: genai.Client, project: str,
        history: list[dict] | None = None, today: date | None = None) -> dict:
    """history: earlier turns as [{"role": "user" | "model", "text": "..."}], oldest first."""
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
        return _call("get_metrics", {"product_name": product_name, "start_date": start_date, "end_date": end_date},
                     lambda: tools.get_metrics(bq, project, product_name, start_date, end_date))

    def compare_periods(product_name: str, before_start: str, before_end: str,
                        after_start: str, after_end: str) -> dict:
        """Totals and exact weighted averages (reviews, average rating, negative reviews and their
        share) for every product whose name contains product_name, in a 'before' and an 'after'
        period. Dates are ISO (YYYY-MM-DD) and the before period must end before the after one starts."""
        args = {"product_name": product_name, "before_start": before_start, "before_end": before_end,
                "after_start": after_start, "after_end": after_end}
        return _call("compare_periods", args, lambda: tools.compare_periods(bq, project, **args))

    def search_reviews(query: str, product_id: str = "", sentiment: str = "", start_date: str = "",
                       end_date: str = "", k: int = 5) -> dict:
        """Find reviews closest in meaning to query. Optional filters: product_id like P-0001,
        sentiment (positive, neutral or negative) and an ISO date range. k is at most 10."""
        args = {"query": query, "product_id": product_id or None, "sentiment": sentiment or None,
                "start_date": start_date or None, "end_date": end_date or None, "k": k}
        result = _call("search_reviews", args, lambda: tools.search_reviews(bq, project, **args))
        for review in result.get("rows", []):
            retrieved[review["review_id"]] = review
        return result

    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT.format(today=(today or date.today()).isoformat()),
        tools=[compare_periods, get_metrics, search_reviews],
        temperature=0,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(maximum_remote_calls=MAX_TOOL_CALLS),
    )
    contents = _contents(question, history)
    # Ids in earlier answers were verified when they were given. The history comes from the client,
    # so it is trusted only for what may be *mentioned*; cited_reviews below still needs a tool result.
    def known() -> set[str]:
        return set(retrieved) | _ids_in(*(t["text"] for t in history or []))

    answer = client.models.generate_content(model=MODEL, contents=contents, config=config).text or ""
    invented = _invented_ids(answer, known())
    if invented:  # one retry, telling the model exactly what it got wrong
        contents += [types.Content(role="model", parts=[types.Part(text=answer)]),
                     types.Content(role="user", parts=[types.Part(text=(
                         f"Your answer cites review ids no tool returned: {invented}. Call search_reviews and "
                         "answer again using only reviews it returns, copying their review_id exactly."))])]
        answer = client.models.generate_content(model=MODEL, contents=contents, config=config).text or ""
        invented = _invented_ids(answer, known())
    if invented:  # still ungrounded: do not return text we cannot back up
        answer = "No pude respaldar la respuesta con reseñas reales. Intenta reformular la pregunta."
    # Only ids a tool really returned AND the answer really mentions count as citations.
    cited = [r for rid, r in retrieved.items() if rid in answer]
    return {"answer": answer, "verified": not invented, "tool_calls": calls, "cited_reviews": cited}


def _contents(question: str, history: list[dict] | None) -> list[types.Content]:
    turns = [*(history or []), {"role": "user", "text": question}]
    return [types.Content(role=t["role"], parts=[types.Part(text=t["text"])]) for t in turns]


def _ids_in(*texts: str) -> set[str]:
    """Review-id-shaped tokens inside square brackets. The model may group several ids in one
    bracket, [id1, id2]; only id-shaped tokens count, so a code like [P-0001] is not an id."""
    return {t for text in texts for group in re.findall(r"\[([^\]]+)\]", text)
            for t in re.split(r"[,;\s]+", group) if ID_SHAPE.fullmatch(t)}


def _invented_ids(answer: str, known) -> list[str]:
    """Review ids cited in the answer that are neither tool results nor earlier verified turns."""
    return sorted(_ids_in(answer) - set(known))
