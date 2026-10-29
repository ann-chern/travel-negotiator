"""Small LLM-facing schema and bounded Yandex/DeepSeek parser.

No user request is mutated here. The existing orchestrator applies validated edits.
"""

from __future__ import annotations

import json
import time
from datetime import date

from pydantic import ValidationError

from app.config import ApplicationSettings, load_settings
from app.constants.prompts import COMPACT_PARSER_SYSTEM_PROMPT
from app.observability import event as audit_event
from app.schemas.agent import CompactTurn, ParsedTurn


class CompactYandexTurnParser:
    """Drop-in TurnParser. Bounded call; no timeout retry, optional one JSON correction."""

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        folder_id: str | None = None,
        client=None,
        timeout: float | None = None,
        max_tokens: int | None = None,
        retry_invalid: bool = True,
        settings: ApplicationSettings | None = None,
    ):
        configuration = settings or load_settings()
        yandex = configuration.yandex
        folder = folder_id or yandex.folder_id
        if not folder:
            raise RuntimeError("YANDEX_FOLDER_ID is required")
        self.model = model or yandex.model
        if not self.model.startswith("gpt://"):
            self.model = f"gpt://{folder}/{self.model}"
        if client is None:
            key = api_key or yandex.api_key
            if not key:
                raise RuntimeError("YANDEX_API_KEY is required")
            from openai import OpenAI

            client = OpenAI(
                api_key=key,
                base_url=yandex.base_url,
                project=folder,
                max_retries=0,
                timeout=timeout or yandex.compact_timeout_seconds,
            )
        self.client = client
        self.timeout = timeout or yandex.compact_timeout_seconds
        self.max_tokens = max_tokens or yandex.compact_max_tokens
        self.retry_invalid = retry_invalid
        self.last_diagnostic: dict = {}

    def parse(self, message: str, current_state: dict) -> ParsedTurn:
        audit_event(
            "llm.parse.started",
            provider="yandex",
            model=self.model,
            temperature=0,
            max_tokens=self.max_tokens,
            current_state=current_state,
        )
        messages = [
            {"role": "system", "content": COMPACT_PARSER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "today": date.today().isoformat(),
                        "current_state": current_state,
                        "message": message,
                    },
                    ensure_ascii=False,
                    default=str,
                ),
            },
        ]
        for attempt in range(2 if self.retry_invalid else 1):
            started = time.monotonic()
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0,
                    response_format={"type": "json_object"},
                    max_tokens=self.max_tokens,
                    timeout=self.timeout,
                )
            except Exception as exc:
                self.last_diagnostic = {
                    "attempt": attempt + 1,
                    "error_type": type(exc).__name__,
                    "elapsed_s": round(time.monotonic() - started, 3),
                }
                audit_event("llm.parse.failed", **self.last_diagnostic)
                raise  # Timeout/auth/network: no implicit costly repeat
            choice = response.choices[0] if response.choices else None
            finish = getattr(choice, "finish_reason", None)
            content = (getattr(getattr(choice, "message", None), "content", None) or "").strip()
            usage = getattr(response, "usage", None)
            self.last_diagnostic = {
                "attempt": attempt + 1,
                "elapsed_s": round(time.monotonic() - started, 3),
                "finish_reason": finish,
                "nonempty": bool(content),
                "tokens_in": getattr(usage, "prompt_tokens", None),
                "tokens_out": getattr(usage, "completion_tokens", None),
            }
            try:
                if not content or finish == "length":
                    raise ValueError("Empty or truncated compact response")
                parsed = CompactTurn.model_validate_json(content).as_parsed_turn()
                audit_event("llm.parse.completed", interpretation=parsed, **self.last_diagnostic)
                return parsed
            except (ValidationError, ValueError):
                self.last_diagnostic["error_type"] = "CompactValidationError"
                audit_event("llm.parse.invalid", **self.last_diagnostic)
                if attempt or not self.retry_invalid:
                    raise ValueError(
                        "Compact model response did not match validated operations contract"
                    ) from None
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Верни короткий JSON с intent и operations; поле value "
                            "обязательно, для remove оно null. "
                            "Не дублируй прежнее состояние. "
                            "Не добавляй комментарии."
                        ),
                    }
                )
        raise AssertionError("unreachable")
