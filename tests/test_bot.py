import pytest

from app.sources.base import Vacancy

CHAT = "777"


class FakeTG:
    def __init__(self):
        self.sent = []
        self.edits = []
        self.answers = []
        self.next_id = 100

    def send(self, chat_id, text, keyboard=None):
        self.next_id += 1
        self.sent.append({"chat": str(chat_id), "text": text, "keyboard": keyboard, "id": self.next_id})
        return {"message_id": self.next_id}

    def edit(self, chat_id, message_id, text, keyboard=None):
        self.edits.append({"id": message_id, "text": text, "keyboard": keyboard})

    def answer(self, callback_id, text=""):
        self.answers.append(text)


class FakeLLM:
    def __init__(self):
        self.extra = None

    def letter(self, v, resume, settings, extra=""):
        self.extra = extra
        return "Новое короткое письмо"


class FakeSMTP:
    instances = []

    def __init__(self, host, port):
        self.host, self.port, self.sent, self.login_args = host, port, [], None
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, user, password):
        self.login_args = (user, password)

    def send_message(self, msg):
        self.sent.append(msg)


@pytest.fixture
def env(tmp_path, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "agent.db")
    monkeypatch.setattr(config, "RESUME_DIR", tmp_path / "resumes")
    from app import bot, storage

    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4 fake")
    storage.save_resume("Иванов_резюме.pdf", str(cv), "Python, FastAPI")
    v = Vacancy("hh", "1", "Python-разработчик <Senior>", "Ромашка & Ко", "https://hh.ru/vacancy/1",
                city="Москва", salary_from=200000, currency="RUR", contact_email="hr@romashka.ru")
    vid = storage.save_vacancy(v, "k", "ready", score=85, reason="Подходит", letter="Здравствуйте! <b>Опыт</b> 5 лет.")
    settings = {"telegram_chat_id": CHAT, "email_enabled": True, "smtp_host": "smtp.test", "smtp_port": 465,
                "smtp_user": "me@test.ru", "smtp_password": "pw", "email_from_name": "Иван",
                "email_subject": "Отклик: {vacancy}", "hh_resume_url": "https://hh.ru/resume/abc",
                "timezone": "Europe/Moscow"}
    FakeSMTP.instances = []
    tg = FakeTG()
    llm = FakeLLM()
    b = bot.Bot(tg, llm_factory=lambda s: llm, smtp_factory=FakeSMTP)
    return {"bot": b, "tg": tg, "llm": llm, "storage": storage, "settings": settings, "vid": vid}


def press(env, data, chat=CHAT):
    env["bot"].handle({"callback_query": {"id": "cb", "data": data, "message": {"chat": {"id": int(chat)}}}},
                      env["settings"])


def text(env, msg, chat=CHAT):
    env["bot"].handle({"message": {"chat": {"id": int(chat)}, "text": msg}}, env["settings"])


def test_card_is_sent_once_and_escaped(env):
    b, tg, s = env["bot"], env["tg"], env["storage"]
    assert b.notify_pending(env["settings"]) == 1
    assert b.notify_pending(env["settings"]) == 0
    card = tg.sent[0]
    assert card["chat"] == CHAT
    assert "&lt;Senior&gt;" in card["text"] and "Ромашка &amp; Ко" in card["text"]
    assert "<pre>Здравствуйте! &lt;b&gt;Опыт&lt;/b&gt; 5 лет.</pre>" in card["text"]
    assert "85%" in card["text"] and "200 000 ₽" in card["text"]
    buttons = [b["text"] for row in card["keyboard"] for b in row]
    assert buttons == ["Открыть на hh.ru", "✅ Отправил", "⏭ Пропустить", "✏️ Изменить письмо", "📧 На email"]
    assert s.get_vacancy(env["vid"])["tg_message_id"] == card["id"]


def test_no_email_button_when_disabled(env):
    env["settings"]["email_enabled"] = False
    env["bot"].notify_pending(env["settings"])
    buttons = [b["text"] for row in env["tg"].sent[0]["keyboard"] for b in row]
    assert "📧 На email" not in buttons


def test_paused_or_no_chat_sends_nothing(env):
    assert env["bot"].notify_pending({**env["settings"], "paused": True}) == 0
    assert env["bot"].notify_pending({**env["settings"], "telegram_chat_id": ""}) == 0


def test_applied_and_skip(env):
    env["bot"].notify_pending(env["settings"])
    press(env, f"a:{env['vid']}")
    v = env["storage"].get_vacancy(env["vid"])
    assert v["status"] == "applied" and v["acted_at"]
    edit = env["tg"].edits[-1]
    assert "✅ Вы откликнулись" in edit["text"] and len(edit["keyboard"]) == 1  # осталась только «Открыть»
    # Повторное нажатие не меняет статус.
    press(env, f"s:{env['vid']}")
    assert env["storage"].get_vacancy(env["vid"])["status"] == "applied"
    assert "уже обработана" in env["tg"].answers[-1]


def test_other_chat_is_ignored(env):
    env["bot"].notify_pending(env["settings"])
    press(env, f"a:{env['vid']}", chat="999")
    assert env["storage"].get_vacancy(env["vid"])["status"] == "ready"
    text(env, "/status", chat="999")
    # Чужому чату бот не показывает данные, только подсказку с его chat ID.
    assert len(env["tg"].sent) == 2
    hint = env["tg"].sent[-1]
    assert hint["chat"] == "999" and "другой chat ID" in hint["text"] and "Сегодня" not in hint["text"]


def test_chat_id_with_spaces_works(env):
    env["settings"]["telegram_chat_id"] = " 777 "
    text(env, "/status")
    assert "Сегодня" in env["tg"].sent[-1]["text"]


