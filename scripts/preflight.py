"""Offline release readiness checks. Run: python -m scripts.preflight."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def audit(root: Path = ROOT) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    for name in (
        "README.md",
        "docker-compose.yml",
        "Dockerfile",
        ".env.example",
        "uv.lock",
    ):
        if not (root / name).is_file():
            failures.append(f"Отсутствует {name}")
    compose = (
        (root / "docker-compose.yml").read_text(encoding="utf-8")
        if (root / "docker-compose.yml").exists()
        else ""
    )
    dockerfile = (
        (root / "Dockerfile").read_text(encoding="utf-8") if (root / "Dockerfile").exists() else ""
    )
    ignore = (
        (root / ".gitignore").read_text(encoding="utf-8") if (root / ".gitignore").exists() else ""
    )
    if not re.search(r"8080:8080", compose):
        failures.append("Compose: нет проброса 8080:8080")
    if "healthcheck:" not in compose or "/health" not in compose:
        failures.append("Compose: отсутствует healthcheck /health")
    if "TN_EXTENDED_DEMO:" not in compose:
        warnings.append("Compose не задаёт TN_EXTENDED_DEMO")
    if len(re.findall(r"(?m)^FROM ", dockerfile)) < 2:
        failures.append("Dockerfile не использует multi-stage build")
    if "USER app" not in dockerfile:
        failures.append("Dockerfile: не найден непривилегированный USER app")
    if ".env" not in ignore:
        failures.append("Нет правила игнорирования .env")
    if (root / ".env").exists():
        warnings.append("В рабочем каталоге есть .env: проверьте, что он не попал в Git/архив")
    if not (root / "secrets.env.age").is_file():
        warnings.append("secrets.env.age отсутствует: нужен перед отправкой жюри")
    if not (root / "app/data/openmeteo_normals.json").is_file():
        warnings.append(
            "Климатический кэш отсутствует: часть оценок сезонности будет unknown/fallback"
        )
    readme = (
        (root / "README.md").read_text(encoding="utf-8") if (root / "README.md").exists() else ""
    )
    if "ДОБАВИТЬ ПЕРЕД СДАЧЕЙ" in readme:
        warnings.append("В README пока нет ссылки на скринкаст")
    for path in root.rglob("*.py"):
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        body = path.read_text(encoding="utf-8")
        if re.search(r"(?:sk-|y0_AgAAAA)[A-Za-z0-9_-]{24,}", body):
            failures.append(f"Возможный секрет в {path.relative_to(root)}")
    return failures, warnings


def main() -> int:
    failures, warnings = audit()
    for warning in warnings:
        print("WARN:", warning)
    for error in failures:
        print("FAIL:", error)
    print(f"Preflight: {len(failures)} blocking issues, {len(warnings)} warnings")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
