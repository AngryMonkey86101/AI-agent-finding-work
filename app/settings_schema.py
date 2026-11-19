"""Описание полей панели настроек и их проверка."""
import re
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str  # text | textarea | number | time | select | multiselect | checkbox | secret
    group: str
    default: object = ""
    help: str = ""
    choices: tuple[tuple[str, str], ...] = ()
    required: bool = False
    min: int | None = None
    max: int | None = None
    placeholder: str = ""
    extra: dict = field(default_factory=dict)


EXPERIENCE = (
    ("", "Любой"),
    ("noExperience", "Нет опыта"),
    ("between1And3", "От 1 до 3 лет"),
    ("between3And6", "От 3 до 6 лет"),
    ("moreThan6", "Более 6 лет"),
)
SCHEDULE = (
    ("fullDay", "Полный день"),
    ("remote", "Удалённая работа"),
    ("flexible", "Гибкий график"),
    ("shift", "Сменный график"),
    ("flyInFlyOut", "Вахта"),
)
EMPLOYMENT = (
    ("full", "Полная занятость"),
    ("part", "Частичная занятость"),
    ("project", "Проектная работа"),
    ("probation", "Стажировка"),
)
CURRENCY = (("RUR", "₽ RUB"), ("USD", "$ USD"), ("EUR", "€ EUR"), ("KZT", "₸ KZT"), ("BYR", "Br BYN"))
LETTER_LANG = (("ru", "Русский"), ("en", "English"))

GROUPS = (
    ("search", "Что ищем"),
    ("letter", "Сопроводительные письма"),
    ("email", "Отклики по email"),
    ("schedule", "Отклики и отчёты"),
    ("keys", "Ключи и токены"),
)

FIELDS: tuple[Field, ...] = (
    # --- Что ищем
    Field("positions", "Желаемые должности", "textarea", "search", required=True,
          help="По одной на строку. Например: Python-разработчик", placeholder="Python-разработчик\nBackend-разработчик"),
    Field("keywords", "Ключевые слова", "textarea", "search",
          help="Навыки и слова, которые должны быть в вакансии. По одному на строку или через запятую."),
    Field("stop_words", "Стоп-слова", "textarea", "search",
          help="Вакансии с этими словами агент пропустит. Например: 1С, стажёр, продажи."),
    Field("excluded_companies", "Исключить компании", "textarea", "search",
          help="Названия компаний, куда не откликаться. По одному на строку."),
    Field("city", "Город", "text", "search", placeholder="Москва",
          help="Оставьте пустым, чтобы искать по всей России."),
    Field("schedule", "График", "multiselect", "search", default=["fullDay", "remote"], choices=SCHEDULE),
    Field("employment", "Тип занятости", "multiselect", "search", default=["full"], choices=EMPLOYMENT),
    Field("experience", "Опыт работы", "select", "search", choices=EXPERIENCE),
    Field("salary_from", "Зарплата от", "number", "search", default=None, min=0,
          help="Пусто, если не важно."),
    Field("currency", "Валюта", "select", "search", default="RUR", choices=CURRENCY),
    Field("only_with_salary", "Только вакансии с указанной зарплатой", "checkbox", "search", default=False),
    Field("min_match_score", "Минимальная оценка соответствия, %", "number", "search", default=60, min=0, max=100,
          help="Агент оценивает каждую вакансию от 0 до 100 и присылает только те, что выше порога."),

    # --- Письма
    Field("letter_language", "Язык писем", "select", "letter", default="ru", choices=LETTER_LANG),
    Field("letter_instructions", "Пожелания к письмам", "textarea", "letter",
          default="Коротко, 4–6 предложений, без шаблонных фраз. Упомянуть 2–3 навыка, которые совпадают с требованиями вакансии.",
          help="Тон, длина, что обязательно упомянуть."),
    Field("letter_signature", "Подпись", "textarea", "letter", placeholder="Иван Иванов\n+7 900 000-00-00\nt.me/ivan"),

    Field("hh_resume_url", "Ссылка на резюме на hh.ru", "text", "letter", placeholder="https://hh.ru/resume/...",
          help="Агент добавит её в письма, которые уходят на email работодателя."),

    # --- Email
    Field("email_enabled", "Предлагать отклик по email, если в вакансии указана почта", "checkbox", "email", default=False),
    Field("smtp_host", "SMTP-сервер", "text", "email", placeholder="smtp.gmail.com",
          help="Gmail: smtp.gmail.com, Яндекс: smtp.yandex.ru, Mail.ru: smtp.mail.ru."),
    Field("smtp_port", "SMTP-порт", "number", "email", default=465, min=1, max=65535,
          help="465 для SSL, 587 для STARTTLS."),
    Field("smtp_user", "Адрес почты (логин)", "text", "email", placeholder="you@example.com"),
    Field("smtp_password", "Пароль приложения", "secret", "email",
          help="Не основной пароль, а пароль приложения из настроек безопасности почты."),
    Field("email_from_name", "Имя отправителя", "text", "email", placeholder="Иван Иванов"),
    Field("email_subject", "Тема письма", "text", "email", default="Отклик на вакансию «{vacancy}»",
          help="{vacancy} заменится на название вакансии."),

    # --- Отклики и отчёты
    Field("search_interval_minutes", "Проверять новые вакансии каждые, мин", "number", "schedule", default=30, min=5, max=1440),
    Field("daily_limit", "Не больше откликов в день", "number", "schedule", default=20, min=1, max=200,
          help="hh.ru ограничивает число откликов, разумно держать до 20–30 в день."),
    Field("report_time", "Время вечернего отчёта", "time", "schedule", default="18:00", required=True),
    Field("timezone", "Часовой пояс", "text", "schedule", default="Europe/Moscow",
          help="Например: Europe/Moscow, Asia/Yekaterinburg, Asia/Almaty."),
    Field("paused", "Поставить агента на паузу", "checkbox", "schedule", default=False),

    # --- Ключи
    Field("deepseek_api_key", "DeepSeek API key", "secret", "keys"),
    Field("deepseek_model", "Модель DeepSeek", "text", "keys", default="deepseek-chat"),
    Field("telegram_bot_token", "Токен Telegram-бота", "secret", "keys", help="Выдаёт @BotFather."),
    Field("telegram_chat_id", "Telegram chat ID", "text", "keys",
          help="Куда слать уведомления. Узнать свой ID можно у @userinfobot."),
)

