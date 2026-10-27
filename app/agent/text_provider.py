"""Specialized prompting through the same model and API key as request parsing."""
import json
import os
from typing import Any


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
        # One bounded retry for a completion cut by max_tokens.
        for token_limit in (650, 1300):
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
            return content.strip()
        raise ValueError("Model output truncated twice (finish_reason=length)")
