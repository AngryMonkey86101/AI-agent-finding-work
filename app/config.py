"""Настройки запуска, которые задаются через переменные окружения (.env)."""
import os
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "./data")).resolve()
DB_PATH = DATA_DIR / "agent.db"
RESUME_DIR = DATA_DIR / "resumes"

# Логин и пароль для входа в панель настроек.
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

MAX_RESUME_BYTES = 10 * 1024 * 1024  # 10 МБ


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RESUME_DIR.mkdir(parents=True, exist_ok=True)
