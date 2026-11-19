"""Оценка вакансий и сопроводительные письма через DeepSeek (совместим с API OpenAI)."""
import json
import logging

import httpx

from app.sources.base import Vacancy

log = logging.getLogger(__name__)

API = "https://api.deepseek.com/chat/completions"
MAX_RESUME_CHARS = 12000
MAX_VACANCY_CHARS = 8000


class DeepSeekError(RuntimeError):
    pass


class DeepSeek:
    def __init__(self, api_key: str, model: str = "deepseek-chat", client: httpx.Client | None = None):
        if not api_key:
            raise DeepSeekError("Не задан ключ DeepSeek в панели настроек.")
        self.model = model or "deepseek-chat"
        self.http = client or httpx.Client(timeout=120)
        self.http.headers["Authorization"] = f"Bearer {api_key}"

    def _chat(self, system: str, user: str, as_json: bool, temperature: float) -> str:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
        }
        if as_json:
            body["response_format"] = {"type": "json_object"}
        r = self.http.post(API, json=body)
        if r.status_code >= 400:
            raise DeepSeekError(f"DeepSeek ответил {r.status_code}: {r.text[:300]}")
        return r.json()["choices"][0]["message"]["content"]

    def score(self, vacancy: Vacancy, resume: str, settings: dict) -> tuple[int, str]:
        """Оценка соответствия резюме вакансии от 0 до 100 и короткое объяснение."""
        system = (
            "Ты помогаешь соискателю отбирать вакансии. Сравни резюме и вакансию и оцени, "
            "насколько кандидат подходит и насколько вакансия соответствует его пожеланиям. "
            "Отвечай только JSON: {\"score\": целое 0-100, \"reason\": \"1-2 предложения по-русски\"}."
        )
        wishes = {
            "должности": settings.get("positions"),
            "ключевые слова": settings.get("keywords"),
            "город": settings.get("city"),
            "график": settings.get("schedule"),
            "зарплата от": settings.get("salary_from"),
        }
        user = (
            f"ПОЖЕЛАНИЯ:\n{json.dumps(wishes, ensure_ascii=False)}\n\n"
            f"РЕЗЮМЕ:\n{resume[:MAX_RESUME_CHARS]}\n\n"
            f"ВАКАНСИЯ:\n{_vacancy_text(vacancy)}"
        )
        raw = self._chat(system, user, as_json=True, temperature=0.1)
        try:
            data = json.loads(raw)
            score = max(0, min(100, int(data.get("score", 0))))
            return score, str(data.get("reason", "")).strip()
        except (ValueError, TypeError) as exc:
            raise DeepSeekError(f"Не удалось разобрать оценку: {raw[:200]}") from exc

    def letter(self, vacancy: Vacancy, resume: str, settings: dict, extra: str = "") -> str:
        """Сопроводительное письмо под конкретную вакансию."""
        lang = "на английском языке" if settings.get("letter_language") == "en" else "на русском языке"
        system = (
            f"Ты пишешь сопроводительные письма {lang} от лица соискателя. "
            "Используй только факты из резюме, ничего не выдумывай. Обращайся к конкретной вакансии и компании. "
            "Без приветствия-шаблона вроде «Уважаемый HR», без markdown, без подписи (её добавят отдельно). "
            "Верни только текст письма."
        )
        user = (
            f"ПОЖЕЛАНИЯ К ПИСЬМУ: {settings.get('letter_instructions') or 'коротко и по делу'}\n"
            + (f"ДОПОЛНИТЕЛЬНО: {extra}\n" if extra else "")
            + f"\nРЕЗЮМЕ:\n{resume[:MAX_RESUME_CHARS]}\n\nВАКАНСИЯ:\n{_vacancy_text(vacancy)}"
        )
        text = self._chat(system, user, as_json=False, temperature=0.7).strip()
        signature = (settings.get("letter_signature") or "").strip()
        return f"{text}\n\n{signature}" if signature else text


def _vacancy_text(v: Vacancy) -> str:
    parts = [
        f"Должность: {v.title}",
        f"Компания: {v.company}",
        f"Город: {v.city}",
        f"Зарплата: {v.salary_text()}",
    ]
    if v.skills:
        parts.append("Навыки: " + ", ".join(v.skills))
    parts.append(f"Описание:\n{v.description[:MAX_VACANCY_CHARS]}")
    return "\n".join(parts)
