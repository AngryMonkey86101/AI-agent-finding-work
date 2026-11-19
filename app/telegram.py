"""Минимальный клиент Telegram Bot API на httpx."""
import httpx


class TelegramError(RuntimeError):
    pass


class Telegram:
    def __init__(self, token: str, client: httpx.Client | None = None):
        if not token:
            raise TelegramError("Не задан токен Telegram-бота.")
        self.base = f"https://api.telegram.org/bot{token}"
        self.http = client or httpx.Client(timeout=40)

    def call(self, method: str, **params):
        payload = {k: v for k, v in params.items() if v is not None}
        r = self.http.post(f"{self.base}/{method}", json=payload)
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if not data.get("ok"):
            raise TelegramError(f"{method}: {data.get('description') or r.status_code}")
        return data["result"]

    def updates(self, offset: int | None, timeout: int = 25) -> list[dict]:
        return self.call("getUpdates", offset=offset, timeout=timeout,
                         allowed_updates=["message", "callback_query"])

    def send(self, chat_id, text: str, keyboard: list | None = None) -> dict:
        return self.call(
            "sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
            link_preview_options={"is_disabled": True},
            reply_markup={"inline_keyboard": keyboard} if keyboard is not None else None,
        )

    def edit(self, chat_id, message_id: int, text: str, keyboard: list | None = None) -> None:
        try:
            self.call(
                "editMessageText", chat_id=chat_id, message_id=message_id, text=text, parse_mode="HTML",
                link_preview_options={"is_disabled": True},
                reply_markup={"inline_keyboard": keyboard or []},
            )
        except TelegramError as exc:
            if "message is not modified" not in str(exc):
                raise

    def answer(self, callback_id: str, text: str = "") -> None:
        try:
            self.call("answerCallbackQuery", callback_query_id=callback_id, text=text or None)
        except TelegramError:
            pass  # кнопка могла устареть, это не страшно
