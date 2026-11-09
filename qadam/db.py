"""SQLite: схема и короткие помощники запросов."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Any, Callable, Iterable

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY,
  role TEXT NOT NULL CHECK(role IN('teacher','student')),
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  full_name TEXT NOT NULL,
  email TEXT DEFAULT '',
  phone TEXT DEFAULT '',
  school TEXT DEFAULT '',
  specialization TEXT DEFAULT '',
  birth_date TEXT DEFAULT '',
  avatar TEXT DEFAULT '🙂',
  invite_code TEXT UNIQUE,
  teacher_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  failed_logins INTEGER DEFAULT 0,
  locked_until TEXT DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS student_profiles(
  student_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  support_notes TEXT DEFAULT '', communication_level TEXT DEFAULT 'Карточки (PECS)',
  sensory TEXT DEFAULT '', interests TEXT DEFAULT '', reinforcers TEXT DEFAULT '',
  known_triggers TEXT DEFAULT '', strengths TEXT DEFAULT '', iep_goals TEXT DEFAULT '',
  parent_contact TEXT DEFAULT '', consent_ai INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ktp(
  id INTEGER PRIMARY KEY, teacher_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title TEXT NOT NULL, subject TEXT DEFAULT '', grade TEXT DEFAULT '', filename TEXT DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ktp_topics(
  id INTEGER PRIMARY KEY, ktp_id INTEGER NOT NULL REFERENCES ktp(id) ON DELETE CASCADE,
  position INTEGER NOT NULL, section TEXT DEFAULT '', topic TEXT NOT NULL,
  goals TEXT DEFAULT '', lesson_date TEXT DEFAULT '', hours TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS lesson_plans(
  id INTEGER PRIMARY KEY, teacher_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  topic_id INTEGER REFERENCES ktp_topics(id) ON DELETE SET NULL,
  student_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  title TEXT NOT NULL, content TEXT NOT NULL, source TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decks(
  id INTEGER PRIMARY KEY, teacher_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title TEXT NOT NULL, description TEXT DEFAULT '',
  topic_id INTEGER REFERENCES ktp_topics(id) ON DELETE SET NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cards(
  id INTEGER PRIMARY KEY, deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
  position INTEGER DEFAULT 0, emoji TEXT DEFAULT '🔹', label TEXT NOT NULL,
  question TEXT DEFAULT '', image BLOB, audio BLOB
);
CREATE TABLE IF NOT EXISTS deck_assignments(
  deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
  student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  PRIMARY KEY(deck_id, student_id)
);
CREATE TABLE IF NOT EXISTS card_attempts(
  id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
  card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
  correct INTEGER NOT NULL, prompted INTEGER NOT NULL DEFAULT 0, options_count INTEGER NOT NULL,
  response_ms INTEGER DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS aac_items(
  id INTEGER PRIMARY KEY, teacher_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  student_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  category TEXT NOT NULL, emoji TEXT DEFAULT '💬', label TEXT NOT NULL, image BLOB
);
CREATE TABLE IF NOT EXISTS aac_log(
  id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  sentence TEXT NOT NULL, words INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schedules(
  id INTEGER PRIMARY KEY, teacher_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title TEXT NOT NULL, emoji TEXT DEFAULT '📋', description TEXT DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schedule_steps(
  id INTEGER PRIMARY KEY, schedule_id INTEGER NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
  position INTEGER NOT NULL, text TEXT NOT NULL, emoji TEXT DEFAULT '▶️',
  minutes INTEGER DEFAULT 0, image BLOB, audio BLOB
);
CREATE TABLE IF NOT EXISTS schedule_assignments(
  schedule_id INTEGER NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
  student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  PRIMARY KEY(schedule_id, student_id)
);
CREATE TABLE IF NOT EXISTS schedule_runs(
  id INTEGER PRIMARY KEY, schedule_id INTEGER NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
  student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  steps_done INTEGER NOT NULL, steps_total INTEGER NOT NULL, help_count INTEGER DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS aba_programs(
  id INTEGER PRIMARY KEY, teacher_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  target TEXT NOT NULL, domain TEXT DEFAULT 'Речь и коммуникация',
  mastery_pct INTEGER DEFAULT 80, mastery_sessions INTEGER DEFAULT 3,
  status TEXT DEFAULT 'В работе', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS aba_sessions(
  id INTEGER PRIMARY KEY, program_id INTEGER NOT NULL REFERENCES aba_programs(id) ON DELETE CASCADE,
  session_date TEXT NOT NULL, correct INTEGER NOT NULL, prompted INTEGER NOT NULL,
  incorrect INTEGER NOT NULL, notes TEXT DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS behavior_logs(
  id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  author_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  occurred_at TEXT NOT NULL, setting TEXT DEFAULT '', antecedent TEXT DEFAULT '',
  behavior TEXT NOT NULL, consequence TEXT DEFAULT '', intensity INTEGER DEFAULT 1,
  duration_min INTEGER DEFAULT 0, notes TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS mood_logs(
  id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  mood TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reports(
  id INTEGER PRIMARY KEY, teacher_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  student_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  period_start TEXT NOT NULL, period_end TEXT NOT NULL, content TEXT NOT NULL,
  source TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS report_prompts(
  id INTEGER PRIMARY KEY, report_id INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
  problem_key TEXT NOT NULL, problem_title TEXT NOT NULL, severity INTEGER NOT NULL,
  evidence TEXT NOT NULL, prompt TEXT NOT NULL, response TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attempts ON card_attempts(student_id, created_at);
CREATE INDEX IF NOT EXISTS idx_behavior ON behavior_logs(student_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_mood ON mood_logs(student_id, created_at);
CREATE INDEX IF NOT EXISTS idx_aac ON aac_log(student_id, created_at);
CREATE INDEX IF NOT EXISTS idx_runs ON schedule_runs(student_id, created_at);
"""

