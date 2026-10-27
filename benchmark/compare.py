"""Paired, reproducible Yandex AI Studio extraction benchmark.
Run: uv run python -m benchmark.compare --models 'gpt://.../aliceai-llm-flash' 'gpt://.../deepseek-v4.1-flash'
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from datetime import date
from pathlib import Path

from app.agent.llm_provider import INSTRUCTIONS, ParsedTurn

ROOT = Path(__file__).resolve().parent
FIELDS = ["origin", "destination", "departure_date", "nights", "adults", "budget_rub", "budget_hard", "intent", "allow_nights", "allow_date_shift", "allow_budget_increase"]


def normalize(value):
    if isinstance(value, date):
        return value.isoformat()
    return value


def score_case(case: dict, result: ParsedTurn) -> dict:
    actual = result.model_dump(mode="json")
    checks = []
    for k, expected in case.get("expected", {}).items():
        checks.append((f"expected.{k}", normalize(actual.get(k)) == expected))
    for k in case.get("must_be_null", []):
        checks.append((f"null.{k}", actual.get(k) is None))
    for k in case.get("must_clear", []):
        checks.append((f"clear.{k}", k in actual["clear_fields"]))
    unknown = " ".join(actual["unsupported_requirements"]).lower()
    # Semantic recall is evaluated by human review; substring checks are only rough diagnostics.
    for token in case.get("must_contain_unsupported", []):
        checks.append((f"unsupported.{token}", token.lower() in unknown))
    # Reject contradictory set/remove operations, and uncontrolled extra fields on patch cases.
    for field in actual["clear_fields"]:
        checks.append((f"no_set_and_clear.{field}", actual.get(field) is None))
    return {"passed": sum(ok for _, ok in checks), "total": len(checks), "details": {k: ok for k, ok in checks}, "exact_case": bool(checks) and all(ok for _, ok in checks)}


def run_one(client, model: str, case: dict, fmt: str, today: str, max_tokens: int):
    payload = json.dumps({"provided_today": today, "current_state": case["state"], "message": case["message"]}, ensure_ascii=False)
    response_format = ({"type": "json_object"} if fmt == "json_object" else
        {"type": "json_schema", "json_schema": {"name": "parsed_turn", "schema": ParsedTurn.model_json_schema()}})
    start = time.perf_counter()
    try:
        response = client.chat.completions.create(
            model=model, messages=[{"role": "system", "content": INSTRUCTIONS}, {"role": "user", "content": payload}],
            response_format=response_format, temperature=0, max_tokens=max_tokens,
        )
        elapsed = time.perf_counter() - start
        content = response.choices[0].message.content or ""
        parsed = ParsedTurn.model_validate_json(content)
        evaluation = score_case(case, parsed)
        usage = getattr(response, "usage", None)
        return {"ok": True, "latency_s": round(elapsed, 3), "score": evaluation,
                "tokens_in": getattr(usage, "prompt_tokens", None), "tokens_out": getattr(usage, "completion_tokens", None),
                "output": parsed.model_dump(mode="json")}
    except Exception as error:
        return {"ok": False, "latency_s": round(time.perf_counter() - start, 3),
                "error": f"{type(error).__name__}: {str(error)[:250]}"}


def summarize(rows: list[dict]) -> dict:
    completed = [x for x in rows if x["ok"]]
    all_checks = sum(x["score"]["total"] for x in completed)
    passed = sum(x["score"]["passed"] for x in completed)
    latencies = [x["latency_s"] for x in rows]
    return {"cases": len(rows), "valid_schema_rate": round(len(completed) / len(rows), 3) if rows else None,
            "field_accuracy_conditional": round(passed / all_checks, 3) if all_checks else None,
            "field_accuracy_all_cases": round(passed / (all_checks + sum(not x["ok"] for x in rows)), 3) if rows else None,
            "exact_case_rate": round(sum(x["score"]["exact_case"] for x in completed) / len(rows), 3) if rows else None,
            "latency_median_s": round(statistics.median(latencies), 3) if latencies else None,
            "latency_p95_s": round(sorted(latencies)[min(len(latencies)-1, int(0.95*len(latencies)))], 3) if latencies else None,
            "input_tokens": sum(x.get("tokens_in") or 0 for x in rows),
            "output_tokens": sum(x.get("tokens_out") or 0 for x in rows)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", required=True, help="Full gpt:// model URIs")
    parser.add_argument("--format", choices=["json_schema", "json_object"], default="json_schema")
    parser.add_argument("--today", default="2026-10-27", help="Fixed YYYY-MM-DD; avoid date.today() in benchmark")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--max-tokens", type=int, default=1200)
    parser.add_argument("--output", default="benchmark/results.json")
    args = parser.parse_args()
    if args.repeat < 1: parser.error("--repeat must be >= 1")
    from openai import OpenAI
    key = os.getenv("YANDEX_API_KEY")
    if not key:
        parser.error("YANDEX_API_KEY must be set in the environment")
    client = OpenAI(base_url=os.getenv("YANDEX_BASE_URL", "https://ai.api.cloud.yandex.net/v1"), api_key=key, timeout=35.0, max_retries=0)
    cases = json.loads((ROOT / "cases.json").read_text(encoding="utf-8"))
    output = {"settings": {"models": args.models, "format": args.format, "today": args.today, "repeat": args.repeat}, "models": {}}
    for model in args.models:
        rows = []
        for i in range(args.repeat):
            for case in cases:
                outcome = run_one(client, model, case, args.format, args.today, args.max_tokens)
                rows.append({"id": case["id"], "group": case["group"], "repeat": i, **outcome})
                print(model.split("/")[-2], case["id"], "OK" if outcome["ok"] else "ERROR", flush=True)
        output["models"][model] = {"summary": summarize(rows), "cases": rows}
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nSUMMARY")
    for model, result in output["models"].items():
        print(model, json.dumps(result["summary"], ensure_ascii=False))
    print(f"Saved: {path}")

if __name__ == "__main__":
    main()
