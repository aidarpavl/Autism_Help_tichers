"""Кабинет учителя."""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from .. import ai, analytics, config, exporter, ktp_parser, services as sv
from .common import pick_student, read_audio, read_image

COMM_LEVELS = ["Без речи, жесты", "Карточки (PECS)", "Отдельные слова", "Фразы", "Развёрнутая речь"]
DOMAINS = ["Речь и коммуникация", "Понимание инструкций", "Имитация", "Социальные навыки",
           "Самообслуживание", "Академические навыки", "Игра", "Моторика"]
ANTECEDENTS = ["Переход между занятиями", "Отказ / слово «нет»", "Сложное задание", "Шум / сенсорная нагрузка",
               "Ожидание", "Изменение расписания", "Нет внимания взрослого", "Конфликт со сверстником", "Неизвестно"]
BEHAVIORS = ["Крик / плач", "Уход от задания", "Самостимуляция", "Самоагрессия", "Агрессия к другим",
             "Бросание предметов", "Отказ от выполнения", "Застывание"]
CONSEQUENCES = ["Перерыв", "Смена задания", "Перенаправление", "Визуальная подсказка", "Внимание взрослого",
                "Игнорирование поведения", "Снижение требований", "Сенсорная пауза"]


# ---------- Главная ----------

def dashboard(user: dict) -> None:
    st.header(f"Здравствуйте, {user['full_name']}")
    students = sv.list_students(user["id"])
    a, b, c = st.columns(3)
    a.metric("Ученики", len(students))
    b.metric("Планы КТП", len(sv.list_ktp(user["id"])))
    c.metric("Поурочные планы", len(sv.list_lesson_plans(user["id"])))
    if not ai.available():
        st.info("ИИ не подключён: планы и отчёты строятся по методическим шаблонам. Добавьте ключ в настройках приложения.")
    if not students:
        st.info(f"Начните с добавления учеников. Код для их регистрации: **{user['invite_code']}**")
        return
    st.subheader("Последние 7 дней")
    end = config.now().date()
    start = end - timedelta(days=6)
    table = []
    for s in students:
        snap = analytics.snapshot(s["id"], start, end)
        problems = [p for p in analytics.detect_problems(snap) if p["key"] != "no_data"]
        table.append({
            "Ученик": s["full_name"], "Ответы": snap["cards"]["attempts"],
            "Точность, %": snap["cards"]["accuracy"], "Поведение": snap["behavior"]["count"],
            "Фразы": snap["aac"]["phrases"], "На что обратить внимание": problems[0]["title"] if problems else "—",
        })
    st.dataframe(pd.DataFrame(table), hide_index=True)


# ---------- Ученики ----------

def students_page(user: dict) -> None:
    st.header("Ученики")
    tid = user["id"]
    st.caption(f"Код для самостоятельной регистрации: {user['invite_code']}")
    with st.expander("Добавить ученика"):
        with st.form("new_student", clear_on_submit=True):
            name = st.text_input("Имя и фамилия")
            login = st.text_input("Логин")
            password = st.text_input("Пароль (от 6 символов)", type="password")
            if st.form_submit_button("Добавить"):
                sv.teacher_create_student(tid, login, password, name)
                st.success("Ученик добавлен.")
                st.rerun()

    student = pick_student(sv.list_students(tid), "students_pick")
    if not student:
        return
    p = sv.get_student_profile(student["id"])
    with st.form("student_profile"):
        st.subheader(f"Карта сопровождения: {student['full_name']}")
        level_index = COMM_LEVELS.index(p["communication_level"]) if p["communication_level"] in COMM_LEVELS else 1
        level = st.selectbox("Уровень коммуникации", COMM_LEVELS, index=level_index)
        support = st.text_area("Особые образовательные потребности", p["support_notes"])
        sensory = st.text_area("Сенсорные особенности", p["sensory"])
        interests = st.text_area("Интересы", p["interests"])
        reinforcers = st.text_area("Подкрепление (что мотивирует)", p["reinforcers"])
        triggers = st.text_area("Известные триггеры", p["known_triggers"])
        strengths = st.text_area("Сильные стороны", p["strengths"])
        goals = st.text_area("Цели индивидуальной программы", p["iep_goals"])
        parent = st.text_input("Контакт родителей (не передаётся ИИ)", p["parent_contact"])
        consent = st.checkbox("Есть согласие родителей на обработку обезличенных данных ИИ", bool(p["consent_ai"]))
        if st.form_submit_button("Сохранить карту"):
            sv.update_student_profile(tid, student["id"], communication_level=level, support_notes=support, sensory=sensory,
                                      interests=interests, reinforcers=reinforcers, known_triggers=triggers,
                                      strengths=strengths, iep_goals=goals, parent_contact=parent, consent_ai=int(consent))
            st.success("Сохранено.")

    with st.expander("Сброс пароля ученика"):
        with st.form("reset_pw", clear_on_submit=True):
            new = st.text_input("Новый пароль", type="password")
            if st.form_submit_button("Сбросить"):
                sv.reset_student_password(tid, student["id"], new)
                st.success("Пароль изменён.")

    with st.expander("Удалить ученика и все его данные"):
        sure = st.checkbox("Я понимаю, что данные нельзя будет восстановить", key=f"del_{student['id']}")
        if st.button("Удалить навсегда", disabled=not sure):
            sv.delete_student(tid, student["id"])
            st.rerun()