DEFAULT_AAC = {
    "Я хочу": [("🙋", "Я хочу"), ("🙅", "Я не хочу"), ("🤝", "Помоги"), ("✋", "Стоп"),
               ("⏸️", "Перерыв"), ("➕", "Ещё"), ("✅", "Да"), ("❌", "Нет")],
    "Еда": [("💧", "вода"), ("🍎", "яблоко"), ("🍌", "банан"), ("🥛", "молоко"),
            ("🍞", "хлеб"), ("🍪", "печенье"), ("🍵", "чай"), ("🍲", "суп")],
    "Занятия": [("🧩", "пазл"), ("🎨", "рисовать"), ("📖", "книга"), ("🎵", "музыка"),
                ("⚽", "мяч"), ("🚶", "гулять"), ("🚗", "машинка"), ("🧱", "кубики")],
    "Чувства": [("😊", "радостно"), ("😢", "грустно"), ("😠", "сердито"), ("😨", "страшно"),
                ("😴", "устал"), ("🤕", "больно"), ("🔊", "громко"), ("🤗", "обнять")],
    "Места и люди": [("🚻", "туалет"), ("🏠", "домой"), ("🏫", "класс"), ("🛝", "площадка"),
                     ("👩", "мама"), ("👨", "папа"), ("👩‍🏫", "учитель"), ("🧒", "друг")],
}

_on_write: Callable[[], None] | None = None


def set_write_hook(hook: Callable[[], None] | None) -> None:
    global _on_write
    _on_write = hook


@contextmanager
def connect():
    path = config.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with connect() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        if not c.execute("SELECT 1 FROM aac_items WHERE teacher_id IS NULL LIMIT 1").fetchone():
            for cat, items in DEFAULT_AAC.items():
                c.executemany(
                    "INSERT INTO aac_items(category,emoji,label) VALUES(?,?,?)",
                    [(cat, e, l) for e, l in items],
                )


def rows(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    with connect() as c:
        return [dict(r) for r in c.execute(sql, tuple(params)).fetchall()]


def one(sql: str, params: Iterable[Any] = ()) -> dict | None:
    with connect() as c:
        r = c.execute(sql, tuple(params)).fetchone()
        return dict(r) if r else None


def run(sql: str, params: Iterable[Any] = ()) -> int:
    with connect() as c:
        last = c.execute(sql, tuple(params)).lastrowid
    if _on_write:
        _on_write()
    return last or 0


def run_many(statements: list[tuple[str, Iterable[Any]]]) -> None:
    """Несколько записей одной транзакцией."""
    with connect() as c:
        for sql, params in statements:
            c.execute(sql, tuple(params))
    if _on_write:
        _on_write()
