"""Specialized prompting through the same model and API key as request parsing."""
import json
import os
import time
from typing import Any

from app.observability import event as audit_event


PROMPTS = {
    "clarification": "Ты агент уточнений Travel Negotiator. Задай один полезный короткий вопрос на русском. Не придумывай условия.",
    "recommendation": "Ты агент рекомендаций. Объясняй лишь проверенные переданные предложения. Цены и предложения демонстрационные.",
    "negotiation": "Ты агент переговоров. Объясняй только переданные уступки; никогда не считай их принятыми.",
}


class YandexTextGenerator:
    def __init__(self, *, client: Any = None, model: str | None = None, folder_id: str | None = None):
        folder = folder_id or os.getenv("YANDEX_FOLDER_ID")
        if not folder:
            raise RuntimeError("YANDEX_FOLDER_ID is required")
        if client is None:
            key = os.getenv("YANDEX_API_KEY")
            if not key:
                raise RuntimeError("YANDEX_API_KEY is required")
            from openai import OpenAI
            client = OpenAI(api_key=key, base_url="https://ai.api.cloud.yandex.net/v1",
                            project=folder, timeout=25.0, max_retries=1)
        self.client = client
        name = model or os.getenv("YANDEX_MODEL", "deepseek-v4.1-flash")
        self.model = name if name.startswith("gpt://") else f"gpt://{folder}/{name}"

    def generate(self, *, task: str, payload: dict[str, Any]) -> str:
        if task not in PROMPTS:
            raise ValueError("Unknown task")
        task_payload = dict(payload)
        if task == "recommendation":
            # A compact factual projection: the UI has full cards, while the LLM
            # needs only enough evidence to explain trade-offs. Saves prompt tokens.
            request = task_payload.get("request", {})
            if isinstance(request, dict):
                task_payload["request"] = {k: request.get(k) for k in (
                    "origin", "destination", "departure_date", "date_window",
                    "nights", "travelers", "budget", "preferences") if request.get(k) is not None}
            fields = ("id", "destination", "hotel", "price_rub", "departure_date",
                      "nights", "score", "hotel_stars", "meal_plan",
                      "outbound_stops", "inbound_stops", "tradeoffs", "match_reasons")
            task_payload["options"] = [
                {k: row[k] for k in fields if k in row}
                for row in task_payload.get("options", []) if isinstance(row, dict)
            ]
        # Avoid duplicating the full structured offer list in generated text.
        task_payload["output_style"] = (
            "Отвечай по-русски, 2–4 коротких предложения, без Markdown-таблиц; "
            "не дублируй список предложений, он выводится отдельно. "
            "Никаких выдуманных цен и обещаний бронирования."
        )
        messages = [
            {"role": "system", "content": PROMPTS[task]},
            {"role": "user", "content": json.dumps(task_payload, ensure_ascii=False, default=str)},
        ]
        audit_event("llm.generate.started", task=task, provider="yandex", model=self.model,
                    temperature=0.2, payload=task_payload)
        # One bounded retry for a completion cut by max_tokens.
        for token_limit in (650, 1300):
            started = time.monotonic()
            response = self.client.chat.completions.create(
                model=self.model, messages=messages, temperature=0.2,
                max_tokens=token_limit,
            )
            if not response.choices:
                raise ValueError("No choices in model response")
            choice = response.choices[0]
            if choice.finish_reason == "length":
                continue
            if choice.finish_reason not in ("stop", None):
                raise ValueError(f"Model response not complete: {choice.finish_reason}")
            content = choice.message.content
            if not content or not content.strip():
                raise ValueError("Empty model response")
            result = content.strip()
            usage = getattr(response, "usage", None)
            audit_event("llm.generate.completed", task=task, output=result,
                        finish_reason=choice.finish_reason, max_tokens=token_limit,
                        elapsed_s=round(time.monotonic() - started, 3),
                        tokens_in=getattr(usage, "prompt_tokens", None),
                        tokens_out=getattr(usage, "completion_tokens", None))
            return result
        raise ValueError("Model output truncated twice (finish_reason=length)")
