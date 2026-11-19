"""Telegram-бот: присылает карточки вакансий и обрабатывает кнопки.

Запуск: python -m app.bot  (в Docker это сервис bot).
"""
import html
import logging
import time

from app import mailer, report, storage
from app.deepseek import DeepSeek
from app.settings_schema import defaults
from app.sources.base import from_row
from app.telegram import Telegram, TelegramError

log = logging.getLogger("bot")

SOURCE_NAMES = {"hh": "hh.ru", "superjob": "SuperJob"}
DONE_LABELS = {
    "applied": "✅ Вы откликнулись",
    "emailed": "📧 Письмо отправлено на {email}",
    "skipped": "⏭ Пропущено",
}
MAX_TEXT = 4000

HELP = (
    "Я присылаю подходящие вакансии с готовым письмом.\n\n"
    "• Нажмите на письмо в рамке, чтобы скопировать его.\n"
    "• «Открыть» ведёт на вакансию, там вставьте письмо и откликнитесь.\n"
    "• «Отправил» отмечает отклик для вечернего отчёта.\n"
    "• «Изменить письмо» перепишет его по вашим словам.\n"
    "• «На email» отправит письмо с резюме работодателю, если в вакансии есть почта.\n\n"
    "/status — сводка за сегодня\n"
    "/report — вечерний отчёт прямо сейчас"
)


def load_settings() -> dict:
    values = defaults()
    values.update(storage.load_settings())
    return values


def norm_chat(value) -> str:
    """Chat ID из панели: убираем пробелы, кавычки и прочий мусор вокруг числа."""
    return str(value or "").strip().strip("'\"").replace(" ", "")


def esc(text) -> str:
    return html.escape(str(text or ""), quote=False)


def card_text(v: dict, footer: str = "") -> str:
    head = [
        f"<b>{v['score']}%</b> · <a href=\"{html.escape(v['url'])}\">{esc(v['title'])}</a>",
        f"{esc(v['company'])} · {esc(v.get('city') or 'город не указан')} · {esc(v.get('salary'))} · "
        f"{SOURCE_NAMES.get(v['source'], v['source'])}",
    ]
    if v.get("score_reason"):
        head.append(f"<i>{esc(v['score_reason'])}</i>")
    if v.get("contact_email"):
        head.append(f"✉ {esc(v['contact_email'])}")
    text = "\n".join(head)
    tail = f"\n\n{footer}" if footer else ""
    room = MAX_TEXT - len(text) - len(tail) - 20
    letter = esc(v.get("letter") or "")
    if len(letter) > room:
        letter = letter[: room - 1] + "…"
    return f"{text}\n\n<pre>{letter}</pre>{tail}"


def open_button(v: dict) -> list:
    return [{"text": f"Открыть на {SOURCE_NAMES.get(v['source'], v['source'])}", "url": v["url"]}]


def card_keyboard(v: dict, settings: dict) -> list:
    rows = [
        open_button(v),
        [{"text": "✅ Отправил", "callback_data": f"a:{v['id']}"},
         {"text": "⏭ Пропустить", "callback_data": f"s:{v['id']}"}],
    ]
    last = [{"text": "✏️ Изменить письмо", "callback_data": f"r:{v['id']}"}]
    if v.get("contact_email") and settings.get("email_enabled"):
        last.append({"text": "📧 На email", "callback_data": f"e:{v['id']}"})
    rows.append(last)
    return rows