# ---------- КТП ----------

TOPIC_COLUMNS = ["section", "topic", "goals", "lesson_date", "hours"]
TOPIC_CONFIG = {
    "id": None, "ktp_id": None, "position": None,
    "section": st.column_config.TextColumn("Раздел"),
    "topic": st.column_config.TextColumn("Тема", required=True, width="large"),
    "goals": st.column_config.TextColumn("Цели", width="large"),
    "lesson_date": st.column_config.TextColumn("Дата"),
    "hours": st.column_config.TextColumn("Часы"),
}


def ktp_page(user: dict) -> None:
    st.header("Календарно-тематический план")
    tid = user["id"]
    upload_tab, list_tab = st.tabs(["Загрузить КТП", "Мои планы"])

    with upload_tab:
        file = st.file_uploader("Файл КТП", type=["xlsx", "csv", "docx", "pdf", "txt"],
                                help="Нужна таблица со столбцом «Тема». В PDF и TXT темы должны быть пронумерованы.")
        if file is not None and st.session_state.get("ktp_file") != file.name + str(file.size):
            try:
                st.session_state.ktp_preview = ktp_parser.parse(file.name, file.getvalue())
                st.session_state.ktp_file = file.name + str(file.size)
            except ktp_parser.ParseError as e:
                st.error(str(e))
                st.session_state.pop("ktp_preview", None)
        if st.button("Создать пустой план и заполнить вручную"):
            st.session_state.ktp_preview = [{"section": "", "topic": "Новая тема", "goals": "", "lesson_date": "", "hours": ""}]
        preview = st.session_state.get("ktp_preview")
        if preview:
            st.success(f"Найдено тем: {len(preview)}. Проверьте и при необходимости исправьте.")
            a, b, c = st.columns(3)
            title = a.text_input("Название", file.name if file else "Мой план")
            subject = b.text_input("Предмет")
            grade = c.text_input("Класс / группа")
            edited = st.data_editor(pd.DataFrame(preview, columns=TOPIC_COLUMNS), column_config=TOPIC_CONFIG,
                                    num_rows="dynamic", key=f"ktp_new_{st.session_state.get('ktp_file', 'manual')}", hide_index=True)
            if st.button("Сохранить КТП", type="primary"):
                sv.save_ktp(tid, title, subject, grade, file.name if file else "", edited.to_dict("records"))
                st.session_state.pop("ktp_preview", None)  # ktp_file остаётся: файл ещё в загрузчике
                st.success("КТП сохранён. Теперь создайте поурочные планы.")
                st.rerun()

    with list_tab:
        plans = sv.list_ktp(tid)
        if not plans:
            st.info("Планов пока нет.")
            return
        ktp = st.selectbox("План", plans, format_func=lambda k: f"{k['title']} ({k['topics']} тем)", key="ktp_pick")
        topics = sv.get_topics(tid, ktp["id"])
        edited = st.data_editor(pd.DataFrame(topics), column_config=TOPIC_CONFIG, num_rows="dynamic",
                                key=f"ktp_edit_{ktp['id']}", hide_index=True,
                                column_order=TOPIC_COLUMNS)
        a, b = st.columns(2)
        if a.button("Сохранить изменения"):
            sv.replace_topics(tid, ktp["id"], edited.to_dict("records"))
            st.success("Сохранено.")
            st.rerun()
        if b.button("Удалить этот КТП"):
            sv.delete_ktp(tid, ktp["id"])
            st.rerun()


