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
