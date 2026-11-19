"""Публичный поиск вакансий hh.ru (без входа соискателя).

Документация: https://api.hh.ru/openapi/redoc
hh.ru просит указывать User-Agent с названием приложения и контактом,
его можно задать переменной окружения HH_USER_AGENT.
"""
import logging
import os

import httpx

from app.sources.base import Vacancy, find_email, html_to_text

log = logging.getLogger(__name__)

API = "https://api.hh.ru"
APP_NAME = "AI-agent-finding-work/0.3"


class HHError(RuntimeError):
    pass


def user_agent(contact: str = "") -> str:
    """hh.ru требует User-Agent вида «Название/версия (контакт)»."""
    override = os.getenv("HH_USER_AGENT", "").strip()
    if override:
        return override
    contact = (contact or "").strip()
    return f"{APP_NAME} ({contact})" if contact else APP_NAME


def explain(r: httpx.Response) -> str:
    """Понятное описание ошибки hh.ru по телу ответа."""
    try:
        data = r.json()
    except ValueError:
        data = {}
    kinds = [f"{e.get('type')}:{e.get('value')}" if e.get("value") else str(e.get("type"))
             for e in data.get("errors") or []]
    detail = ", ".join(kinds) or data.get("description") or r.text[:200].strip()
    hint = ""
    if r.status_code == 403 or "forbidden" in detail:
        hint = (" hh.ru не пускает запросы с IP этого сервера. Укажите в панели «Прокси для hh.ru» "
                "с российским IP.")
    if "bad_user_agent" in detail:
        hint = " hh.ru не принял User-Agent: укажите в панели email для hh.ru."
    if "captcha" in detail:
        hint = " hh.ru просит капчу для этого IP."
    return f"hh.ru ответил {r.status_code} ({detail}).{hint}"


class HHClient:
    def __init__(self, client: httpx.Client | None = None, contact: str = "", app_token: str = "",
                 proxy: str = ""):
        proxy = (proxy or "").strip() or None
        self.http = client or httpx.Client(timeout=30, proxy=proxy)
        ua = user_agent(contact)
        self.http.headers["HH-User-Agent"] = ua
        self.http.headers["User-Agent"] = ua
        if app_token:
            self.http.headers["Authorization"] = f"Bearer {app_token.strip()}"
        self._areas: dict[str, str | None] = {}

    def _get(self, path: str, params=None) -> dict:
        r = self.http.get(f"{API}{path}", params=params)
        if r.status_code >= 400:
            raise HHError(explain(r))
        return r.json()

    def check(self) -> str:
        """Пробный запрос: доступен ли hh.ru с этого сервера (и через прокси, если он задан)."""
        data = self._get("/vacancies", {"text": "python", "per_page": "1"})
        return f"hh.ru доступен, по запросу «python» сейчас {data.get('found', 0)} вакансий."

    def area_id(self, city: str) -> str | None:
        """Находит id региона по названию города. None, если не нашли."""
        city = (city or "").strip()
        if not city:
            return None
        if city not in self._areas:
            items = self._get("/suggests/areas", {"text": city}).get("items", [])
            exact = [i for i in items if i.get("text", "").lower() == city.lower()]
            pick = (exact or items or [None])[0]
            self._areas[city] = pick["id"] if pick else None
            if not pick:
                log.warning("hh.ru: город %r не найден, ищу без города", city)
        return self._areas[city]

    def search(self, settings: dict, period_days: int = 1, max_pages: int = 3) -> list[Vacancy]:
        positions = settings.get("positions") or []
        if not positions:
            return []
        params: list[tuple[str, str]] = [
            ("text", " OR ".join(f'"{p}"' for p in positions)),
            ("search_field", "name"),
            ("order_by", "publication_time"),
            ("period", str(period_days)),
            ("per_page", "50"),
        ]
        area = self.area_id(settings.get("city", ""))
        if area:
            params.append(("area", area))
        for s in settings.get("schedule") or []:
            params.append(("schedule", s))
        for e in settings.get("employment") or []:
            params.append(("employment", e))
        if settings.get("experience"):
            params.append(("experience", settings["experience"]))
        if settings.get("salary_from"):
            params.append(("salary", str(settings["salary_from"])))
            params.append(("currency", settings.get("currency") or "RUR"))
        if settings.get("only_with_salary"):
            params.append(("only_with_salary", "true"))

        found: list[Vacancy] = []
        for page in range(max_pages):
            data = self._get("/vacancies", params + [("page", str(page))])
            found.extend(self._short(item) for item in data.get("items", []))
            if page + 1 >= data.get("pages", 0):
                break
        return found

    def details(self, vacancy: Vacancy) -> Vacancy:
        """Догружает полное описание, навыки и контакты."""
        d = self._get(f"/vacancies/{vacancy.external_id}")
        vacancy.description = html_to_text(d.get("description", ""))
        vacancy.skills = [s["name"] for s in d.get("key_skills") or []]
        contacts = d.get("contacts") or {}
        vacancy.contact_email = (contacts.get("email") or "").lower() or find_email(vacancy.description)
        return vacancy

    @staticmethod
    def _short(item: dict) -> Vacancy:
        salary = item.get("salary") or {}
        snippet = item.get("snippet") or {}
        return Vacancy(
            source="hh",
            external_id=str(item["id"]),
            title=item.get("name", ""),
            company=(item.get("employer") or {}).get("name", ""),
            url=item.get("alternate_url") or f"https://hh.ru/vacancy/{item['id']}",
            city=(item.get("area") or {}).get("name", ""),
            salary_from=salary.get("from"),
            salary_to=salary.get("to"),
            currency=salary.get("currency") or "",
            description=html_to_text(" ".join(filter(None, [snippet.get("requirement"), snippet.get("responsibility")]))),
            published_at=item.get("published_at", ""),
        )
