"""Minimal live Agent Mode CLI smoke test. No demo/offline mode."""

from app.agent.llm_provider import YandexTurnParser
from app.agent.orchestrator import Session, TravelAgent


def main() -> None:
    agent = TravelAgent(YandexTurnParser())
    session = Session()
    print("Travel Negotiator — Agent Mode (синтетические коммерческие предложения)")
    print("Введите /exit для выхода")
    while True:
        try:
            message = input("Вы: ").strip()
        except (KeyboardInterrupt, EOFError):
            break
        if message == "/exit":
            break
        reply = agent.handle(session, message)
        print(f"Агент [{reply.status}]: {reply.message}")
        for i, option in enumerate(reply.options, 1):
            print(
                f"  {i}. {option['destination']}, {option['hotel']}: "
                f"{option['price_rub']} RUB (синтетические данные)"
            )
        for i, compromise in enumerate(reply.compromises, 1):
            print(f"  Уступка {i}: {compromise['description']} — {compromise['price_rub']} RUB")


if __name__ == "__main__":
    main()
