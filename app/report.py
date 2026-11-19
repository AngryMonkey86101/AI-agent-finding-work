"""Вечерний отчёт о проделанной работе."""
import html
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app import storage
from app.pipeline import local_midnight_utc

SOURCE_NAMES = {"hh": "hh.ru", "superjob": "SuperJob"}
MAX_LIST = 15


def esc(text) -> str:
    return html.escape(str(text or ""), quote=False)


def local_now(settings: dict, now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(ZoneInfo(settings.get("timezone") or "Europe/Moscow"))


def is_due(settings: dict, last_sent_date: str | None, now: datetime | None = None) -> bool:
    """Пора ли слать отчёт: время настало, и сегодня (по местному времени) его ещё не было."""
    local = local_now(settings, now)
    try:
        hour, minute = (int(x) for x in (settings.get("report_time") or "18:00").split(":"))
    except ValueError:
        hour, minute = 18, 0
    if (local.hour, local.minute) < (hour, minute):
        return False
    return last_sent_date != local.date().isoformat()


def _link(v: dict) -> str:
    return f"<a href=\"{html.escape(v['url'])}\">{esc(v['title'])}</a> — {esc(v['company'])}"


def _list(items: list[dict], extra=None, max_list: int = MAX_LIST) -> list[str]:
    lines = []
    for v in items[:max_list]:
        line = f"• {_link(v)}"
        if extra:
            line += extra(v)
        lines.append(line)
    if len(items) > max_list:
        lines.append(f"…и ещё {len(items) - max_list}")
    return lines


def build(settings: dict, now: datetime | None = None) -> str:
    """Текст отчёта. Списки сокращаются, чтобы влезть в одно сообщение Telegram."""
    for max_list in (MAX_LIST, 8, 4, 0):
        text = _build(settings, now, max_list)
        if len(text) <= 4000:
            return text
    return text


def _build(settings: dict, now: datetime | None, max_list: int) -> str:
    since = local_midnight_utc(settings.get("timezone") or "Europe/Moscow", now)
    found = storage.found_since(since)
    acted = storage.acted_since(since)
    waiting = storage.list_vacancies("ready", limit=500)
    by_status = Counter(v["status"] for v in found)
    by_source = Counter(v["source"] for v in found)
    passed = sum(by_status[s] for s in storage.PASSED)

    applied = [v for v in acted if v["status"] == "applied"]
    emailed = [v for v in acted if v["status"] == "emailed"]
    skipped = [v for v in acted if v["status"] == "skipped"]

    date = local_now(settings, now).strftime("%d.%m")
    out = [f"<b>Отчёт за {date}</b>", ""]

    if found:
        sources = ", ".join(f"{SOURCE_NAMES.get(k, k)} {n}" for k, n in by_source.most_common())
        out.append(f"🔎 Новых вакансий: <b>{len(found)}</b> ({sources})")
        out.append(f"   подошли: {passed}, не подошли по оценке: {by_status['low_score']}, "
                   f"отсеяно фильтрами: {by_status['filtered']}, дубли: {by_status['duplicate']}")
        if by_status["error"]:
            out.append(f"   ⚠️ с ошибкой: {by_status['error']} (будут перепроверены)")
        scores = [v["score"] for v in found if v["status"] in storage.PASSED and v["score"] is not None]
        if scores:
            out.append(f"   средняя оценка подошедших: {round(sum(scores) / len(scores))}%")
    else:
        out.append("🔎 Новых вакансий сегодня не было.")
    limit = int(settings.get("daily_limit") or 0)
    if limit:
        out.append(f"   дневной лимит: {passed} из {limit}")
    out.append("")

    out.append(f"✅ Откликов на сайтах: <b>{len(applied)}</b>")
    out += _list(applied, max_list=max_list)
    out.append(f"📧 Отправлено на email: <b>{len(emailed)}</b>")
    out += _list(emailed, lambda v: f" ({esc(v.get('contact_email'))})", max_list)
    if skipped:
        out.append(f"⏭ Пропущено: {len(skipped)}")
    out.append("")

    if waiting:
        out.append(f"⏳ Ждут вашего решения: <b>{len(waiting)}</b>")
        out += _list(sorted(waiting, key=lambda v: -(v["score"] or 0)), lambda v: f" · {v['score']}%", max_list)
    else:
        out.append("⏳ Все карточки разобраны.")

    if settings.get("paused"):
        out += ["", "⏸ Агент на паузе, новые вакансии не ищутся."]

    return "\n".join(out).strip()
