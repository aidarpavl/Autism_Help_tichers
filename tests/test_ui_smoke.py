"""Каждая страница открывается и каждая кнопка нажимается без необработанных ошибок."""
import io
import runpy
import shutil
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from PIL import Image

from qadam import config, services as sv

import fake_streamlit as fs

APP = str(Path(__file__).resolve().parent.parent / "app.py")
TEACHER = ["🏠 Главная", "🧒 Ученики", "🗓️ КТП", "📝 Поурочные планы", "🃏 Карточки", "💬 Коммуникатор",
           "📋 Пошаговые задания", "📈 ABA и пробы", "🌦️ Поведение", "🤖 Отчёты ИИ", "👤 Профиль"]
STUDENT = ["🏠 Мой день", "🃏 Карточки", "💬 Хочу сказать", "📋 Мои задания", "😊 Настроение", "👤 Профиль"]


def png():
    out = io.BytesIO()
    Image.new("RGBA", (800, 800), "red").save(out, "PNG")
    return out.getvalue()


@pytest.fixture
def st(monkeypatch):
    for name in [m for m in sys.modules if m.startswith("qadam.ui") or m.startswith("streamlit")]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    fake = fs.install(sys.modules)
    yield fake
    for name in [m for m in sys.modules if m.startswith("qadam.ui") or m.startswith("streamlit")]:
        del sys.modules[name]


def seed():
    t = sv.register_teacher("teacher", "password123", "Анна Учитель")
    s = sv.teacher_create_student(t, "pupil", "secret1", "Айбек Серіков")
    k = sv.save_ktp(t, "КТП", "Мир", "1", "", [{"topic": "Домашние животные", "goals": "Называет корову"}])
    tp = sv.get_topics(t, k)[0]
    sv.save_lesson_plan(t, tp["id"], s, "План", "# П\n| a | b |\n|---|---|\n| 1 | 2 |", "template")
    d = sv.create_deck(t, "Цвета")
    img = None
    cards = [sv.add_card(t, d, n, "🔴", image=img) for n in ("Красный", "Синий", "Жёлтый")]
    sv.set_deck_students(t, d, [s])
    for i in range(6):
        sv.record_attempt(s, d, cards[i % 3], i % 2 == 0, False, 2, 1500)
    sch = sv.create_schedule(t, "Мою руки", "🧼")
    sv.add_step(t, sch, "Открой кран", "🚰", 1)
    sv.add_step(t, sch, "Вытри руки", "🧻")
    sv.set_schedule_students(t, sch, [s])
    p = sv.create_program(t, s, "Даёт предмет", "Игра")
    sv.record_aba_session(t, p, "2026-11-01", 3, 4, 3)
    now = config.now()
    sv.add_behavior(t, s, (now - timedelta(hours=1)).isoformat(sep=" "), "Класс", "Ожидание", "Крик / плач", "Перерыв", 4, 2)
    sv.add_mood(s, "Грустно", "ребёнок")
    sv.log_phrase(s, ["Я хочу", "вода"])
    sv.add_aac_item(t, "Мои", "💬", "Качели")
    from qadam import ai
    ai.generate_report(t, s, now.date() - timedelta(days=3), now.date())
    return t, s


def run(st, click=None):
    """Один проход скрипта; True, если был запрошен перезапуск."""
    st.begin(click)
    try:
        runpy.run_path(APP, run_name="__main__")
    except fs.Stop:
        return False
    except fs.Rerun:
        return True
    return False


def settle(st, click):
    if run(st, click):
        for _ in range(3):
            if not run(st):
                break


def snapshot_db():
    p = config.db_path()
    import sqlite3
    c = sqlite3.connect(p)
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    c.close()
    backup = p.with_suffix(".bak")
    shutil.copy(p, backup)
    return p, backup


@pytest.mark.parametrize("role,pages", [("teacher", TEACHER), ("student", STUDENT)])
def test_every_page_and_button(st, role, pages):
    t, s = seed()
    uid = t if role == "teacher" else s
    path, backup = snapshot_db()
    pressed = 0
    for page in pages:
        st.nav = page
        st.session_state.clear()
        st.session_state["user_id"] = uid
        run(st)
        labels = list(dict.fromkeys(st.buttons))
        assert st.errors == [], (page, st.errors)
        for label in labels:
            shutil.copy(backup, path)
            st.session_state.clear()
            st.session_state["user_id"] = uid
            run(st)
            settle(st, label)
            pressed += 1
            if label == "Выйти":
                continue
            run(st)  # страница открывается и после действия
    assert pressed > (40 if role == "teacher" else 15)


def test_register_and_login(st):
    st.nav = None
    run(st)
    assert {"Войти", "Создать кабинет учителя", "Зарегистрировать ученика"} <= set(st.buttons)
    st.nav = TEACHER[0]
    assert run(st, "Создать кабинет учителя") is True
    assert st.session_state["user_id"]
    run(st)
    assert st.errors == []


def test_ktp_upload_to_lessons_and_cards(st):
    from openpyxl import Workbook

    t = sv.register_teacher("teacher", "password123", "Анна Учитель")
    wb = Workbook()
    wb.active.append(["Тема урока", "Цели"])
    wb.active.append(["Времена года", "Называет времена года"])
    out = io.BytesIO()
    wb.save(out)
    st.session_state["user_id"] = t
    st.upload = fs.Upload("plan.xlsx", out.getvalue())
    st.nav = "🗓️ КТП"
    run(st)
    settle(st, "Сохранить КТП")
    run(st)
    assert "ktp_preview" not in st.session_state  # файл ещё в загрузчике, но план не предлагается повторно
    st.upload = None
    assert sv.list_ktp(t)[0]["topics"] == 1
    st.nav = "📝 Поурочные планы"
    run(st)
    run(st, "Создать планы")
    plans = sv.list_lesson_plans(t)
    assert len(plans) == 1 and "Времена года" in plans[0]["content"]
    assert sv.list_decks(t)[0]["cards"] >= 2


def test_student_plays_full_card_game(st):
    t, s = seed()
    st.session_state["user_id"] = s
    st.nav = "🃏 Карточки"
    deck = sv.student_decks(s)[0]
    before = len(sv.recent_attempts(s, deck["id"], 100))
    run(st)
    settle(st, f"deck_{deck['id']}")
    game = st.session_state["game"]
    total = len(game["queue"])
    for _ in range(total):
        game = st.session_state["game"]
        card = game["queue"][game["index"]]
        assert len(game["options"]) == game["level"]["options"] and card in game["options"]
        settle(st, f"opt_{game['index']}_{card}")
        settle(st, "game_next")
    assert st.session_state["game"]["finished"] and st.session_state["game"]["right"] == total
    assert len(sv.recent_attempts(s, deck["id"], 100)) == before + total


def test_student_finishes_schedule(st):
    from qadam import db

    t, s = seed()
    st.session_state["user_id"] = s
    st.nav = "📋 Мои задания"
    sch = sv.student_schedules(s)[0]
    run(st)
    settle(st, f"run_{sch['id']}")
    settle(st, "step_ok_0")
    settle(st, "step_ok_1")
    assert "run" not in st.session_state
    row = db.one("SELECT * FROM schedule_runs WHERE student_id=?", [s])
    assert (row["steps_done"], row["steps_total"]) == (2, 2)


def test_image_is_downscaled(st):
    from qadam.ui.common import read_image

    data = read_image(fs.Upload("a.png", png()))
    assert max(Image.open(io.BytesIO(data)).size) == 512
