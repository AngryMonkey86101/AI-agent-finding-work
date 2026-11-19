"""Веб-панель настроек агента."""
import hashlib
import hmac
import secrets
import uuid
from pathlib import Path

import threading

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from starlette.datastructures import UploadFile

from app import config, storage
from app.resume import ALLOWED_EXTENSIONS, ResumeError, extract_text
from app.settings_schema import FIELDS, GROUPS, defaults, for_display, parse_form

app = FastAPI(title="AI-агент поиска работы", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
security = HTTPBasic(realm="Job agent")


def require_admin(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    if not config.ADMIN_PASSWORD:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Задайте ADMIN_PASSWORD в файле .env и перезапустите сервис.",
        )
    ok_user = secrets.compare_digest(credentials.username.encode(), config.ADMIN_USER.encode())
    ok_pass = secrets.compare_digest(credentials.password.encode(), config.ADMIN_PASSWORD.encode())
    if not (ok_user and ok_pass):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Неверный логин или пароль",
            headers={"WWW-Authenticate": 'Basic realm="Job agent"'},
        )
    return credentials.username


def csrf_token() -> str:
    return hmac.new(config.ADMIN_PASSWORD.encode(), b"csrf", hashlib.sha256).hexdigest()


def check_csrf(token: str | None) -> None:
    if not token or not hmac.compare_digest(token, csrf_token()):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Форма устарела, обновите страницу.")


def render(request: Request, values: dict, errors: dict | None = None, notice: str = "",
           resume_error: str = "", code: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "groups": GROUPS,
            "fields": FIELDS,
            "values": values,
            "errors": errors or {},
            "notice": notice,
            "resume": storage.load_resume(),
            "resume_error": resume_error,
            "accept": ",".join(sorted(ALLOWED_EXTENSIONS)),
            "csrf": csrf_token(),
            "display": for_display,
        },
        status_code=code,
    )


def current_settings() -> dict:
    values = defaults()
    values.update(storage.load_settings())
    return values


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def settings_page(request: Request, saved: str = "", _: str = Depends(require_admin)):
    notice = {"settings": "Настройки сохранены.", "resume": "Резюме загружено."}.get(saved, "")
    return render(request, current_settings(), notice=notice)


@app.post("/settings", response_class=HTMLResponse)
async def save_settings(request: Request, _: str = Depends(require_admin)):
    form = await request.form()
    check_csrf(form.get("csrf"))
    current = current_settings()
    values, errors = parse_form(form, current)
    if errors:
        merged = {**current, **values}
        return render(request, merged, errors=errors, code=422)
    storage.save_settings(values)
    return RedirectResponse("/?saved=settings", status_code=303)


@app.post("/resume", response_class=HTMLResponse)
async def upload_resume(request: Request, _: str = Depends(require_admin)):
    form = await request.form()
    check_csrf(form.get("csrf"))
    upload = form.get("resume")
    if not isinstance(upload, UploadFile) or not upload.filename:
        return render(request, current_settings(), resume_error="Выберите файл.", code=422)

    data = await upload.read(config.MAX_RESUME_BYTES + 1)
    if len(data) > config.MAX_RESUME_BYTES:
        return render(request, current_settings(), resume_error="Файл больше 10 МБ.", code=422)
    try:
        text = extract_text(upload.filename, data)
    except ResumeError as exc:
        return render(request, current_settings(), resume_error=str(exc), code=422)

    config.ensure_dirs()
    ext = Path(upload.filename).suffix.lower()
    stored = config.RESUME_DIR / f"{uuid.uuid4().hex}{ext}"
    stored.write_bytes(data)
    old = storage.load_resume()
    storage.save_resume(Path(upload.filename).name, str(stored), text)
    if old and old["stored_path"] != str(stored):
        Path(old["stored_path"]).unlink(missing_ok=True)
    return RedirectResponse("/?saved=resume", status_code=303)


_run_lock = threading.Lock()
_last_run: dict = {}


def _run_now() -> None:
    from app.pipeline import build_sources, run_once

    if not _run_lock.acquire(blocking=False):
        return
    try:
        settings = current_settings()
        stats = run_once(settings, build_sources(settings))
        _last_run.clear()
        _last_run.update(
            text=(f"Найдено {stats.found}, новых {stats.new}, отсеяно {stats.filtered}, дублей {stats.duplicates}, "
                  f"не подошло {stats.low_score}, готово {stats.ready}, ошибок {stats.errors}."),
            messages=stats.messages,
        )
    except Exception as exc:  # noqa: BLE001
        _last_run.clear()
        _last_run.update(text=f"Проход завершился ошибкой: {exc}", messages=[])
    finally:
        _run_lock.release()


@app.get("/vacancies", response_class=HTMLResponse)
def vacancies_page(request: Request, status_filter: str = "", _: str = Depends(require_admin)):
    items = storage.list_vacancies(status_filter or None, limit=200)
    return templates.TemplateResponse(
        request,
        "vacancies.html",
        {
            "items": items,
            "labels": storage.STATUS_LABELS,
            "status_filter": status_filter,
            "running": _run_lock.locked(),
            "last_run": _last_run,
            "csrf": csrf_token(),
        },
    )


@app.post("/run")
async def run_now(request: Request, background: BackgroundTasks, _: str = Depends(require_admin)):
    form = await request.form()
    check_csrf(form.get("csrf"))
    if not _run_lock.locked():
        background.add_task(_run_now)
    return RedirectResponse("/vacancies", status_code=303)