# ---------- Поурочные планы ----------

def lessons_page(user: dict) -> None:
    st.header("Поурочные планы")
    tid = user["id"]
    create_tab, list_tab = st.tabs(["Создать", "Готовые планы"])
    students = sv.list_students(tid)

    with create_tab:
        plans = sv.list_ktp(tid)
        if not plans:
            st.info("Сначала загрузите КТП.")
        else:
            ktp = st.selectbox("КТП", plans, format_func=lambda k: k["title"], key="lesson_ktp")
            topics = sv.get_topics(tid, ktp["id"])
            chosen = st.multiselect("Темы", topics, format_func=lambda t: f"{t['position']}. {t['topic']}", key="lesson_topics")
            if st.checkbox("Выбрать все темы", key="lesson_all"):
                chosen = topics
            a, b = st.columns(2)
            minutes = a.number_input("Длительность, минут", 15, 90, 40, 5)
            student = b.selectbox("Индивидуально для", [None, *students],
                                  format_func=lambda s: "Для группы" if s is None else s["full_name"], key="lesson_student")
            make_cards = st.checkbox("Сразу создать набор интерактивных карточек к каждой теме", True)
            if st.button("Создать планы", type="primary", disabled=not chosen):
                bar = st.progress(0.0, "Составляю планы…")
                sid = student["id"] if student else None
                ai_count = 0
                for i, topic in enumerate(chosen, 1):
                    content, source = ai.generate_lesson_plan(ktp["subject"], ktp["grade"], topic["topic"], topic["goals"], minutes, sid)
                    ai_count += source == "ai"
                    sv.save_lesson_plan(tid, topic["id"], sid, topic["topic"], content, source)
                    if make_cards:
                        items, _ = ai.generate_cards(topic["topic"], topic["goals"])
                        if items:
                            deck = sv.create_deck(tid, topic["topic"], topic["goals"], topic["id"])
                            for card in items:
                                sv.add_card(tid, deck, card["label"], card["emoji"], card["question"])
                            if sid:
                                sv.set_deck_students(tid, deck, [sid])
                    bar.progress(i / len(chosen), f"Готово: {i} из {len(chosen)}")
                st.success(f"Создано планов: {len(chosen)}, из них с помощью ИИ: {ai_count}. Откройте вкладку «Готовые планы».")

    with list_tab:
        saved = sv.list_lesson_plans(tid)
        if not saved:
            st.info("Планов пока нет.")
            return
        plan = st.selectbox("План", saved, key="lesson_pick", format_func=lambda p:
                            f"{p['title']} — {p['student_name'] or 'группа'} ({'ИИ' if p['source'] == 'ai' else 'шаблон'})")
        view, edit = st.tabs(["Просмотр", "Редактировать"])
        with view:
            st.markdown(plan["content"])
        with edit:
            with st.form(f"plan_{plan['id']}"):
                title = st.text_input("Название", plan["title"])
                content = st.text_area("Текст (Markdown)", plan["content"], height=500)
                if st.form_submit_button("Сохранить"):
                    sv.update_lesson_plan(tid, plan["id"], title, content)
                    st.rerun()
        a, b = st.columns(2)
        a.download_button("Скачать Word", exporter.markdown_to_docx(plan["content"]), f"plan_{plan['id']}.docx", key=f"dl_{plan['id']}")
        if b.button("Удалить план"):
            sv.delete_lesson_plan(tid, plan["id"])
            st.rerun()


# ---------- Карточки ----------

