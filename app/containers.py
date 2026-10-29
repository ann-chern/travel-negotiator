"""Register application resources and manage their lifecycle."""

from __future__ import annotations

from threading import RLock

from app.config import ApplicationSettings, load_settings


class ApplicationContainer:
    """Create shared services lazily and release them on shutdown."""

    def __init__(self, settings: ApplicationSettings | None = None) -> None:
        self.settings = settings or load_settings()
        self.agent_lock = RLock()
        self._agent = None

    @property
    def agent(self):
        if self._agent is None:
            from app.agent.compact_parser import CompactYandexTurnParser
            from app.agent.graph_agent import GraphTravelAgent
            from app.agent.mcp_booking import McpCatalogBooking
            from app.agent.mcp_search import McpCatalogSearch
            from app.agent.text_provider import YandexTextGenerator

            catalog = self.settings.catalog
            self._agent = GraphTravelAgent(
                CompactYandexTurnParser(settings=self.settings),
                YandexTextGenerator(settings=self.settings),
                search=McpCatalogSearch(module=catalog.mcp_module, limit=catalog.search_limit),
                booking=McpCatalogBooking(module=catalog.mcp_module),
            )
        return self._agent

    def close(self) -> None:
        resource = self._agent
        self._agent = None
        close = getattr(resource, "close", None)
        if callable(close):
            close()
