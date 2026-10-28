"""Interactive multi-turn smoke test, no fake offline agent."""
from app.agent.graph_agent import GraphTravelAgent
from app.agent.mcp_search import McpCatalogSearch
from app.agent.mcp_booking import McpCatalogBooking
from app.agent.compact_parser import CompactYandexTurnParser
from app.agent.text_provider import YandexTextGenerator


def main():
    agent = GraphTravelAgent(CompactYandexTurnParser(), YandexTextGenerator(), search=McpCatalogSearch(), booking=McpCatalogBooking())
    print("Travel Negotiator (LangGraph). Для выхода введи 'exit'.")
    while True:
        try:
            message = input("Вы: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nВыход.")
            break
        if message.lower() in {"exit", "quit", "выход"}:
            break
        reply = agent.handle("demo-thread", message)
        print(f"Агент [{reply.status}]: {reply.message}")
        for item in reply.options:
            print("  -", item)
        for item in reply.compromises:
            print("  *", item)


if __name__ == "__main__":
    main()
