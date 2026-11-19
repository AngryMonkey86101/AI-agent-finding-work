# AI-агент поиска работы

Агент ищет вакансии на hh.ru, пишет сопроводительные письма через DeepSeek и присылает их в Telegram.
Вы откликаетесь в один клик (кнопки «Открыть на hh.ru» и «Скопировать письмо»), а если в вакансии
указан email, агент по вашей кнопке отправит письмо с резюме туда. Каждый вечер приходит отчёт.

hh.ru закрыл API для соискателей 15.12.2025, поэтому автоматической отправки откликов на hh.ru нет.

## Статус

- [x] Шаг 1. Панель настроек: поиск, письма, почта, расписание, ключи; загрузка резюме PDF/DOC/DOCX
- [ ] Шаг 2. Поиск вакансий на hh.ru, оценка и письма через DeepSeek
- [ ] Шаг 3. Telegram: вакансия с письмом, кнопки «Открыть», «Скопировать», «Отправил», «Отправить на email»
- [ ] Шаг 4. Вечерний отчёт в Telegram

## Запуск на VPS

```bash
git clone https://github.com/AngryMonkey86101/AI-agent-finding-work.git
cd AI-agent-finding-work
cp .env.example .env        # задайте ADMIN_PASSWORD
docker compose up -d --build
```

Панель слушает `127.0.0.1:8000`, то есть снаружи её не видно. Открыть её можно двумя способами:

- SSH-туннель: `ssh -L 8000:localhost:8000 user@ваш-vps`, затем в браузере http://localhost:8000
- nginx с HTTPS (Let's Encrypt), проксирующий на `127.0.0.1:8000`

Вход по логину и паролю из `.env`. Настройки и резюме хранятся в папке `data/` (SQLite).

## Разработка

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
ADMIN_PASSWORD=dev uvicorn app.main:app --reload
pytest
```
