import io
import re

import pytest
from fastapi.testclient import TestClient

AUTH = ("admin", "secret-pass")


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "agent.db")
    monkeypatch.setattr(config, "RESUME_DIR", tmp_path / "resumes")
    monkeypatch.setattr(config, "ADMIN_USER", AUTH[0])
    monkeypatch.setattr(config, "ADMIN_PASSWORD", AUTH[1])
    from app.main import app

    return TestClient(app)


def csrf(client) -> str:
    html = client.get("/", auth=AUTH).text
    return re.search(r'name="csrf" value="([0-9a-f]+)"', html).group(1)


def base_form(token: str, **over) -> dict:
    form = {
        "csrf": token,
        "positions": "Python-разработчик\nBackend-разработчик",
        "keywords": "FastAPI, PostgreSQL",
        "report_time": "18:00",
        "timezone": "Europe/Moscow",
        "currency": "RUR",
        "min_match_score": "60",
        "daily_limit": "20",
        "search_interval_minutes": "30",
        "schedule": ["remote"],
        "deepseek_api_key": "sk-test-1234",
        "platforms": ["hh"],
    }
    form.update(over)
    return form


def test_requires_password(client):
    assert client.get("/").status_code == 401
    assert client.get("/", auth=("admin", "wrong")).status_code == 401
    assert client.get("/", auth=AUTH).status_code == 200


def test_save_settings_and_keep_secret(client):
    token = csrf(client)
    r = client.post("/settings", data=base_form(token, report_time="19:30"), auth=AUTH, follow_redirects=False)
    assert r.status_code == 303

    from app import storage

    s = storage.load_settings()
    assert s["positions"] == ["Python-разработчик", "Backend-разработчик"]
    assert s["keywords"] == ["FastAPI", "PostgreSQL"]
    assert s["report_time"] == "19:30"
    assert s["schedule"] == ["remote"]
    assert s["deepseek_api_key"] == "sk-test-1234"

    # Пустое поле секрета не стирает сохранённый ключ, и ключ не попадает в HTML.
    client.post("/settings", data=base_form(token, deepseek_api_key=""), auth=AUTH)
    assert storage.load_settings()["deepseek_api_key"] == "sk-test-1234"
    html = client.get("/", auth=AUTH).text
    assert "sk-test-1234" not in html and "…1234" in html


def test_validation_errors(client):
    token = csrf(client)
    r = client.post("/settings", data=base_form(token, report_time="25:00", positions="", timezone="Mars/Base"), auth=AUTH)
    assert r.status_code == 422
    assert "ЧЧ:ММ" in r.text and "Обязательное поле" in r.text and "часовой пояс" in r.text


def test_csrf_required(client):
    r = client.post("/settings", data=base_form("bad"), auth=AUTH)
    assert r.status_code == 403


def make_docx() -> bytes:
    import docx

    d = docx.Document()
    d.add_paragraph("Иван Иванов")
    d.add_paragraph("Python-разработчик, 5 лет опыта")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def make_pdf() -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 750, "Ivan Ivanov - Python developer")
    c.save()
    return buf.getvalue()


@pytest.mark.parametrize("name,maker,expected", [
    ("cv.docx", make_docx, "Python-разработчик"),
    ("cv.pdf", make_pdf, "Python developer"),
])
def test_resume_upload(client, name, maker, expected):
    token = csrf(client)
    r = client.post("/resume", data={"csrf": token}, files={"resume": (name, maker())}, auth=AUTH, follow_redirects=False)
    assert r.status_code == 303
    from app import storage

    resume = storage.load_resume()
    assert resume["filename"] == name and expected in resume["text"]
    assert expected in client.get("/", auth=AUTH).text


def test_resume_replaces_old_file(client):
    from pathlib import Path

    from app import storage

    token = csrf(client)
    client.post("/resume", data={"csrf": token}, files={"resume": ("a.docx", make_docx())}, auth=AUTH)
    first = storage.load_resume()["stored_path"]
    client.post("/resume", data={"csrf": token}, files={"resume": ("b.pdf", make_pdf())}, auth=AUTH)
    assert not Path(first).exists()
    assert storage.load_resume()["filename"] == "b.pdf"


def test_resume_rejects_other_formats(client):
    token = csrf(client)
    r = client.post("/resume", data={"csrf": token}, files={"resume": ("cv.txt", b"hello")}, auth=AUTH)
    assert r.status_code == 422 and "PDF, DOC и DOCX" in r.text


def test_doc_extraction_if_converter_available(tmp_path):
    import shutil
    import subprocess

    office = shutil.which("soffice")
    if not office:
        pytest.skip("нет LibreOffice для создания тестового .doc")
    src = tmp_path / "cv.docx"
    src.write_bytes(make_docx())
    subprocess.run([office, "--headless", "--convert-to", "doc", "--outdir", str(tmp_path), str(src)],
                   capture_output=True, timeout=120)
    doc = tmp_path / "cv.doc"
    if not doc.exists():
        pytest.skip("LibreOffice не смог создать .doc")
    from app.resume import extract_text

    assert "Python-разработчик" in extract_text("cv.doc", doc.read_bytes())