def decks_page(user: dict) -> None:
    st.header("Интерактивные карточки")
    tid = user["id"]
    with st.expander("Новый набор"):
        with st.form("new_deck", clear_on_submit=True):
            title = st.text_input("Название набора")
            description = st.text_area("Описание")
            auto = st.checkbox("Заполнить автоматически по названию и описанию")
            if st.form_submit_button("Создать"):
                deck = sv.create_deck(tid, title, description)
                if auto:
                    items, _ = ai.generate_cards(title, description)
                    for c in items:
                        sv.add_card(tid, deck, c["label"], c["emoji"], c["question"])
                st.rerun()
    decks = sv.list_decks(tid)
    if not decks:
        st.info("Наборов пока нет. Их также можно создать вместе с поурочными планами.")
        return
    deck = st.selectbox("Набор", decks, key="deck_pick", format_func=lambda d: f"{d['title']} ({d['cards']} карт.)")

    students = sv.list_students(tid)
    by_id = {s["id"]: s for s in students}
    assigned = [i for i in sv.deck_students(deck["id"]) if i in by_id]
    who = st.multiselect("Назначить ученикам", list(by_id), default=assigned, key=f"deck_who_{deck['id']}",
                         format_func=lambda i: by_id[i]["full_name"])
    if st.button("Сохранить назначение", key="deck_assign"):
        sv.set_deck_students(tid, deck["id"], who)
        st.success("Назначено.")

    cards = sv.teacher_cards(tid, deck["id"])
    if len(cards) < 2:
        st.warning("Для игры нужно минимум 2 карточки.")
    for i in range(0, len(cards), 4):
        for col, card in zip(st.columns(4), cards[i:i + 4]):
            with col, st.container(border=True):
                if card["image"]:
                    st.image(card["image"], width=120)
                else:
                    st.markdown(f"## {card['emoji']}")
                st.write(f"**{card['label']}**")
                st.caption(card["question"] or "")
                if card["audio"]:
                    st.audio(card["audio"])
                if st.button("Удалить", key=f"card_del_{card['id']}"):
                    sv.delete_card(tid, card["id"])
                    st.rerun()

    with st.form("new_card", clear_on_submit=True):
        st.subheader("Добавить карточку")
        a, b = st.columns([1, 3])
        emoji = a.text_input("Эмодзи", "🔹")
        label = b.text_input("Подпись")
        question = st.text_input("Инструкция ребёнку, например: Покажи яблоко")
        image = st.file_uploader("Фото", type=["png", "jpg", "jpeg", "webp"])
        audio = st.file_uploader("Голосовая запись", type=["mp3", "wav", "m4a", "ogg"])
        if st.form_submit_button("Добавить"):
            sv.add_card(tid, deck["id"], label, emoji, question, read_image(image), read_audio(audio))
            st.rerun()
    if st.button("Удалить весь набор"):
        sv.delete_deck(tid, deck["id"])
        st.rerun()


# ---------- Коммуникатор ----------

def communicator_page(user: dict) -> None:
    st.header("Коммуникатор")
    tid = user["id"]
    students = sv.list_students(tid)
    by_id = {s["id"]: s for s in students}
    with st.form("new_aac", clear_on_submit=True):
        st.subheader("Добавить карточку общения")
        a, b, c = st.columns(3)
        category = a.text_input("Раздел", "Мои карточки")
        emoji = b.text_input("Эмодзи", "💬")
        label = c.text_input("Слово или фраза")
        who = st.selectbox("Кому", [None, *by_id], format_func=lambda i: "Всем ученикам" if i is None else by_id[i]["full_name"])
        image = st.file_uploader("Фото", type=["png", "jpg", "jpeg", "webp"])
        if st.form_submit_button("Добавить"):
            sv.add_aac_item(tid, category, emoji, label, who, read_image(image))
            st.rerun()
    items = sv.teacher_aac_items(tid)
    if items:
        st.subheader("Ваши карточки")
        for item in items:
            a, b = st.columns([5, 1])
            a.write(f"{item['emoji']} **{item['label']}** · {item['category']} · {item['student_name'] or 'все ученики'}")
            if b.button("Удалить", key=f"aac_del_{item['id']}"):
                sv.delete_aac_item(tid, item["id"])
                st.rerun()
    st.subheader("Что говорил ребёнок")
    student = pick_student(students, "aac_student")
    if student:
        log = sv.phrase_log(tid, student["id"])
        if log:
            st.dataframe(pd.DataFrame(log).rename(columns={"sentence": "Фраза", "created_at": "Время"}), hide_index=True)
        else:
            st.caption("Записей пока нет.")


