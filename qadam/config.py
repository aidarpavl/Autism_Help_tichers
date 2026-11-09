"""Настройки: переменные окружения, затем секреты Streamlit."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent


def get(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value:
        return value
    try:
        import streamlit as st

        value = st.secrets.get(name)  # нет файла секретов — исключение
        if value:
            return str(value)
    except Exception:
        pass
    return default


def db_path() -> Path:
    return Path(get("QADAM_DB", str(ROOT / "data" / "qadam.db")))


def now() -> datetime:
    try:
        tz = ZoneInfo(get("QADAM_TZ", "Asia/Qyzylorda"))
    except Exception:
        tz = ZoneInfo("UTC")
    return datetime.now(tz).replace(tzinfo=None, microsecond=0)


def now_iso() -> str:
    return now().isoformat(sep=" ")
