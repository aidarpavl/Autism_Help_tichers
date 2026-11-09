"""Вход, регистрация и страница профиля."""
from __future__ import annotations

from datetime import date

import streamlit as st

from .. import config, github_sync, services as sv

AVATARS = ["🙂", "🦊", "🐻", "🐱", "🐶", "🐢", "🦉", "🚀", "🌟", "🌈", "⚽", "🎵"]


def login_page() -> None:
    st.title("Qadam")
    st.caption("Обучение и сопровождение детей с аутизмом: шаг за шагом")
    login, teacher, student = st.tabs(["Вход", "Регистрация учителя", "Регистрация ученика"])

    with login, st.form("login"):
        username = st.text_input("Логин")
        password = st.text_input("Пароль", type="password")
        if st.form_submit_button("Войти"):
            try:
                user = sv.authenticate(username, password)
                st.session_state.user_id = user["id"]
                st.rerun()
            except sv.ValidationError as e:
                st.error(str(e))

    with teacher, st.form("register_teacher"):
        name = st.text_input("ФИО")
        school = st.text_input("Организация")
        email = st.text_input("Эл. почта")
        username = st.text_input("Придумайте логин", key="rt_u")
        password = st.text_input("Пароль (от 8 символов)", type="password", key="rt_p")
        code = st.text_input("Код регистрации (если выдан)") if config.get("TEACHER_SIGNUP_CODE") else ""
        if st.form_submit_button("Создать кабинет учителя"):
            try:
                uid = sv.register_teacher(username, password, name, email, school, code)
                st.session_state.user_id = uid
                st.rerun()
            except sv.ValidationError as e:
                st.error(str(e))

    with student, st.form("register_student"):
        st.caption("Регистрацию ребёнка проводит взрослый. Код выдаёт учитель.")
        name = st.text_input("Имя и фамилия ученика")
        birth = st.date_input("Дата рождения", value=date(2016, 1, 1), min_value=date(2000, 1, 1),
                              max_value=config.now().date())
        invite = st.text_input("Код учителя")
        username = st.text_input("Придумайте логин", key="rs_u")
        password = st.text_input("Пароль (от 6 символов)", type="password", key="rs_p")
        if st.form_submit_button("Зарегистрировать ученика"):
            try:
                uid = sv.register_student(username, password, name, invite, str(birth))
                st.session_state.user_id = uid
                st.rerun()
            except sv.ValidationError as e:
                st.error(str(e))


def profile_page(user: dict) -> None:
    st.header("Мой профиль")
    data_tab, password_tab = st.tabs(["Личные данные", "Смена пароля"])

    with data_tab, st.form("profile"):
        avatar = st.selectbox("Значок", AVATARS, index=AVATARS.index(user["avatar"]) if user["avatar"] in AVATARS else 0)
        name = st.text_input("ФИО", user["full_name"])
        username = st.text_input("Логин", user["username"])
        fields = {}
        if user["role"] == "teacher":
            fields["email"] = st.text_input("Эл. почта", user["email"])
            fields["phone"] = st.text_input("Телефон", user["phone"])
            fields["school"] = st.text_input("Организация", user["school"])
            fields["specialization"] = st.text_input("Специализация", user["specialization"])
        if st.form_submit_button("Сохранить"):
            try:
                sv.update_profile(user["id"], full_name=name, avatar=avatar, **fields)
                if username.strip().lower() != user["username"]:
                    sv.change_username(user["id"], username)
                st.success("Профиль сохранён.")
                st.rerun()
            except sv.ValidationError as e:
                st.error(str(e))

    with password_tab, st.form("password", clear_on_submit=True):
        current = st.text_input("Текущий пароль", type="password")
        new = st.text_input("Новый пароль", type="password")
        repeat = st.text_input("Повторите новый пароль", type="password")
        if st.form_submit_button("Изменить пароль"):
            try:
                sv.change_password(user["id"], current, new, repeat)
                st.success("Пароль изменён.")
            except sv.ValidationError as e:
                st.error(str(e))

    if user["role"] == "teacher":
        st.subheader("Код для регистрации учеников")
        st.code(user["invite_code"], language=None)
        if st.button("Выдать новый код"):
            sv.regenerate_invite(user["id"])
            st.rerun()

        st.subheader("Хранение данных")
        if github_sync.enabled():
            st.caption(github_sync.last_status or "Копия создаётся автоматически после изменений.")
            if st.button("Сохранить в GitHub сейчас"):
                try:
                    st.success(github_sync.backup_now())
                except github_sync.SyncError as e:
                    st.error(str(e))
        else:
            st.warning("Копирование в приватный репозиторий GitHub не настроено. "
                       "На Streamlit Cloud данные будут потеряны при перезапуске. Инструкция в README.")
