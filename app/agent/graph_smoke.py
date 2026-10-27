"""Interactive multi-turn smoke test, no fake offline agent."""
from app.agent.graph_agent import GraphTravelAgent
from app.agent.llm_provider import YandexTurnParser
from app.agent.text_provider import YandexTextGenerator


def main():
    agent = GraphTravelAgent(YandexTurnParser(), YandexTextGenerator())
    print("Travel Negotiator (LangGraph). Для выхода введи 'exit'.")
    while True:
        message = input("Вы: ").strip()
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
