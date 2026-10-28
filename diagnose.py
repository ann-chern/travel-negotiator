"""Safe connectivity check for Yandex AI Studio / DeepSeek.
Run: uv run --env-file .env python diagnose.py
No secret, user message or response text is printed.
"""

from __future__ import annotations

import json
import os
import time

from openai import OpenAI


def check(label: str, client: OpenAI, model: str, *, json_mode: bool) -> None:
    system = "Отвечай только JSON-объектом." if json_mode else "Отвечай кратко."
    user = (
        "Верни JSON-объект с одним ключом ok и значением true."
        if json_mode
        else "Ответь одним словом: готово."
    )
    params = dict(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0,
        max_tokens=128,
    )
    if json_mode:
        params["response_format"] = {"type": "json_object"}
    start = time.perf_counter()
    try:
        response = client.chat.completions.create(**params)
        elapsed = time.perf_counter() - start
        choice = response.choices[0] if response.choices else None
        body = (choice.message.content or "") if choice else ""
        valid = None
        if json_mode and body:
            try:
                valid = isinstance(json.loads(body), dict)
            except json.JSONDecodeError:
                valid = False
        usage = response.usage
        print(
            f"{label}: OK, elapsed={elapsed:.1f}s, "
            f"finish_reason={getattr(choice, 'finish_reason', None)}, "
            f"nonempty={bool(body.strip())}, valid_json={valid}, "
            f"tokens_in={getattr(usage, 'prompt_tokens', None)}, "
            f"tokens_out={getattr(usage, 'completion_tokens', None)}"
        )
    except Exception as exc:
        elapsed = time.perf_counter() - start
        status = getattr(exc, "status_code", None)
        print(
            f"{label}: ERROR, elapsed={elapsed:.1f}s, "
            f"type={type(exc).__name__}, http_status={status}"
        )


def main() -> None:
    key = os.environ.get("YANDEX_API_KEY")
    folder = os.environ.get("YANDEX_FOLDER_ID")
    if not key or not folder:
        raise SystemExit(
            "YANDEX_API_KEY / YANDEX_FOLDER_ID are missing. Load .env using uv --env-file."
        )
    model = os.getenv("YANDEX_MODEL", "deepseek-v4.1-flash")
    if not model.startswith("gpt://"):
        model = f"gpt://{folder}/{model}"
    base_url = os.getenv("YANDEX_BASE_URL", "https://ai.api.cloud.yandex.net/v1")
    # Avoid logging model URI and request objects; only timings and response metadata.
    client = OpenAI(api_key=key, project=folder, base_url=base_url, timeout=20.0, max_retries=0)
    check("plain", client, model, json_mode=False)
    check("json_object", client, model, json_mode=True)


if __name__ == "__main__":
    main()
