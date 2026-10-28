# Travel Negotiator — Островок, «О!дин запрос»

Прототип AI-агента: понимает требования путешественника, уточняет недостающие условия, подбирает и проверяет поездки в **синтетическом каталоге**, объясняет компромиссы, пересобирает план и выполняет **только демонстрационное** бронирование.

## Быстрый старт

Требуются Docker Engine и Docker Compose. Скопируйте `.env.example` в `.env`, укажите **свои** параметры Yandex Cloud LLM, затем выполните:

```bash
git clone <URL_КОМАНДНОГО_РЕПОЗИТОРИЯ>
cd <ПАПКА_РЕПОЗИТОРИЯ>
cp .env.example .env
docker compose up --build
```

Откройте http://localhost:8080. Приложение инициализирует LLM при первом сообщении; **без `YANDEX_API_KEY` и `YANDEX_FOLDER_ID` чат не сможет обрабатывать запросы**, хотя `/health` будет доступен. Никакая локальная GPU не требуется.

## Интерфейс и маршруты

- `/` — веб-интерфейс; ответы от сервера поступают в виде SSE-событий выполнения графа.
- `GET /health` — `200`, JSON со статусом `ok`.
- `POST /api/chat` — стандартный JSON-ответ.
- `POST /api/chat/stream` — SSE, `progress`, затем `result` или `error`. **Это streaming этапов выполнения, не потоковая генерация токенов LLM.**
- `/api/health` — совместимость с первоначальным API.

## Настройка и данные

- Python 3.12; версии Python-пакетов зафиксированы в `uv.lock`.
- `YANDEX_API_KEY` — ключ API Yandex Cloud для LLM.
- `YANDEX_FOLDER_ID` — folder ID.
- `YANDEX_MODEL` — модель, по умолчанию `deepseek-v4.1-flash`.
- `YANDEX_BASE_URL` — базовый URL API, по умолчанию `https://ai.api.cloud.yandex.net/v1`.
- Приложение использует встроенный синтетический seed-каталог из `app/data/catalog.py`; отдельная миграция не требуется. Цены, рейсы, доступность и бронирования **не являются настоящими**.
- Логи: `docker compose logs -f app`. Каждый шаг агента пишется отдельной JSON-строкой с общими `request_id` и `session_id`: прием сообщения, результат разбора LLM, маршрутизация, недостающие поля и уточнение, параметры MCP-поиска, ранжирование с причинами и итоговый ответ. API также возвращает `X-Request-ID`, а первое SSE-событие содержит `request_id`. Ключи и скрытые рассуждения модели не логируются. Текст пользователя логируется по умолчанию; установите `TN_LOG_USER_MESSAGES=0`, чтобы заменить его на `<redacted>`. Данные диалогов хранятся в памяти процесса и сбрасываются при перезапуске.
- Рекомендуемые ресурсы для демонстрации: 2 CPU, 2–4 ГБ RAM, выход в интернет для LLM.

## Тесты

При установленном `uv` и Python 3.12: `uv sync --frozen --group dev && uv run python -m pytest -q`.



## Архитектура и обработка запроса

- `app/agent/` — интерпретация текста, LangGraph, управление состоянием диалога и перепланирование.
- `app/mcp_catalog/` — отдельный MCP-процесс каталога и контракт предложений.
- `app/data/` — расширяемый синтетический каталог из 15 направлений, профили маршрутов и цен.
- `app/services/` — оценка допустимости, ранжирование, сезонность Open-Meteo.
- `app/web/` — FastAPI, SSE и веб-интерфейс.

Гибкие месяцы обрабатываются как окна дат, при уточнениях сохраняются неназванные
параметры, а изменение бюджета не снимает ограничение без указания пользователя.
Период поиска по одной операции ограничен 31 днём. Расписание, цены и наличие —
моковые; демонстрационное подтверждение не является бронированием.

## Climate and historical catalog (2026 extension)

### Seasonal data: Open-Meteo

The offline-compatible `OpenMeteoSeasonalityProvider` reads a locally
refreshed `app/data/openmeteo_normals.json` file. It **never** calls an API from a
chat request, avoiding delays or outages during judging. Historical ERA5 daily
maximum air temperatures and Open-Meteo marine sea-surface temperatures are
aggregated by month. These are *historical norms, not weather forecasts for 2027*.
A missing sea value remains `unknown`. The previous hand-curated demonstration
normals are only used where enabled as a separate fallback; their source note is
retained. Thus real Open-Meteo coverage is **not** claimed until refreshed.

To prefetch (requires internet, Python project environment):

```bash
uv run python -m scripts.refresh_climate --start-year 2022 --end-year 2025 --include-extended
```

For each destination, use an appropriate coastal sea coordinate rather than a
city-center coordinate when validating marine readings. The current command
uses city-center coordinates and therefore may report sea values unavailable.
Before a public demo, review the generated `months`, `sea_data_issue`, date
range and location selection. The cache file persists across restarts if it is
included in the Docker build context before building the image.

