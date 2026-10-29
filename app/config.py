"""Типизированная загрузка настроек из TOML с переопределением через окружение."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field

SETTINGS_PATH = Path(__file__).resolve().parents[1] / "configs" / "settings.toml"


class WebSettings(BaseModel):
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8080, ge=1, le=65535)
    workers: int = Field(default=1, ge=1)


class YandexSettings(BaseModel):
    api_key: str | None = Field(default=None)
    folder_id: str | None = Field(default=None)
    base_url: str = Field(default="https://ai.api.cloud.yandex.net/v1")
    model: str = Field(default="deepseek-v4.1-flash")
    parser_model: str = Field(default="aliceai-llm-flash")
    parser_format: str = Field(default="auto")
    compact_timeout_seconds: float = Field(default=20.0, gt=0)
    compact_max_tokens: int = Field(default=750, ge=1)
    parser_timeout_seconds: float = Field(default=60.0, gt=0)
    parser_max_tokens: int = Field(default=2400, ge=1)
    text_timeout_seconds: float = Field(default=25.0, gt=0)
    text_max_retries: int = Field(default=1, ge=0)
    text_token_limits: tuple[int, ...] = Field(default=(650, 1300))


class CatalogSettings(BaseModel):
    extended_demo: bool = Field(default=False)
    seasonality_provider: str = Field(default="legacy")
    mcp_module: str = Field(default="app.mcp_catalog.server")
    search_limit: int = Field(default=3, ge=1)


class LoggingSettings(BaseModel):
    include_user_messages: bool = Field(default=True)


class ApplicationSettings(BaseModel):
    web: WebSettings = Field(default_factory=WebSettings)
    yandex: YandexSettings = Field(default_factory=YandexSettings)
    catalog: CatalogSettings = Field(default_factory=CatalogSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_settings(path: Path = SETTINGS_PATH) -> ApplicationSettings:
    """Загрузить базовые настройки и применить переменные окружения."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    yandex = data.setdefault("yandex", {})
    catalog = data.setdefault("catalog", {})
    logging_data = data.setdefault("logging", {})

    environment = os.environ
    yandex["api_key"] = environment.get("YANDEX_API_KEY") or None
    yandex["folder_id"] = environment.get("YANDEX_FOLDER_ID") or None
    yandex["base_url"] = environment.get("YANDEX_BASE_URL", yandex["base_url"])
    if "YANDEX_MODEL" in environment:
        yandex["model"] = environment["YANDEX_MODEL"]
        yandex["parser_model"] = environment["YANDEX_MODEL"]
    yandex["parser_format"] = environment.get("YANDEX_PARSER_FORMAT", yandex["parser_format"])
    if "TN_EXTENDED_DEMO" in environment:
        catalog["extended_demo"] = _as_bool(environment["TN_EXTENDED_DEMO"])
    catalog["seasonality_provider"] = environment.get(
        "TN_SEASONALITY_PROVIDER", catalog["seasonality_provider"]
    )
    if "TN_LOG_USER_MESSAGES" in environment:
        logging_data["include_user_messages"] = _as_bool(environment["TN_LOG_USER_MESSAGES"])
    return ApplicationSettings.model_validate(data)
