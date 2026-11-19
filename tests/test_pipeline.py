import json

import httpx
import pytest

from app.sources.base import Vacancy, find_email, html_to_text


@pytest.fixture
def db(tmp_path, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "agent.db")
    monkeypatch.setattr(config, "RESUME_DIR", tmp_path / "resumes")
    from app import storage

    storage.save_resume("cv.pdf", str(tmp_path / "cv.pdf"), "Иван, Python, FastAPI, 5 лет опыта")
    return storage


# ---------- hh.ru

HH_LIST = {
    "pages": 1,
    "items": [
        {"id": "101", "name": "Python-разработчик", "employer": {"name": "Ромашка"},
         "alternate_url": "https://hh.ru/vacancy/101", "area": {"name": "Москва"},
         "salary": {"from": 200000, "to": None, "currency": "RUR"},
         "snippet": {"requirement": "<highlighttext>Python</highlighttext>, FastAPI", "responsibility": "API"},
         "published_at": "2026-11-19T08:00:00+0300"},
    ],
}
HH_DETAIL = {
    "id": "101", "description": "<p>Пишем на <b>Python</b>.</p><ul><li>FastAPI</li></ul><p>Резюме: jobs@romashka.ru</p>",
    "key_skills": [{"name": "Python"}, {"name": "FastAPI"}], "contacts": None,
}


def hh_transport(seen: list):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/suggests/areas":
            return httpx.Response(200, json={"items": [{"id": "1", "text": "Москва"}]})
        if request.url.path == "/vacancies":
            return httpx.Response(200, json=HH_LIST)
        if request.url.path == "/vacancies/101":
            return httpx.Response(200, json=HH_DETAIL)
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def test_hh_search_builds_query_and_parses():
    from app.sources.hh import HHClient

    seen: list = []
    client = HHClient(httpx.Client(transport=hh_transport(seen)))
    settings = {"positions": ["Python-разработчик", "Backend"], "city": "Москва", "schedule": ["remote", "fullDay"],
                "employment": ["full"], "experience": "between3And6", "salary_from": 150000, "currency": "RUR"}
    items = client.search(settings)
    q = seen[-1].url.params
    assert q["text"] == '"Python-разработчик" OR "Backend"'
    assert q["area"] == "1" and q.get_list("schedule") == ["remote", "fullDay"]
    assert q["experience"] == "between3And6" and q["salary"] == "150000"
    assert seen[-1].headers["HH-User-Agent"]
    v = items[0]
    assert (v.title, v.company, v.salary_from, v.url) == ("Python-разработчик", "Ромашка", 200000, "https://hh.ru/vacancy/101")

    v = client.details(v)
    assert "Пишем на Python." in v.description and "• FastAPI" in v.description
    assert v.skills == ["Python", "FastAPI"] and v.contact_email == "jobs@romashka.ru"


# ---------- SuperJob

SJ_PAGE = {
    "more": False,
    "objects": [
        {"id": 555, "profession": "Python developer", "firm_name": "Лютик", "link": "https://www.superjob.ru/vakansii/555.html",
         "town": {"title": "Москва"}, "payment_from": 180000, "payment_to": 250000, "currency": "rub",
         "vacancyRichText": "<p>Django, PostgreSQL</p>", "date_published": 1763535600, "email": "HR@lutik.ru"},
    ],
}


def test_superjob_search():
    from app.sources.superjob import SuperJobClient

    seen: list = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=SJ_PAGE)

    client = SuperJobClient("v3.secret", httpx.Client(transport=httpx.MockTransport(handler)))
    items = client.search({"positions": ["Python", "Backend"], "schedule": ["remote", "fullDay"],
                           "experience": "between1And3", "only_with_salary": True, "city": "Москва"})
    assert len(seen) == 2  # по запросу на каждую должность
    assert seen[0].headers["X-Api-App-Id"] == "v3.secret"
    q = seen[0].url.params
    assert q["keyword"] == "Python" and q["place_of_work"] == "2" and q["experience"] == "2"
    assert q.get_list("type_of_work[]") == ["6"] and q["no_agreement"] == "1" and q["town"] == "Москва"
    assert len(items) == 1  # одинаковые id не дублируются
    v = items[0]
    assert (v.source, v.company, v.currency, v.contact_email) == ("superjob", "Лютик", "RUR", "hr@lutik.ru")
    assert v.description == "Django, PostgreSQL"


