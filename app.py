"""Точка входа: streamlit run app.py"""
import streamlit as st

from qadam import db, github_sync, services as sv
from qadam.ui import auth, student, teacher
from qadam.ui.common import inject_css

st.set_page_config(page_title="Qadam", page_icon="🌱", layout="wide")
inject_css()


@st.cache_resource
def bootstrap() -> bool:
    github_sync.restore_if_missing()
    db.init_db()
    db.set_write_hook(github_sync.schedule_backup)
    return True


try:
    bootstrap()
except github_sync.SyncError as e:
    st.error(f"Не удалось загрузить данные из GitHub: {e}")
    st.stop()

TEACHER_PAGES = {
    "🏠 Главная": teacher.dashboard, "🧒 Ученики": teacher.students_page, "🗓️ КТП": teacher.ktp_page,
    "📝 Поурочные планы": teacher.lessons_page, "🃏 Карточки": teacher.decks_page,
    "💬 Коммуникатор": teacher.communicator_page, "📋 Пошаговые задания": teacher.schedules_page,
    "📈 ABA и пробы": teacher.aba_page, "🌦️ Поведение": teacher.behavior_page,
    "🤖 Отчёты ИИ": teacher.reports_page, "👤 Профиль": auth.profile_page,
}
STUDENT_PAGES = {
    "🏠 Мой день": student.home, "🃏 Карточки": student.cards_page, "💬 Хочу сказать": student.communicator,
    "📋 Мои задания": student.schedule_page, "😊 Настроение": student.mood_page, "👤 Профиль": auth.profile_page,
}

user_id = st.session_state.get("user_id")
user = sv.get_user(user_id) if user_id else None

if not user:
    st.session_state.pop("user_id", None)
    auth.login_page()
    st.stop()

pages = TEACHER_PAGES if user["role"] == "teacher" else STUDENT_PAGES
with st.sidebar:
    st.markdown(f"### {user['avatar']} {user['full_name']}")
    st.caption("Учитель" if user["role"] == "teacher" else "Ученик")
    choice = st.radio("Меню", list(pages), label_visibility="collapsed", key="nav")
    if st.button("Выйти"):
        st.session_state.clear()
        st.rerun()

try:
    pages[choice](user)
except sv.ValidationError as e:
    st.error(str(e))
except sv.AccessDenied as e:
    st.error(str(e))