FIELDS_BY_KEY = {f.key: f for f in FIELDS}
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def defaults() -> dict:
    return {f.key: f.default for f in FIELDS}


def _split_list(raw: str) -> list[str]:
    items = re.split(r"[\n,;]", raw or "")
    return [i.strip() for i in items if i.strip()]


def parse_form(form, current: dict) -> tuple[dict, dict]:
    """Разбирает отправленную форму. Возвращает (значения, ошибки по полям).

    Пустое поле секрета означает «оставить как было».
    """
    values: dict = {}
    errors: dict = {}
    for f in FIELDS:
        if f.kind == "checkbox":
            values[f.key] = form.get(f.key) in ("on", "true", "1")
            continue
        if f.kind == "multiselect":
            allowed = {c[0] for c in f.choices}
            values[f.key] = [v for v in form.getlist(f.key) if v in allowed]
            continue

        raw = (form.get(f.key) or "").strip()

        if f.kind == "secret":
            if form.get(f"{f.key}__clear") == "on":
                values[f.key] = ""
            else:
                values[f.key] = raw or current.get(f.key, "")
            continue

        if f.kind == "number":
            if raw == "":
                values[f.key] = None if f.default is None else f.default
            else:
                try:
                    n = int(raw)
                except ValueError:
                    errors[f.key] = "Введите целое число."
                    continue
                if f.min is not None and n < f.min:
                    errors[f.key] = f"Не меньше {f.min}."
                elif f.max is not None and n > f.max:
                    errors[f.key] = f"Не больше {f.max}."
                values[f.key] = n
            continue

        if f.kind == "select":
            allowed = {c[0] for c in f.choices}
            values[f.key] = raw if raw in allowed else f.default
            continue

        if f.kind == "time":
            if not TIME_RE.match(raw):
                errors[f.key] = "Укажите время в формате ЧЧ:ММ, например 18:00."
            values[f.key] = raw
            continue

        if f.key in ("positions", "keywords", "stop_words", "excluded_companies"):
            values[f.key] = _split_list(raw)
        else:
            values[f.key] = raw

        if f.required and not values[f.key]:
            errors[f.key] = "Обязательное поле."

    tz = values.get("timezone") or "Europe/Moscow"
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        errors["timezone"] = "Неизвестный часовой пояс. Пример: Europe/Moscow."
    values["timezone"] = tz

    if values.get("email_enabled"):
        for key in ("smtp_host", "smtp_user", "smtp_password"):
            if not values.get(key):
                errors[key] = "Нужно для отправки писем на email."
        if values.get("smtp_user") and "@" not in values["smtp_user"]:
            errors["smtp_user"] = "Укажите адрес почты целиком."

    return values, errors


def for_display(value) -> str:
    if isinstance(value, list):
        return "\n".join(value)
    if value is None:
        return ""
    return str(value)