# ---------- фильтры и утилиты

def test_filters():
    from app.filters import dedupe_key, stop_reason

    v = Vacancy("hh", "1", "Python-разработчик (1С)", "ООО Ёлка", "u", description="продажи не нужны")
    assert "стоп-слово" in stop_reason(v, {"stop_words": ["1с"]})
    assert "компания" in stop_reason(v, {"excluded_companies": ["елка"]})
    assert stop_reason(v, {"stop_words": ["java"]}) == ""
    assert stop_reason(Vacancy("hh", "2", "Python", "X", "u", description="Javascript"), {"stop_words": ["java"]}) == ""
    a = Vacancy("hh", "1", "Python-разработчик", "Ромашка", "u")
    b = Vacancy("superjob", "9", "Python разработчик", "РОМАШКА", "u")
    assert dedupe_key(a) == dedupe_key(b)


def test_html_and_email():
    assert html_to_text("<p>a</p><ul><li>b</li></ul>") == "a\n• b"
    assert find_email("пишите на Job@Mail.ru.") == "job@mail.ru"


# ---------- DeepSeek

def deepseek_transport(calls: list, score: int = 80):
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if body.get("response_format"):
            content = json.dumps({"score": score, "reason": "Совпадает стек"})
        else:
            content = "Здравствуйте! Мой опыт с FastAPI подходит под вашу задачу."
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    return httpx.MockTransport(handler)


def make_llm(calls, score=80):
    from app.deepseek import DeepSeek

    return DeepSeek("sk-x", "deepseek-chat", httpx.Client(transport=deepseek_transport(calls, score)))


def test_deepseek_score_and_letter():
    calls: list = []
    llm = make_llm(calls)
    v = Vacancy("hh", "1", "Python", "Ромашка", "u", description="FastAPI")
    assert llm.score(v, "резюме", {"positions": ["Python"]}) == (80, "Совпадает стек")
    letter = llm.letter(v, "резюме", {"letter_signature": "Иван\n+7 900"})
    assert letter.endswith("Иван\n+7 900") and "FastAPI" in letter
    assert calls[0]["model"] == "deepseek-chat" and "Ромашка" in calls[1]["messages"][1]["content"]


# ---------- проход целиком

class FakeSource:
    def __init__(self, items):
        self.items = items
        self.detailed = []

    def search(self, settings):
        return list(self.items)

    def details(self, v):
        self.detailed.append(v.external_id)
        return v


def settings(**over):
    s = {"positions": ["Python"], "min_match_score": 60, "daily_limit": 20, "timezone": "Europe/Moscow",
         "stop_words": ["1С"], "excluded_companies": ["Плохая"]}
    s.update(over)
    return s


def test_run_once_full_flow(db):
    from app.pipeline import run_once

    hh = FakeSource([
        Vacancy("hh", "1", "Python-разработчик", "Ромашка", "u1"),
        Vacancy("hh", "2", "Программист 1С", "Ромашка", "u2"),
        Vacancy("hh", "3", "Python", "Плохая компания", "u3"),
    ])
    sj = FakeSource([Vacancy("superjob", "9", "Python разработчик", "РОМАШКА", "u9")])
    calls: list = []
    stats = run_once(settings(), [hh, sj], llm=make_llm(calls))
    assert (stats.found, stats.ready, stats.filtered, stats.duplicates) == (4, 1, 2, 1)
    ready = db.list_vacancies("ready")
    assert len(ready) == 1 and ready[0]["score"] == 80 and "FastAPI" in ready[0]["letter"]

    # Повторный проход не трогает уже обработанные вакансии и не тратит запросы к DeepSeek.
    n = len(calls)
    stats = run_once(settings(), [hh, sj], llm=make_llm(calls))
    assert stats.new == 0 and len(calls) == n


