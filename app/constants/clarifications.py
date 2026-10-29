"""Стандартные вопросы для обязательных параметров поездки."""

from app.schemas.agent import Clarification

ORIGIN_CLARIFICATION = Clarification(
    code="origin",
    question="Из какого города планируете выезжать?",
    missing_label="город отправления",
    reason="required",
)
DEPARTURE_DATE_CLARIFICATION = Clarification(
    code="departure_date",
    question="Когда хотите выехать? Можно указать точную дату или период до 31 дня.",
    missing_label="дату или период отправления",
    reason="required",
)
NIGHTS_CLARIFICATION = Clarification(
    code="nights",
    question="На сколько ночей планируете поездку?",
    missing_label="продолжительность поездки",
    reason="required",
)
ADULTS_CLARIFICATION = Clarification(
    code="adults",
    question="Сколько взрослых поедет?",
    missing_label="число взрослых",
    reason="required",
)
CHILDREN_AGES_CLARIFICATION = Clarification(
    code="children_ages",
    question="Сколько лет каждому ребёнку на момент поездки?",
    missing_label="возраст детей",
    reason="missing_detail",
)