def test_start_drops_webhook():
    from app.bot import start

    class TG:
        calls = []

        def call(self, method, **kw):
            self.calls.append(method)
            if method == "getMe":
                return {"username": "my_bot"}
            if method == "getWebhookInfo":
                return {"url": "https://old.example/hook"}
            return True

    tg = TG()
    start(tg, {"telegram_chat_id": "1"})
    assert tg.calls == ["getMe", "getWebhookInfo", "deleteWebhook"]


def test_unknown_chat_gets_its_id_when_not_configured(env):
    env["settings"]["telegram_chat_id"] = ""
    text(env, "/start", chat="555")
    assert "555" in env["tg"].sent[-1]["text"] and env["tg"].sent[-1]["chat"] == "555"


def test_rewrite_letter(env):
    env["bot"].notify_pending(env["settings"])
    press(env, f"r:{env['vid']}")
    assert "Что поменять" in env["tg"].sent[-1]["text"]
    text(env, "короче и упомяни Docker")
    v = env["storage"].get_vacancy(env["vid"])
    assert v["letter"] == "Новое короткое письмо" and v["status"] == "ready"
    assert "упомяни Docker" in env["llm"].extra and "Здравствуйте!" in env["llm"].extra
    assert "Новое короткое письмо" in env["tg"].edits[-1]["text"]
    # Следующее сообщение уже не считается правкой письма.
    text(env, "привет")
    assert "Я присылаю" in env["tg"].sent[-1]["text"]


def test_cancel_rewrite(env):
    press(env, f"r:{env['vid']}")
    text(env, "/cancel")
    text(env, "короче")
    assert env["storage"].get_vacancy(env["vid"])["letter"].startswith("Здравствуйте!")


def test_email_needs_confirmation_and_sends(env):
    env["bot"].notify_pending(env["settings"])
    press(env, f"e:{env['vid']}")
    assert not FakeSMTP.instances
    assert "Отправить письмо с резюме на hr@romashka.ru?" in env["tg"].edits[-1]["text"]
    press(env, f"ey:{env['vid']}")
    smtp = FakeSMTP.instances[-1]
    assert (smtp.host, smtp.port, smtp.login_args) == ("smtp.test", 465, ("me@test.ru", "pw"))
    msg = smtp.sent[0]
    assert msg["To"] == "hr@romashka.ru" and msg["Subject"] == "Отклик: Python-разработчик <Senior>"
    body = msg.get_body(("plain",)).get_content()
    assert "Опыт" in body and "https://hh.ru/resume/abc" in body and "https://hh.ru/vacancy/1" in body
    att = list(msg.iter_attachments())[0]
    assert att.get_filename() == "Иванов_резюме.pdf" and att.get_content() == b"%PDF-1.4 fake"
    v = env["storage"].get_vacancy(env["vid"])
    assert v["status"] == "emailed"
    assert "Письмо отправлено на hr@romashka.ru" in env["tg"].edits[-1]["text"]


def test_email_cancel_restores_card(env):
    env["bot"].notify_pending(env["settings"])
    press(env, f"e:{env['vid']}")
    press(env, f"en:{env['vid']}")
    assert not FakeSMTP.instances
    assert env["storage"].get_vacancy(env["vid"])["status"] == "ready"
    assert len(env["tg"].edits[-1]["keyboard"]) == 3


def test_email_failure_is_reported(env):
    env["settings"]["smtp_password"] = ""
    env["bot"].notify_pending(env["settings"])
    press(env, f"ey:{env['vid']}")
    assert "Не получилось" in env["tg"].sent[-1]["text"]
    assert env["storage"].get_vacancy(env["vid"])["status"] == "ready"


def test_status_command(env):
    press(env, f"a:{env['vid']}")
    text(env, "/status")
    assert "Откликнулся: 1" in env["tg"].sent[-1]["text"]


def test_long_letter_fits_telegram_limit(env):
    from app.bot import card_text

    v = env["storage"].get_vacancy(env["vid"])
    v["letter"] = "а" * 6000
    assert len(card_text(v, "подвал")) <= 4096


def test_telegram_client_requests():
    import json

    import httpx

    from app.telegram import Telegram, TelegramError

    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append((request.url.path, body))
        if request.url.path.endswith("/editMessageText"):
            return httpx.Response(400, json={"ok": False, "description": "Bad Request: message is not modified"})
        if request.url.path.endswith("/sendMessage") and body["chat_id"] == "bad":
            return httpx.Response(400, json={"ok": False, "description": "chat not found"})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 5}})

    tg = Telegram("123:ABC", httpx.Client(transport=httpx.MockTransport(handler)))
    assert tg.send("1", "<b>x</b>", [[{"text": "a", "callback_data": "a:1"}]]) == {"message_id": 5}
    path, body = calls[-1]
    assert path == "/bot123:ABC/sendMessage" and body["parse_mode"] == "HTML"
    assert body["reply_markup"] == {"inline_keyboard": [[{"text": "a", "callback_data": "a:1"}]]}
    tg.edit("1", 5, "same")  # «not modified» не считается ошибкой
    with pytest.raises(TelegramError, match="chat not found"):
        tg.send("bad", "x")


def test_old_database_gets_new_columns(tmp_path, monkeypatch):
    import sqlite3

    from app import config, storage

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "agent.db")
    old = sqlite3.connect(tmp_path / "agent.db")
    old.execute("CREATE TABLE vacancies (id INTEGER PRIMARY KEY, source TEXT, external_id TEXT, dedupe_key TEXT, "
                "title TEXT, company TEXT, url TEXT, found_at TEXT, status TEXT, updated_at TEXT)")
    old.commit()
    old.close()
    assert storage.pending_notifications() == []
    with storage.connect() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(vacancies)")}
    assert {"notified_at", "tg_message_id", "acted_at"} <= cols
