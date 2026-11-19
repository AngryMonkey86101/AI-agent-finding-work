"""Поиск вакансий SuperJob по ключу приложения.

Документация: https://api.superjob.ru/
"""
from datetime import datetime, timezone

import httpx

from app.sources.base import Vacancy, find_email, html_to_text

API = "https://api.superjob.ru/2.0"

# Соответствие значений из панели (они в терминах hh.ru) параметрам SuperJob.
EXPERIENCE = {"noExperience": 1, "between1And3": 2, "between3And6": 3, "moreThan6": 4}
TYPE_OF_WORK = {"fullDay": 6, "shift": 12, "flyInFlyOut": 9}
EMPLOYMENT_TYPE = {"part": 10, "probation": 13}
CURRENCY = {"RUR": "rub", "USD": "usd", "EUR": "eur", "KZT": "kzt", "BYR": "byr"}


class SuperJobClient:
    def __init__(self, secret_key: str, client: httpx.Client | None = None):
        self.http = client or httpx.Client(timeout=30)
        self.http.headers["X-Api-App-Id"] = secret_key

    def search(self, settings: dict, period_days: int = 1, max_pages: int = 3) -> list[Vacancy]:
        positions = settings.get("positions") or []
        if not positions:
            return []
        base: list[tuple[str, str]] = [
            ("period", str(period_days)),
            ("count", "100"),
            ("order_field", "date"),
            ("order_direction", "desc"),
        ]
        if settings.get("city"):
            base.append(("town", settings["city"]))
        schedule = settings.get("schedule") or []
        if "remote" in schedule:
            base.append(("place_of_work", "2"))
        types = {TYPE_OF_WORK[s] for s in schedule if s in TYPE_OF_WORK}
        types |= {EMPLOYMENT_TYPE[e] for e in settings.get("employment") or [] if e in EMPLOYMENT_TYPE}
        for t in sorted(types):
            base.append(("type_of_work[]", str(t)))
        if settings.get("experience") in EXPERIENCE:
            base.append(("experience", str(EXPERIENCE[settings["experience"]])))
        if settings.get("salary_from"):
            base.append(("payment_from", str(settings["salary_from"])))
        if settings.get("only_with_salary"):
            base.append(("no_agreement", "1"))

        found: dict[str, Vacancy] = {}
        # SuperJob ищет по одному запросу, поэтому каждую должность отдельно.
        for position in positions:
            for page in range(max_pages):
                params = base + [("keyword", position), ("page", str(page))]
                r = self.http.get(f"{API}/vacancies/", params=params)
                r.raise_for_status()
                data = r.json()
                for item in data.get("objects", []):
                    v = self._parse(item)
                    found.setdefault(v.external_id, v)
                if not data.get("more"):
                    break
        return list(found.values())

    def details(self, vacancy: Vacancy) -> Vacancy:
        # Поиск SuperJob уже отдаёт полное описание.
        return vacancy

    @staticmethod
    def _parse(item: dict) -> Vacancy:
        description = html_to_text(item.get("vacancyRichText") or "") or "\n".join(
            filter(None, [item.get("candidat"), item.get("work"), item.get("compensation")])
        )
        published = item.get("date_published")
        currency = (item.get("currency") or "").upper()
        return Vacancy(
            source="superjob",
            external_id=str(item["id"]),
            title=item.get("profession", ""),
            company=item.get("firm_name", "") or (item.get("client") or {}).get("title", ""),
            url=item.get("link", ""),
            city=(item.get("town") or {}).get("title", ""),
            salary_from=item.get("payment_from") or None,
            salary_to=item.get("payment_to") or None,
            currency="RUR" if currency == "RUB" else currency,
            description=description,
            published_at=datetime.fromtimestamp(published, timezone.utc).isoformat() if published else "",
            contact_email=(item.get("email") or "").lower() or find_email(description),
        )
