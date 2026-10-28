import json
from pathlib import Path

from app.agent.llm_provider import ParsedTurn
from benchmark.compare import score_case, summarize


def test_gold_cases_valid():
    cases = json.loads(
        (Path(__file__).parents[1] / "benchmark" / "cases.json").read_text(encoding="utf-8")
    )
    assert len(cases) >= 20
    assert len({x["id"] for x in cases}) == len(cases)
    for case in cases:
        assert case["message"] and isinstance(case["state"], dict)
        assert (
            case.get("expected") or case.get("must_be_null") or case.get("must_contain_unsupported")
        )


def test_scoring_perfect_match():
    case = {
        "expected": {"origin": "Москва", "nights": 5},
        "must_be_null": ["destination"],
    }
    result = score_case(case, ParsedTurn(origin="Москва", nights=5))
    assert result["exact_case"]
    assert result["passed"] == 3


def test_wrong_or_hallucinated_value_fails():
    case = {"expected": {"nights": 5}, "must_be_null": ["origin"]}
    result = score_case(case, ParsedTurn(nights=6, origin="Москва"))
    assert not result["exact_case"]
    assert result["passed"] == 0


def test_summary_treats_invalid_model_output_as_failure():
    rows = [
        {
            "ok": True,
            "latency_s": 1,
            "score": {"passed": 2, "total": 2, "exact_case": True},
        },
        {"ok": False, "latency_s": 2, "error": "ValidationError"},
    ]
    value = summarize(rows)
    assert value["valid_schema_rate"] == 0.5
    assert value["exact_case_rate"] == 0.5
