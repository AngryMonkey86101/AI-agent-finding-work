"""Один проход агента: найти новые вакансии, отсеять, оценить, написать письма."""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app import storage
from app.deepseek import DeepSeek, DeepSeekError
from app.filters import dedupe_key, stop_reason
from app.sources.base import Vacancy

log = logging.getLogger(__name__)


@dataclass
class RunStats:
    found: int = 0
    new: int = 0
    filtered: int = 0
    duplicates: int = 0
    low_score: int = 0
    ready: int = 0
    errors: int = 0
    limit_reached: bool = False
    messages: list[str] = field(default_factory=list)
    ready_ids: list[int] = field(default_factory=list)


def local_midnight_utc(tz_name: str, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(ZoneInfo(tz_name or "Europe/Moscow"))
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(timezone.utc).isoformat(timespec="seconds")


def build_sources(settings: dict, hh_client=None, sj_client=None) -> list:
    from app.sources.hh import HHClient
    from app.sources.superjob import SuperJobClient

    sources = []
    platforms = settings.get("platforms") or ["hh"]
    if "hh" in platforms:
        sources.append(hh_client or HHClient(contact=settings.get("hh_contact_email", ""),
                                             proxy=settings.get("hh_proxy", ""),
                                             app_token=settings.get("hh_app_token", "")))
    if "superjob" in platforms and settings.get("superjob_secret_key"):
        sources.append(sj_client or SuperJobClient(settings["superjob_secret_key"]))
    return sources


def run_once(settings: dict, sources: list, llm: DeepSeek | None = None,
             now: datetime | None = None) -> RunStats:
    stats = RunStats()
    resume = storage.load_resume()
    if not resume:
        stats.messages.append("Резюме не загружено, оценка и письма невозможны.")
        return stats
    if llm is None:
        try:
            llm = DeepSeek(settings.get("deepseek_api_key", ""), settings.get("deepseek_model", ""))
        except DeepSeekError as exc:
            stats.messages.append(str(exc))
            return stats

    since = local_midnight_utc(settings.get("timezone", "Europe/Moscow"), now)
    budget = int(settings.get("daily_limit") or 20) - storage.count_passed_since(since)
    threshold = int(settings.get("min_match_score") or 0)

    candidates: list[tuple[object, Vacancy]] = []
    for source in sources:
        name = {"HHClient": "hh.ru", "SuperJobClient": "SuperJob"}.get(type(source).__name__, type(source).__name__)
        try:
            items = source.search(settings)
        except Exception as exc:  # noqa: BLE001 - сеть, лимиты, ошибки площадки
            log.error("Поиск %s упал: %s", name, exc)
            stats.messages.append(f"{name}: ошибка поиска: {exc}")
            continue
        stats.found += len(items)
        stats.messages.append(f"{name}: найдено {len(items)}")
        candidates.extend((source, v) for v in items)

    seen_keys: set[str] = set()
    for source, v in candidates:
        if storage.vacancy_seen(v.source, v.external_id):
            continue
        stats.new += 1
        key = dedupe_key(v)
        if key in seen_keys or storage.dedupe_exists(key):
            storage.save_vacancy(v, key, "duplicate")
            stats.duplicates += 1
            continue
        seen_keys.add(key)

        reason = stop_reason(v, settings)
        if reason:
            storage.save_vacancy(v, key, "filtered", note=reason)
            stats.filtered += 1
            continue

        if budget <= 0:
            # Не сохраняем: вакансия вернётся в поиск завтра, когда лимит обновится.
            stats.limit_reached = True
            continue

        try:
            v = source.details(v)
            reason = stop_reason(v, settings)  # в полном описании могли появиться стоп-слова
            if reason:
                storage.save_vacancy(v, key, "filtered", note=reason)
                stats.filtered += 1
                continue
            score, why = llm.score(v, resume["text"], settings)
            if score < threshold:
                storage.save_vacancy(v, key, "low_score", score=score, reason=why)
                stats.low_score += 1
                continue
            letter = llm.letter(v, resume["text"], settings)
        except Exception as exc:  # noqa: BLE001
            log.exception("Обработка вакансии %s:%s упала", v.source, v.external_id)
            storage.save_vacancy(v, key, "error", note=str(exc)[:500])
            stats.errors += 1
            continue

        vid = storage.save_vacancy(v, key, "ready", score=score, reason=why, letter=letter)
        stats.ready += 1
        stats.ready_ids.append(vid)
        budget -= 1

    if stats.limit_reached:
        stats.messages.append("Дневной лимит достигнут, остальные вакансии разберу завтра.")
    return stats


def summary(stats: RunStats) -> str:
    return (f"Найдено {stats.found}, новых {stats.new}, отсеяно {stats.filtered}, дублей {stats.duplicates}, "
            f"не подошло {stats.low_score}, готово {stats.ready}, ошибок {stats.errors}.")


def run_and_record(settings: dict, by: str) -> RunStats | None:
    """Проход с записью хода и итога в базу, чтобы панель видела его после обновления страницы."""
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    storage.set_state("last_run", {"by": by, "started_at": started, "running": True,
                                   "text": "Поиск идёт…", "messages": []})
    try:
        stats = run_once(settings, build_sources(settings))
        result = {"text": summary(stats), "messages": stats.messages}
    except Exception as exc:  # noqa: BLE001
        log.exception("Проход завершился ошибкой")
        stats = None
        result = {"text": f"Проход завершился ошибкой: {exc}", "messages": []}
    storage.set_state("last_run", {"by": by, "started_at": started, "running": False,
                                   "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                   **result})
    return stats
