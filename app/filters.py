"""Отсев вакансий до обращения к DeepSeek: стоп-слова, компании, дубли."""
import re

from app.sources.base import Vacancy


def _norm(text: str) -> str:
    return re.sub(r"[^\w]+", " ", (text or "").lower().replace("ё", "е")).strip()


def stop_reason(v: Vacancy, settings: dict) -> str:
    """Почему вакансию надо пропустить, или пустая строка."""
    company = _norm(v.company)
    for name in settings.get("excluded_companies") or []:
        n = _norm(name)
        if n and n in company:
            return f"компания в списке исключений: {name}"
    haystack = f" {_norm(v.title)} {_norm(v.company)} {_norm(v.description)} "
    for word in settings.get("stop_words") or []:
        w = _norm(word)
        if w and f" {w} " in haystack:
            return f"стоп-слово: {word}"
    return ""


def dedupe_key(v: Vacancy) -> str:
    """Одна и та же вакансия на разных площадках: совпадают должность и компания."""
    return f"{_norm(v.title)}|{_norm(v.company)}"