# ---------- Пошаговые задания ----------

def schedules_page(user: dict) -> None:
    st.header("Пошаговые задания")
    tid = user["id"]
    with st.expander("Новое задание"):
        with st.form("new_schedule", clear_on_submit=True):
            a, b = st.columns([1, 4])
            emoji = a.text_input("Эмодзи", "📋")
            title = b.text_input("Название, например: Мою руки")
            description = st.text_input("Описание")
            if st.form_submit_button("Создать"):
                sv.create_schedule(tid, title, emoji, description)
                st.rerun()
    items = sv.list_schedules(tid)
    if not items:
        st.info("Заданий пока нет.")
        return
    item = st.selectbox("Задание", items, key="sch_pick", format_func=lambda s: f"{s['emoji']} {s['title']} ({s['steps']} шагов)")
    students = sv.list_students(tid)
    by_id = {s["id"]: s for s in students}
    assigned = [i for i in sv.schedule_students(item["id"]) if i in by_id]
    who = st.multiselect("Назначить ученикам", list(by_id), default=assigned, key=f"sch_who_{item['id']}",
                         format_func=lambda i: by_id[i]["full_name"])
    if st.button("Сохранить назначение", key="sch_assign"):
        sv.set_schedule_students(tid, item["id"], who)
        st.success("Назначено.")
    for n, step in enumerate(sv.teacher_steps(tid, item["id"]), 1):
        a, b, c = st.columns([1, 5, 1])
        if step["image"]:
            a.image(step["image"], width=70)
        else:
            a.markdown(f"### {step['emoji']}")
        b.write(f"**Шаг {n}.** {step['text']}" + (f" · {step['minutes']} мин" if step["minutes"] else ""))
        if step["audio"]:
            b.audio(step["audio"])
        if c.button("Удалить", key=f"step_del_{step['id']}"):
            sv.delete_step(tid, step["id"])
            st.rerun()
    with st.form("new_step", clear_on_submit=True):
        st.subheader("Добавить шаг")
        a, b, c = st.columns([1, 4, 1])
        emoji = a.text_input("Эмодзи", "▶️")
        text = b.text_input("Что сделать, коротко")
        minutes = c.number_input("Минут", 0, 120, 0)
        image = st.file_uploader("Фото шага", type=["png", "jpg", "jpeg", "webp"])
        audio = st.file_uploader("Аудиоподсказка", type=["mp3", "wav", "m4a", "ogg"])
        if st.form_submit_button("Добавить шаг"):
            sv.add_step(tid, item["id"], text, emoji, minutes, read_image(image), read_audio(audio))
            st.rerun()
    if st.button("Удалить задание"):
        sv.delete_schedule(tid, item["id"])
        st.rerun()


# ---------- ABA ----------

