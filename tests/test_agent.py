"""Offline tests for the guardrails around the model. No GCP calls: every case here is rejected
(or accepted) before any query would run."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
import agent  # noqa: E402
import tools  # noqa: E402

REAL = "f1a7444a-04c7-4f2d-a4ae-44ca8cf4cb52"
OTHER = "1ce37d04-6456-48da-a55b-f5ed8194af9d"
RETRIEVED = {REAL: {}, OTHER: {}}


def rejects(fn, *args, **kwargs):
    try:
        fn(None, "p", *args, **kwargs)
    except ValueError:
        return True
    return False


def test_real_citations_pass():
    assert agent._invented_ids(f"Se desconectan [{REAL}].", RETRIEVED) == []


def test_grouped_citations_in_one_bracket_pass():
    assert agent._invented_ids(f"Fallan [{REAL}, {OTHER}].", RETRIEVED) == []


def test_invented_id_is_caught_even_next_to_a_real_one():
    fake = "a1b2c3d4-e5f6-7890-1234-567890abcdef"
    assert agent._invented_ids(f"Dicen [{REAL}, {fake}].", RETRIEVED) == [fake]


def test_product_codes_in_brackets_are_not_review_ids():
    assert agent._invented_ids("El producto [P-0001] baja.", RETRIEVED) == []


def test_answer_without_citations_has_nothing_invented():
    assert agent._invented_ids("No hay datos suficientes.", RETRIEVED) == []


def test_ids_from_earlier_turns_may_be_mentioned_again():
    history = [{"role": "model", "text": f"Fallan [{REAL}]."}]
    known = {REAL} | agent._ids_in(*(t["text"] for t in history))
    assert agent._invented_ids(f"Como dije [{REAL}].", known) == []
    assert agent._invented_ids(f"Y también [{OTHER}].", known) == [OTHER]


def test_history_becomes_alternating_contents_ending_with_the_new_question():
    history = [{"role": "user", "text": "hola"}, {"role": "model", "text": "¿en qué ayudo?"}]
    contents = agent._contents("¿y los precios?", history)
    assert [c.role for c in contents] == ["user", "model", "user"]
    assert contents[-1].parts[0].text == "¿y los precios?"
    assert [c.role for c in agent._contents("solo la pregunta", None)] == ["user"]


def test_compare_periods_rejects_bad_periods_before_querying():
    assert rejects(tools.compare_periods, "audífonos", "2026-10-02", "2026-10-07", "2026-10-07", "2026-10-08")  # overlap
    assert rejects(tools.compare_periods, "audífonos", "2026-10-06", "2026-10-02", "2026-10-07", "2026-10-08")  # reversed
    assert rejects(tools.compare_periods, "audífonos", "ayer", "2026-10-06", "2026-10-07", "2026-10-08")
    assert rejects(tools.compare_periods, " ", "2026-10-02", "2026-10-06", "2026-10-07", "2026-10-08")


def test_tools_reject_bad_arguments_before_querying():
    assert rejects(tools.get_metrics, "audífonos", "ayer", "2026-10-07")
    assert rejects(tools.get_metrics, "audífonos", "2026-10-07", "2026-10-01")
    assert rejects(tools.get_metrics, "  ", "2026-10-01", "2026-10-07")
    assert rejects(tools.search_reviews, "")
    assert rejects(tools.search_reviews, "bluetooth", product_id="1 OR 1=1")
    assert rejects(tools.search_reviews, "bluetooth", sentiment="angry")
    assert rejects(tools.search_reviews, "bluetooth", start_date="not-a-date")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
