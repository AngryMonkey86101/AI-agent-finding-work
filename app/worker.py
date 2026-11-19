"""Фоновый процесс: раз в N минут запускает проход агента.

Запуск: python -m app.worker  (в Docker это сервис worker).
Разовый проход для проверки: python -m app.worker --once
"""
import argparse
import logging
import time

from app import storage
from app.pipeline import build_sources, run_once
from app.settings_schema import defaults

log = logging.getLogger("worker")


def current_settings() -> dict:
    values = defaults()
    values.update(storage.load_settings())
    return values


def tick() -> int:
    """Один проход. Возвращает паузу до следующего в секундах."""
    settings = current_settings()
    interval = max(5, int(settings.get("search_interval_minutes") or 30)) * 60
    if settings.get("paused"):
        log.info("Агент на паузе")
        return interval
    if not settings.get("positions"):
        log.info("Не заданы желаемые должности, жду настройки")
        return 60
    stats = run_once(settings, build_sources(settings))
    log.info(
        "Найдено %s, новых %s, отсеяно %s, дублей %s, не подошло %s, готово %s, ошибок %s",
        stats.found, stats.new, stats.filtered, stats.duplicates, stats.low_score, stats.ready, stats.errors,
    )
    for m in stats.messages:
        log.warning(m)
    return interval


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="один проход и выход")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.once:
        tick()
        return
    while True:
        try:
            pause = tick()
        except Exception:  # noqa: BLE001 - воркер не должен падать насовсем
            log.exception("Проход завершился ошибкой")
            pause = 300
        time.sleep(pause)


if __name__ == "__main__":
    main()