def aba_page(user: dict) -> None:
    st.header("ABA-программы и учёт проб")
    tid = user["id"]
    students = sv.list_students(tid)
    if not students:
        st.info("Сначала добавьте учеников.")
        return
    session_tab, programs_tab, charts_tab = st.tabs(["Занятие с несколькими учениками", "Программы", "Графики"])

    with session_tab:
        by_id = {s["id"]: s for s in students}
        chosen = st.multiselect("Ученики на занятии", list(by_id), key="aba_group", format_func=lambda i: by_id[i]["full_name"])
        programs = sv.list_programs(tid, chosen, active_only=True) if chosen else []
        trials = st.session_state.setdefault("aba_trials", {})
        for p in programs:
            log = trials.setdefault(p["id"], [])
            with st.container(border=True):
                st.write(f"**{p['student_name']}** — {p['target']}")
                a, b, c, d = st.columns(4)
                if a.button("✅ Сам", key=f"t_ok_{p['id']}"):
                    log.append("+")
                if b.button("🤝 С подсказкой", key=f"t_p_{p['id']}"):
                    log.append("P")
                if c.button("❌ Ошибка", key=f"t_no_{p['id']}"):
                    log.append("-")
                if d.button("↩️ Отменить", key=f"t_undo_{p['id']}") and log:
                    log.pop()
                st.caption(f"Пробы: {' '.join(log) or '—'} · Сам {log.count('+')}, подсказка {log.count('P')}, ошибка {log.count('-')}")
        if chosen and not programs:
            st.info("У выбранных учеников нет активных программ.")
        if programs and st.button("Сохранить занятие", type="primary"):
            saved, mastered = 0, []
            for p in programs:
                log = trials.get(p["id"]) or []
                if log:
                    if sv.record_aba_session(tid, p["id"], config.now().date(), log.count("+"), log.count("P"), log.count("-")):
                        mastered.append(p["target"])
                    saved += 1
                    trials[p["id"]] = []
            st.success(f"Сохранено сессий: {saved}.")
            if mastered:
                st.success("Навык освоен: " + ", ".join(mastered))

    with programs_tab:
        with st.form("new_program", clear_on_submit=True):
            student = st.selectbox("Ученик", students, format_func=lambda s: s["full_name"])
            target = st.text_input("Целевой навык, например: Выполняет инструкцию «дай»")
            domain = st.selectbox("Область", DOMAINS)
            a, b = st.columns(2)
            mastery = a.slider("Критерий освоения, %", 50, 100, 80, 5)
            sessions = b.number_input("Сессий подряд", 1, 10, 3)
            if st.form_submit_button("Добавить программу"):
                sv.create_program(tid, student["id"], target, domain, mastery, sessions)
                st.rerun()
        for p in sv.list_programs(tid):
            a, b, c = st.columns([4, 2, 1])
            a.write(f"**{p['student_name']}** — {p['target']} · {p['domain']} · цель {p['mastery_pct']}% × {p['mastery_sessions']}")
            options = ["В работе", "Освоено", "Пауза"]
            status = b.selectbox("Статус", options, index=options.index(p["status"]) if p["status"] in options else 0,
                                 key=f"prog_status_{p['id']}", label_visibility="collapsed")
            if status != p["status"]:
                sv.set_program_status(tid, p["id"], status)
                st.rerun()
            if c.button("Удалить", key=f"prog_del_{p['id']}"):
                sv.delete_program(tid, p["id"])
                st.rerun()

    with charts_tab:
        student = pick_student(students, "aba_chart_student")
        if student:
            shown = False
            for p in sv.list_programs(tid, [student["id"]]):
                data = sv.program_sessions(p["id"])
                if data:
                    shown = True
                    st.write(f"**{p['target']}** ({p['status']})")
                    frame = pd.DataFrame([{"Дата": f"{i + 1}. {s['session_date']}", "Самостоятельно, %": analytics.session_pct(s),
                                           "Критерий, %": p["mastery_pct"]} for i, s in enumerate(data)]).set_index("Дата")
                    st.line_chart(frame)
            if not shown:
                st.caption("Данных пока нет.")


# ---------- Поведение ----------

def _choice(col, label: str, options: list[str], key: str) -> str:
    picked = col.selectbox(label, options, key=key)
    other = col.text_input("Или своё", key=key + "_o")
    return other.strip() or picked


