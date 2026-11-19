from datetime import datetime, timezone

import pytest

from app.sources.base import Vacancy

# 19.11.2026 15:30 по Москве = 12:30 UTC
NOW = datetime(2026, 11, 19, 12, 30, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path, monkeypatch):
    from app import config, storage

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "agent.db")
    return storage


def settings(**over):
    s = {"timezone": "Europe/Moscow", "report_time": "18:00", "daily_limit": 20, "telegram_chat_id": "1"}
    s.update(over)
    return s


def test_is_due():
    from app.report import is_due

    s = settings()
    assert not is_due(s, None, NOW)                                   # 15:30 < 18:00
    evening = datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc)      # 18:00 МСК
    assert is_due(s, None, evening)
    assert is_due(s, "2026-11-18", evening)
    assert not is_due(s, "2026-11-19", evening)                       # сегодня уже отправлен
    assert is_due(settings(report_time="15:00"), None, NOW)
    # Часовой пояс учитывается: в Новосибирске (+7) уже 19:30.
    assert is_due(settings(timezone="Asia/Novosibirsk"), None, NOW)


def add(storage, ext_id, status, source="hh", score=80, **kw):
    v = Vacancy(source, ext_id, f"Вакансия {ext_id}", f"Компания {ext_id}", f"https://x/{ext_id}",
                contact_email=kw.get("email", ""))
    vid = storage.save_vacancy(v, ext_id, "ready" if status in storage.PASSED else status, score=score)
    if status in ("applied", "emailed", "skipped"):
        storage.set_status(vid, status)
    return vid


def test_build_report(db):
    from app.report import build

    add(db, "1", "applied")
    add(db, "2", "emailed", email="hr@two.ru")
    add(db, "3", "skipped")
    add(db, "4", "ready", score=91)
    add(db, "5", "ready", source="superjob", score=70)
    add(db, "6", "low_score", score=30)
    add(db, "7", "filtered", score=None)
    add(db, "8", "duplicate", score=None)
    add(db, "9", "error", score=None)

    text = build(settings())
    assert "Отчёт за" in text
    assert "Новых вакансий: <b>9</b> (hh.ru 8, SuperJob 1)" in text
    assert "подошли: 5, не подошли по оценке: 1, отсеяно фильтрами: 1, дубли: 1" in text
    assert "с ошибкой: 1" in text and "дневной лимит: 5 из 20" in text
    assert "Откликов на сайтах: <b>1</b>" in text and "Вакансия 1</a>" in text
    assert "Отправлено на email: <b>1</b>" in text and "(hr@two.ru)" in text
    assert "Пропущено: 1" in text
    assert "Ждут вашего решения: <b>2</b>" in text
    assert text.index("Вакансия 4") < text.index("Вакансия 5")  # сначала с высокой оценкой


def test_empty_day(db):
    from app.report import build

    text = build(settings(paused=True))
    assert "Новых вакансий сегодня не было" in text and "Все карточки разобраны" in text
    assert "на паузе" in text


def test_long_lists_fit_one_message(db):
    from app.report import build

    for i in range(120):
        add(db, f"w{i}", "ready")
    text = build(settings())
    assert len(text) <= 4096 and "…и ещё" in text


def test_bot_sends_report_once_a_day(db, monkeypatch):
    from app import report
    from app.bot import Bot

    class TG:
        sent = []

        def send(self, chat, text, keyboard=None):
            self.sent.append(text)
            return {"message_id": 1}

    tg = TG()
    bot = Bot(tg)
    evening = datetime(2026, 11, 19, 15, 5, tzinfo=timezone.utc)
    assert not bot.maybe_send_report(settings(), NOW)
    assert bot.maybe_send_report(settings(), evening)
    assert not bot.maybe_send_report(settings(), evening)
    next_evening = datetime(2026, 11, 20, 15, 5, tzinfo=timezone.utc)
    assert bot.maybe_send_report(settings(), next_evening)
    assert len(tg.sent) == 2 and "Отчёт за 20.11" in tg.sent[-1]
    # Без chat ID отчёт не шлётся.
    assert not bot.maybe_send_report(settings(telegram_chat_id=""), datetime(2026, 11, 21, 15, 5, tzinfo=timezone.utc))


def test_report_command(db):
    from app.bot import Bot

    class TG:
        sent = []

        def send(self, chat, text, keyboard=None):
            self.sent.append(text)
            return {"message_id": 1}

    bot = Bot(TG())
    bot.handle({"message": {"chat": {"id": 1}, "text": "/report"}}, settings())
    assert "Отчёт за" in TG.sent[-1]