def test_run_once_low_score_and_limit(db):
    from app.pipeline import run_once

    src = FakeSource([Vacancy("hh", str(i), f"Python {i}", f"Компания {i}", "u") for i in range(5)])
    stats = run_once(settings(min_match_score=90), [src], llm=make_llm([], score=50))
    assert stats.low_score == 5 and stats.ready == 0

    src2 = FakeSource([Vacancy("hh", str(i), f"Go {i}", f"Фирма {i}", "u") for i in range(10, 15)])
    stats = run_once(settings(daily_limit=2), [src2], llm=make_llm([]))
    assert stats.ready == 2 and stats.limit_reached
    # Вакансии сверх лимита не сохранены и будут разобраны позже.
    assert len(db.list_vacancies("ready")) == 2 and not db.vacancy_seen("hh", "14")


def test_run_once_errors_are_retried(db):
    from app.deepseek import DeepSeek
    from app.pipeline import run_once

    def failing(request):
        return httpx.Response(500, text="boom")

    bad = DeepSeek("sk", client=httpx.Client(transport=httpx.MockTransport(failing)))
    src = FakeSource([Vacancy("hh", "1", "Python", "Ромашка", "u")])
    stats = run_once(settings(), [src], llm=bad)
    assert stats.errors == 1 and db.list_vacancies("error")
    stats = run_once(settings(), [src], llm=make_llm([]))
    assert stats.ready == 1


def test_run_once_without_resume(tmp_path, monkeypatch):
    from app import config
    from app.pipeline import run_once

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "agent.db")
    stats = run_once(settings(), [FakeSource([])], llm=make_llm([]))
    assert "Резюме не загружено" in stats.messages[0]


def test_source_failure_does_not_stop_others(db):
    from app.pipeline import run_once

    class Broken:
        def search(self, s):
            raise httpx.ConnectError("нет сети")

    stats = run_once(settings(), [Broken(), FakeSource([Vacancy("hh", "1", "Python", "Р", "u")])], llm=make_llm([]))
    assert stats.ready == 1 and "ошибка поиска" in stats.messages[0]


def test_hh_403_is_explained():
    from app.sources.hh import HHClient, HHError

    def handler(request):
        return httpx.Response(403, json={"errors": [{"type": "forbidden"}], "description": "Forbidden"})

    client = HHClient(httpx.Client(transport=httpx.MockTransport(handler)), contact="me@x.ru", app_token="tok")
    with pytest.raises(HHError) as err:
        client.search({"positions": ["Python"]})
    assert "403" in str(err.value) and "forbidden" in str(err.value) and "Прокси для hh.ru" in str(err.value)


def test_hh_headers_from_settings(monkeypatch):
    from app.sources.hh import HHClient

    monkeypatch.delenv("HH_USER_AGENT", raising=False)
    seen = []
    client = HHClient(httpx.Client(transport=hh_transport(seen)), contact="me@x.ru", app_token="tok")
    client.search({"positions": ["Python"]})
    assert seen[-1].headers["HH-User-Agent"] == "AI-agent-finding-work/0.3 (me@x.ru)"
    assert seen[-1].headers["Authorization"] == "Bearer tok"


def test_hh_proxy_is_used(monkeypatch):
    from app.sources import hh

    captured = {}
    real = httpx.Client

    def fake_client(*args, **kwargs):
        captured.update(kwargs)
        return real(transport=hh_transport([]))

    monkeypatch.setattr(hh.httpx, "Client", fake_client)
    hh.HHClient(proxy=" socks5://u:p@1.2.3.4:1080 ")
    assert captured["proxy"] == "socks5://u:p@1.2.3.4:1080"
    hh.HHClient(proxy="")
    assert captured["proxy"] is None


def test_hh_check():
    from app.sources.hh import HHClient

    def handler(request):
        return httpx.Response(200, json={"found": 1234, "items": []})

    client = HHClient(httpx.Client(transport=httpx.MockTransport(handler)))
    assert "1234" in client.check()