def behavior_page(user: dict) -> None:
    st.header("Дневник поведения и настроения")
    tid = user["id"]
    student = pick_student(sv.list_students(tid), "beh_student")
    if not student:
        return
    add_tab, history_tab = st.tabs(["Новая запись", "История и графики"])
    with add_tab:
        with st.form("behavior", clear_on_submit=True):
            a, b, c = st.columns(3)
            when_d = a.date_input("Дата", config.now().date())
            when_t = b.time_input("Время", config.now().time().replace(second=0, microsecond=0))
            setting = c.text_input("Где, на каком занятии")
            x, y, z = st.columns(3)
            antecedent = _choice(x, "До (что произошло)", ANTECEDENTS, "abc_a")
            behavior = _choice(y, "Поведение", BEHAVIORS, "abc_b")
            consequence = _choice(z, "После (реакция)", CONSEQUENCES, "abc_c")
            p, q = st.columns(2)
            intensity = p.slider("Интенсивность", 1, 5, 2)
            duration = q.number_input("Длительность, минут", 0, 240, 1)
            notes = st.text_area("Заметки")
            if st.form_submit_button("Сохранить запись"):
                when = datetime.combine(when_d, when_t).isoformat(sep=" ")
                sv.add_behavior(tid, student["id"], when, setting, antecedent, behavior, consequence, intensity, duration, notes)
                st.success("Запись сохранена.")
        mood = st.selectbox("Настроение ребёнка сейчас", ["Радостно", "Спокойно", "Грустно", "Сердито", "Страшно", "Устал"], key="teacher_mood")
        if st.button("Отметить настроение"):
            sv.add_mood(student["id"], mood, "учитель")
            st.success("Отмечено.")
    with history_tab:
        rows = sv.list_behavior(tid, student["id"])
        if not rows:
            st.caption("Записей пока нет.")
        else:
            frame = pd.DataFrame(rows)
            st.bar_chart(frame.assign(day=frame["occurred_at"].str[:10]).groupby("day").size().rename("Случаи"))
            a, b = st.columns(2)
            a.write("**Что предшествовало**")
            a.bar_chart(frame["antecedent"].value_counts().rename("Случаи"))
            b.write("**Поведение**")
            b.bar_chart(frame["behavior"].value_counts().rename("Случаи"))
            st.dataframe(frame[["occurred_at", "setting", "antecedent", "behavior", "consequence", "intensity", "duration_min", "notes"]].rename(
                columns={"occurred_at": "Когда", "setting": "Где", "antecedent": "До", "behavior": "Поведение",
                         "consequence": "После", "intensity": "Сила", "duration_min": "Минут", "notes": "Заметки"}), hide_index=True)


# ---------- Отчёты ИИ ----------

def reports_page(user: dict) -> None:
    st.header("Отчёты и анализ")
    tid = user["id"]
    student = pick_student(sv.list_students(tid), "rep_student")
    if not student:
        return
    profile = sv.get_student_profile(student["id"])
    if ai.available() and not profile.get("consent_ai"):
        st.warning("Для этого ребёнка не отмечено согласие родителей: отчёт будет составлен без ИИ, по шаблонам.")
    today = config.now().date()
    a, b = st.columns(2)
    start = a.date_input("С", today - timedelta(days=13), key="rep_from")
    end = b.date_input("По", today, key="rep_to")
    if st.button("Создать отчёт", type="primary", disabled=start > end):
        with st.spinner("Анализирую данные и пишу промт по каждой проблеме…"):
            ai.generate_report(tid, student["id"], start, end)
        st.rerun()

    reports = sv.list_reports(tid, student["id"])
    if not reports:
        st.info("Отчётов пока нет.")
        return
    report = st.selectbox("Отчёт", reports, key="rep_pick", format_func=lambda r:
                          f"{r['created_at']} · {r['period_start']} — {r['period_end']} · {'ИИ' if r['source'] == 'ai' else 'шаблон'}")
    text_tab, prompts_tab = st.tabs(["Отчёт", "Промты по проблемам"])
    with text_tab:
        st.markdown(report["content"])
        c, d = st.columns(2)
        c.download_button("Скачать Word", exporter.markdown_to_docx(report["content"]), f"report_{report['id']}.docx", key=f"rep_dl_{report['id']}")
        if d.button("Удалить отчёт"):
            sv.delete_report(tid, report["id"])
            st.rerun()
    with prompts_tab:
        st.caption("Система сама написала отдельный промт под каждую проблему. Его можно изменить и выполнить заново.")
        for item in sv.report_prompts(tid, report["id"]):
            with st.expander(item["problem_title"]):
                st.caption(item["evidence"])
                prompt = st.text_area("Промт", item["prompt"], height=260, key=f"pr_{item['id']}")
                if st.button("Выполнить заново", key=f"pr_run_{item['id']}", disabled=not ai.ai_allowed(student["id"])):
                    try:
                        with st.spinner("Выполняю…"):
                            sv.update_prompt_result(tid, item["id"], prompt, ai.run_prompt(prompt))
                        st.rerun()
                    except ai.AIError as e:
                        st.error(str(e))
                st.markdown(item["response"])