Data attribution: Open-Meteo and underlying Copernicus/ECMWF/MeteoFrance
providers. Terms: https://open-meteo.com/en/terms . Free noncommercial use has
limits; review commercial conditions before any commercial deployment.

### Catalog with provenance

`TN_EXTENDED_DEMO=1` enables 15 geographic destinations in the local synthetic
catalog (still with fictional hotel names, flight schedules, prices and
availability). It is enabled in `docker-compose.yml` for broader hackathon tests.
This flag **does not** create real offers.

For actual observed historical prices, import your own legally collected JSON
array into `app/data/offer_snapshots.json`:

```bash
uv run python -m scripts.import_snapshots path/to/verified_snapshots.json
```

The schema is `app.data.snapshot_catalog.HistoricalSnapshot`; see
`app/data/snapshot_example.json` for the **format only** (the example is fictional
and must not be imported as authentic). Each record requires `source_url`,
`observed_at`, hotel, actual stay dates and guest count. Deduplication uses
source URL + source offer ID + observation time. The endpoint
`GET /api/catalog/history?destination_id=antalya` lists historical observations,
and MCP `search_historical_snapshots` does likewise. Historical records are **not
live bookable** and are intentionally not used to synthesize entire packages
with imaginary matching flights or transfers. The existing synthetic-package
booking flow is separate.

If the historical catalog is empty, the API returns zero results. Populating a
broad, real snapshot dataset requires actual authorized collection; no scraped
prices were manufactured in this project. Check the source's terms of use and
store screen evidence only with rights to retain/share it.

## Калиброванный мок-каталог (v2)

`app/data/demo_pricing.py` содержит **редакционные ориентиры** для 15 направлений:
базовую стоимость ночи в отеле 3★ (двухместный номер), перелёта в одну сторону
на человека и группового трансфера. Это не котировки и не реальные исторические
снимки, а правдоподобные допущения для демонстрации.

Генератор учитывает категорию отеля (3★, 4★, 5★), сезонный коэффициент,
число ночей, состав гостей и детерминированные небольшие изменения цены по дате.
Цена поездки складывается из размещения, двух авиаперелётов и двух трансферов.
Цены в RUB являются примерными; при изменении рыночной ситуации требуется
перекалибровка ориентиров. Все предложения помечены `synthetic`, реальные брони
и платежи не создаются.

Каталог не имитирует конкретные авиакомпании, реальные расписания или
доступность номеров. В частности, синтетический вариант «прямой перелёт» не
является подтверждением наличия беспересадочного рейса на этом направлении.
Если потребуется повышенная реалистичность, эти поля следует заменить
проверенными reference-снимками **без автоматического скрейпинга**.

Тесты адекватности и повторяемости: `uv run pytest tests/test_demo_pricing.py -q`.

## Реалистичность синтетического каталога

В `app/data/inventory_profiles.py` хранятся независимые демонстрационные
профили гостиниц (`HOTELS`) и маршрутов (`ROUTES`). Это **не реальные отели,
расписания рейсов или подтверждённые тарифы**. Все предложения сохраняют
`provenance.source_type=synthetic`.

* Для дальней поездки политика может не предлагать прямой перелёт. Это редакционное
  допущение для моковых данных, а не проверка фактической маршрутной сети.
* Трансфер зависит от назначения; в особенности у Мальдив демонстрационная
  стоимость и длительность выше обычного городского трансфера. Настоящие типы
  трансфера (лодка, гидросамолёт и др.) пока не представлены типом в схеме.
* Длительная дорога и позднее прибытие могут дать статус валидации `unknown`
  с `late_checkin`. Это корректное предупреждение, не повод обходить валидатор.
* Чтобы расширить географию, добавьте город в `EXTENDED_CITIES`, ценовой диапазон
  в `BANDS` и маршрут в `ROUTES`; проверьте тесты совместимости.
* Версия сохраняет прежние идентификаторы офферов и MCP-контракты, чтобы не
  нарушить выбор карточки и демо-бронирование. Категории размещения по-прежнему
  представлены тремя прототипами на направление, не полноценным hotel inventory.

Пример проверки: `python -m pytest -q tests/test_inventory_profiles.py`.

## Подбор портфеля вариантов (MVP)

Поиск по гибкому периоду (до 31 дня включительно) проверяет каждую дату, а
затем выбирает **разные** предложения из прошедших детерминированную проверку
ограничений. Если направление открыто, приоритет — разные города; при заданном
направлении — стоимость, уровень отеля и число пересадок. Это не отменяет
жёсткий бюджет, направление, состав гостей или даты. Не гарантируется, что
каждое из трёх предложений отличается по всем признакам: реальный состав
результата зависит от доступного демонстрационного инвентаря.

В карточке выводится разбивка синтетической полной стоимости: отель,
перелёты туда/обратно и трансферы в обе стороны. Сумма компонентов должна
совпадать с ценой поездки; реального бронирования и подтверждения цен нет.

**Ссылка на скринкаст:** ДОБАВИТЬ