class Bot:
    def __init__(self, tg: Telegram, llm_factory=None, smtp_factory=None):
        self.tg = tg
        self.llm_factory = llm_factory or (lambda s: DeepSeek(s.get("deepseek_api_key", ""), s.get("deepseek_model", "")))
        self.smtp_factory = smtp_factory
        self.username = ""

    # ---------- исходящие карточки

    def notify_pending(self, settings: dict) -> int:
        chat = norm_chat(settings.get("telegram_chat_id"))
        if not chat or settings.get("paused"):
            return 0
        sent = 0
        for v in storage.pending_notifications():
            msg = self.tg.send(chat, card_text(v), card_keyboard(v, settings))
            storage.mark_notified(v["id"], msg.get("message_id"))
            sent += 1
        return sent

    def maybe_send_report(self, settings: dict, now=None) -> bool:
        chat = norm_chat(settings.get("telegram_chat_id"))
        if not chat or not report.is_due(settings, storage.get_state("report_sent_date"), now):
            return False
        self.tg.send(chat, report.build(settings, now))
        storage.set_state("report_sent_date", report.local_now(settings, now).date().isoformat())
        return True

    def refresh_card(self, v: dict, settings: dict) -> None:
        if not v.get("tg_message_id"):
            return
        chat = norm_chat(settings["telegram_chat_id"])
        if v["status"] == "ready":
            self.tg.edit(chat, v["tg_message_id"], card_text(v), card_keyboard(v, settings))
        else:
            footer = DONE_LABELS.get(v["status"], v["status"]).format(email=v.get("contact_email", ""))
            self.tg.edit(chat, v["tg_message_id"], card_text(v, footer), [open_button(v)])

    # ---------- входящие

    def handle(self, update: dict, settings: dict) -> None:
        chat = norm_chat(settings.get("telegram_chat_id"))
        settings = {**settings, "telegram_chat_id": chat}
        if "callback_query" in update:
            cq = update["callback_query"]
            from_chat = str(((cq.get("message") or {}).get("chat") or {}).get("id", ""))
            if not chat or from_chat != chat:
                self.tg.answer(cq["id"], "Этот бот настроен на другой чат.")
                return
            self.on_button(cq, settings)
            return

        msg = update.get("message") or {}
        from_chat = str((msg.get("chat") or {}).get("id", ""))
        text = (msg.get("text") or "").strip()
        log.info("Сообщение из чата %s: %s", from_chat, text[:50])
        if not from_chat:
            return
        if not chat:
            self.tg.send(from_chat, f"Ваш chat ID: <code>{esc(from_chat)}</code>\n"
                                    "Впишите его в панели настроек в поле «Telegram chat ID».")
            return
        if from_chat != chat:
            log.warning("Чат %s не совпадает с chat ID из панели (%s), отвечаю подсказкой", from_chat, chat)
            self.tg.send(from_chat, f"Этот бот настроен на другой chat ID. Ваш chat ID: <code>{esc(from_chat)}</code>\n"
                                    "Если это ваш бот, впишите это число в панели в поле «Telegram chat ID».")
            return
        self.on_text(text, settings)

    def on_text(self, text: str, settings: dict) -> None:
        chat = settings["telegram_chat_id"]
        waiting = storage.get_state("edit_wait")
        if text in ("/cancel", "отмена", "Отмена"):
            storage.set_state("edit_wait", None)
            self.tg.send(chat, "Хорошо, письмо не меняю.")
            return
        if waiting and not text.startswith("/"):
            storage.set_state("edit_wait", None)
            self.rewrite_letter(int(waiting), text, settings)
            return
        if text.startswith("/report"):
            self.tg.send(chat, report.build(settings))
            return
        if text.startswith("/status"):
            self.tg.send(chat, status_text(settings))
            return
        self.tg.send(chat, HELP)

    def on_button(self, cq: dict, settings: dict) -> None:
        action, _, raw_id = (cq.get("data") or "").partition(":")
        try:
            v = storage.get_vacancy(int(raw_id))
        except ValueError:
            v = None
        if not v:
            self.tg.answer(cq["id"], "Вакансия не найдена.")
            return
        if v["status"] != "ready" and action not in ("en",):
            self.tg.answer(cq["id"], "Эта вакансия уже обработана.")
            self.refresh_card(v, settings)
            return

        chat = settings["telegram_chat_id"]
        if action == "a":
            storage.set_status(v["id"], "applied")
            self.tg.answer(cq["id"], "Отмечено")
        elif action == "s":
            storage.set_status(v["id"], "skipped")
            self.tg.answer(cq["id"], "Пропущено")
        elif action == "r":
            storage.set_state("edit_wait", v["id"])
            self.tg.answer(cq["id"])
            self.tg.send(chat, f"Что поменять в письме для «{esc(v['title'])}»? Например: «короче», "
                               "«упомяни Docker», «тон построже». Или /cancel.")
            return
        elif action == "e":
            self.tg.answer(cq["id"])
            self.tg.edit(chat, v["tg_message_id"], card_text(v, f"Отправить письмо с резюме на {esc(v['contact_email'])}?"), [
                [{"text": "Да, отправить", "callback_data": f"ey:{v['id']}"},
                 {"text": "Отмена", "callback_data": f"en:{v['id']}"}],
            ])
            return
        elif action == "ey":
            self.tg.answer(cq["id"], "Отправляю…")
            try:
                msg = mailer.build_message(v, settings, storage.load_resume())
                mailer.send(msg, settings, smtp_factory=self.smtp_factory)
            except mailer.MailError as exc:
                self.tg.send(chat, f"Не получилось: {esc(exc)}")
                self.refresh_card(v, settings)
                return
            storage.set_status(v["id"], "emailed", note=v["contact_email"])
        elif action == "en":
            self.tg.answer(cq["id"])
        else:
            self.tg.answer(cq["id"])
            return
        self.refresh_card(storage.get_vacancy(v["id"]), settings)

    def rewrite_letter(self, vacancy_id: int, wish: str, settings: dict) -> None:
        chat = settings["telegram_chat_id"]
        v = storage.get_vacancy(vacancy_id)
        if not v or v["status"] != "ready":
            self.tg.send(chat, "Эта вакансия уже обработана.")
            return
        resume = storage.load_resume()
        try:
            llm = self.llm_factory(settings)
            extra = f"Предыдущий вариант письма:\n{v.get('letter') or ''}\n\nЧто поменять: {wish}"
            letter = llm.letter(from_row(v), resume["text"] if resume else "", settings, extra=extra)
        except Exception as exc:  # noqa: BLE001
            self.tg.send(chat, f"Не удалось переписать письмо: {esc(exc)}")
            return
        storage.set_letter(vacancy_id, letter)
        v = storage.get_vacancy(vacancy_id)
        self.refresh_card(v, settings)
        self.tg.send(chat, f"Письмо для «{esc(v['title'])}» обновил в карточке выше.")


