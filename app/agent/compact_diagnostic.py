"""Diagnose the REAL extraction prompt outside LangGraph. Does not print secrets or user text."""
import argparse
import json
import os
from app.agent.compact_parser import CompactYandexTurnParser

DEFAULT_MESSAGE = ("Мы вдвоём из Москвы, вылетаем 10 ноября 2026 года на пять ночей. "
                   "С нами ребёнок четырёх лет и собака. Нужен прямой рейс и завтрак.")


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--timeout", type=float, default=20)
    cli.add_argument("--max-tokens", type=int, default=650)
    args = cli.parse_args()
    parser = CompactYandexTurnParser(timeout=args.timeout, max_tokens=args.max_tokens,
                                     retry_invalid=False)
    try:
        result = parser.parse(DEFAULT_MESSAGE, current_state={})
        print("PASS", json.dumps(parser.last_diagnostic, ensure_ascii=False))
        print("operations:", [(op.op, op.field) for op in result.operations])
    except Exception as exc:
        print("FAIL", type(exc).__name__, json.dumps(parser.last_diagnostic, ensure_ascii=False))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