def test_email_requires_smtp_when_enabled(client):
    token = csrf(client)
    r = client.post("/settings", data=base_form(token, email_enabled="on", smtp_user="bad"), auth=AUTH)
    assert r.status_code == 422 and "Нужно для отправки писем" in r.text and "адрес почты целиком" in r.text

    ok = base_form(token, email_enabled="on", smtp_host="smtp.yandex.ru", smtp_user="me@ya.ru",
                   smtp_password="app-pass", smtp_port="465")
    assert client.post("/settings", data=ok, auth=AUTH, follow_redirects=False).status_code == 303
    from app import storage

    s = storage.load_settings()
    assert s["email_enabled"] is True and s["smtp_port"] == 465 and s["smtp_password"] == "app-pass"


def test_superjob_needs_key(client):
    token = csrf(client)
    r = client.post("/settings", data=base_form(token, platforms=["hh", "superjob"]), auth=AUTH)
    assert r.status_code == 422 and "Нужен для поиска на SuperJob" in r.text
    r = client.post("/settings", data=base_form(token, platforms=[]), auth=AUTH)
    assert r.status_code == 422 and "хотя бы одну площадку" in r.text
    ok = base_form(token, platforms=["hh", "superjob"], superjob_secret_key="v3.r.sj", superjob_app_id="123")
    assert client.post("/settings", data=ok, auth=AUTH, follow_redirects=False).status_code == 303
    from app import storage

    assert storage.load_settings()["platforms"] == ["hh", "superjob"]


def test_vacancies_page_and_run(client, monkeypatch):
    from app import main, storage
    from app.sources.base import Vacancy

    storage.save_vacancy(Vacancy("hh", "1", "Python-разработчик", "Ромашка", "https://hh.ru/vacancy/1"),
                         "k1", "ready", score=85, reason="Совпадает стек", letter="Текст письма")
    html = client.get("/vacancies", auth=AUTH).text
    assert "Python-разработчик" in html and "85%" in html and "Текст письма" in html
    assert "Python-разработчик" not in client.get("/vacancies?status_filter=low_score", auth=AUTH).text

    called = []

    def fake_run():
        called.append(1)
        storage.set_state("last_run", {"by": "вручную из панели", "running": False, "text": "Найдено 3",
                                       "messages": ["hh.ru: ошибка поиска: 403"],
                                       "started_at": "2026-11-19T06:00:00+00:00",
                                       "finished_at": "2026-11-19T06:01:00+00:00"})

    monkeypatch.setattr(main, "_run_now", fake_run)
    token = csrf(client)
    assert client.post("/run", data={"csrf": "bad"}, auth=AUTH).status_code == 403
    r = client.post("/run", data={"csrf": token}, auth=AUTH, follow_redirects=False)
    assert r.status_code == 303 and called == [1]
    # Итог виден после обновления страницы, в том числе ошибка площадки.
    html = client.get("/vacancies", auth=AUTH).text
    assert "Найдено 3" in html and "ошибка поиска: 403" in html and "19.11 09:01" in html
    assert "Состояние" in html and "Ключ DeepSeek" in html


def test_running_search_survives_refresh(client):
    from datetime import datetime, timezone

    from app import storage

    storage.set_state("last_run", {"by": "по расписанию", "running": True, "text": "Поиск идёт…", "messages": [],
                                   "started_at": datetime.now(timezone.utc).isoformat()})
    html = client.get("/vacancies", auth=AUTH).text
    assert "Поиск идёт." in html and 'http-equiv="refresh"' in html and "Запустить поиск сейчас" not in html
    # Зависший больше 30 минут поиск не блокирует кнопку.
    storage.set_state("last_run", {"by": "по расписанию", "running": True, "text": "Поиск идёт…", "messages": [],
                                   "started_at": "2026-01-01T00:00:00+00:00"})
    html = client.get("/vacancies", auth=AUTH).text
    assert "Запустить поиск сейчас" in html and "прервался" in html


def test_proxy_validation(client):
    token = csrf(client)
    r = client.post("/settings", data=base_form(token, hh_proxy="1.2.3.4"), auth=AUTH)
    assert r.status_code == 422 and "socks5://" in r.text
    ok = base_form(token, hh_proxy="socks5://user:pw@1.2.3.4:1080")
    assert client.post("/settings", data=ok, auth=AUTH, follow_redirects=False).status_code == 303
    from app import storage

    assert storage.load_settings()["hh_proxy"] == "socks5://user:pw@1.2.3.4:1080"
    assert "user:pw" not in client.get("/", auth=AUTH).text


def test_check_hh_button(client, monkeypatch):
    from app.sources import hh

    monkeypatch.setattr(hh.HHClient, "check", lambda self: (_ for _ in ()).throw(hh.HHError("hh.ru ответил 403 (forbidden).")))
    token = csrf(client)
    r = client.post("/check-hh", data={"csrf": token}, auth=AUTH, follow_redirects=False)
    assert r.status_code == 303
    html = client.get("/vacancies", auth=AUTH).text
    assert "Проверка hh.ru" in html and "403" in html and "(напрямую)" in html