def status_text(settings: dict) -> str:
    from app.pipeline import local_midnight_utc

    since = local_midnight_utc(settings.get("timezone", "Europe/Moscow"))
    counts = storage.status_counts_since(since)
    lines = ["<b>Сегодня</b>"]
    for key, label in storage.STATUS_LABELS.items():
        if counts.get(key):
            lines.append(f"{label}: {counts[key]}")
    if len(lines) == 1:
        lines.append("Пока ничего нового.")
    return "\n".join(lines)


def start(tg: Telegram, settings: dict) -> str:
    me = tg.call("getMe")
    info = tg.call("getWebhookInfo")
    if info.get("url"):
        log.warning("На боте был вебхук %s, удаляю его, чтобы получать сообщения", info["url"])
        tg.call("deleteWebhook")
    chat = norm_chat(settings.get("telegram_chat_id"))
    log.info("Бот @%s запущен. Chat ID из панели: %s", me.get("username"), chat or "не задан, напишите боту /start")
    return me.get("username", "")


def heartbeat(ok: bool, text: str) -> None:
    from datetime import datetime, timezone

    storage.set_state("bot_status", {"ok": ok, "text": text,
                                     "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    bot: Bot | None = None
    token = None
    while True:
        settings = load_settings()
        if not settings.get("telegram_bot_token"):
            log.info("Нет токена Telegram-бота в панели (база %s), жду настройки", storage.config.DB_PATH)
            heartbeat(False, "нет токена в панели")
            time.sleep(30)
            continue
        if settings["telegram_bot_token"].strip() != token:
            token = settings["telegram_bot_token"].strip()
            bot = Bot(Telegram(token))
            try:
                bot.username = start(bot.tg, settings)
            except TelegramError as exc:
                log.error("Не удалось подключиться к Telegram: %s. Проверьте токен в панели.", exc)
                heartbeat(False, f"ошибка подключения к Telegram: {exc}")
                token = None
                time.sleep(30)
                continue
        try:
            offset = storage.get_state("tg_offset")
            for update in bot.tg.updates(offset):
                storage.set_state("tg_offset", update["update_id"] + 1)
                try:
                    bot.handle(update, load_settings())
                except Exception:  # noqa: BLE001
                    log.exception("Не удалось обработать сообщение")
            bot.notify_pending(load_settings())
            bot.maybe_send_report(load_settings())
            heartbeat(True, f"работает как @{bot.username}")
        except TelegramError as exc:
            heartbeat(False, f"ошибка Telegram: {exc}")
            if "Conflict" in str(exc):
                log.error("Telegram: этот токен уже опрашивает другая копия бота или на нём висит вебхук. "
                          "Остановите другие копии (docker compose ps) — бот сбросит вебхук сам. %s", exc)
                try:
                    bot.tg.call("deleteWebhook")
                except TelegramError:
                    pass
            else:
                log.warning("Telegram: %s", exc)
            time.sleep(10)
        except Exception:  # noqa: BLE001
            log.exception("Ошибка цикла бота")
            time.sleep(10)


if __name__ == "__main__":
    main()
