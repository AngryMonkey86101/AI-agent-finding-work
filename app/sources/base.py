"""Общая модель вакансии и вспомогательные функции для площадок."""
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


@dataclass
class Vacancy:
    source: str  # hh | superjob
    external_id: str
    title: str
    company: str
    url: str
    city: str = ""
    salary_from: int | None = None
    salary_to: int | None = None
    currency: str = ""
    description: str = ""
    published_at: str = ""
    contact_email: str = ""
    skills: list[str] = field(default_factory=list)
    salary_label: str = ""  # готовая строка зарплаты, если вакансия восстановлена из базы

    def salary_text(self) -> str:
        if self.salary_label:
            return self.salary_label
        if not self.salary_from and not self.salary_to:
            return "не указана"
        parts = []
        if self.salary_from:
            parts.append(f"от {self.salary_from:,}".replace(",", " "))
        if self.salary_to:
            parts.append(f"до {self.salary_to:,}".replace(",", " "))
        symbol = {"RUR": "₽", "RUB": "₽", "USD": "$", "EUR": "€", "KZT": "₸"}.get(self.currency, self.currency)
        return " ".join(parts) + (f" {symbol}" if symbol else "")


class _TextExtractor(HTMLParser):
    BLOCK = {"p", "br", "li", "div", "ul", "ol", "h1", "h2", "h3", "h4", "tr"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("• ")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(html: str) -> str:
    if not html:
        return ""
    parser = _TextExtractor()
    parser.feed(html)
    text = "".join(parser.parts)
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def find_email(*texts: str) -> str:
    for text in texts:
        if not text:
            continue
        match = EMAIL_RE.search(text)
        if match:
            return match.group(0).rstrip(".").lower()
    return ""


def from_row(row: dict) -> Vacancy:
    """Восстанавливает вакансию из строки таблицы vacancies."""
    return Vacancy(
        source=row["source"], external_id=row["external_id"], title=row["title"], company=row["company"],
        url=row["url"], city=row.get("city") or "", description=row.get("description") or "",
        published_at=row.get("published_at") or "", contact_email=row.get("contact_email") or "",
        salary_label=row.get("salary") or "",
    )
