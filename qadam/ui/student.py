"""Кабинет ученика: крупные кнопки, мало текста, спокойная обратная связь."""
from __future__ import annotations

import random
import time

import streamlit as st

from .. import analytics, services as sv
from .common import MOODS, big, speak


def _wrap() -> None:
    st.markdown('<style>[data-testid="stMain"] .stButton button {min-height:96px}'
                '[data-testid="stMain"] .stButton button p {font-size:1.35rem}</style>',
                unsafe_allow_html=True)


def home(user: dict) -> None:
    _wrap()
    st.header(f"{user['avatar']} Привет, {user['full_name'].split()[0]}!")
    st.subheader("Как ты себя чувствуешь?")
    _mood_buttons(user, "home")
    decks = sv.student_decks(user["id"])
    schedules = sv.student_schedules(user["id"])
    st.subheader("Сегодня")
    if not decks and not schedules:
        st.info("Учитель ещё не добавил занятия.")
    for d in decks:
        st.write(f"🃏 {d['title']}")
    for s in schedules:
        st.write(f"{s['emoji']} {s['title']}")


def _mood_buttons(user: dict, key: str) -> None:
    cols = st.columns(3)
    for i, (name, emoji) in enumerate(MOODS.items()):
        if cols[i % 3].button(f"{emoji} {name}", key=f"mood_{key}_{name}"):
            sv.add_mood(user["id"], name, "ребёнок")
            st.session_state.mood_done = name
    if st.session_state.get("mood_done"):
        st.success(f"Спасибо! Ты отметил: {st.session_state.mood_done}")


def mood_page(user: dict) -> None:
    _wrap()
    st.header("Моё настроение")
    _mood_buttons(user, "page")


# ---------- Карточки ----------

def _new_question(game: dict) -> None:
    card = game["cards"][game["queue"][game["index"]]]
    others = [c for c in game["cards"].values() if c["id"] != card["id"]]
    random.shuffle(others)
    options = [card["id"]] + [c["id"] for c in others[:game["level"]["options"] - 1]]
    random.shuffle(options)
    game["options"] = options
    game["shown_at"] = time.time()
    game["feedback"] = None


def _start_game(user: dict, deck: dict) -> None:
    cards = {c["id"]: c for c in sv.get_cards(deck["id"])}
    level = analytics.adaptive_level(sv.recent_attempts(user["id"], deck["id"]))
    ids = list(cards)
    random.shuffle(ids)
    queue = [ids[i % len(ids)] for i in range(level["session_size"])]
    game = {"deck": deck, "cards": cards, "level": level, "queue": queue, "index": 0, "right": 0, "finished": False}
    _new_question(game)
    st.session_state.game = game


def cards_page(user: dict) -> None:
    _wrap()
    st.header("🃏 Карточки")
    game = st.session_state.get("game")
    if not game:
        decks = [d for d in sv.student_decks(user["id"]) if d["cards"] >= 2]
        if not decks:
            st.info("Учитель ещё не добавил карточки.")
            return
        for d in decks:
            if st.button(f"🃏 {d['title']}", key=f"deck_{d['id']}"):
                _start_game(user, d)
                st.rerun()
        return

    if game["finished"]:
        big("⭐", f"Готово! Верно: {game['right']} из {len(game['queue'])}")
        reinforcer = sv.get_student_profile(user["id"]).get("reinforcers")
        if reinforcer:
            st.info(f"Теперь можно выбрать: {reinforcer}")
        if st.button("Выйти", key="game_exit"):
            del st.session_state.game
            st.rerun()
        return

    st.progress(game["index"] / len(game["queue"]), f"{game['index'] + 1} из {len(game['queue'])}")
    card = game["cards"][game["queue"][game["index"]]]
    instruction = card["question"] or f"Покажи: {card['label']}"
    st.markdown(f'<div class="big-text">{_escape(instruction)}</div>', unsafe_allow_html=True)
    speak(instruction)
    if card["audio"]:
        st.audio(card["audio"])

    feedback = game["feedback"]
    if feedback:
        if feedback == "right":
            big("👍", "Верно!")
        else:
            st.info(f"Правильный ответ: {card['emoji']} {card['label']}")
            speak(card["label"], "🔊 Как звучит")
        if st.button("Дальше ➡️", key="game_next"):
            game["index"] += 1
            if game["index"] >= len(game["queue"]):
                game["finished"] = True
            else:
                _new_question(game)
            st.rerun()
        return

    columns = st.columns(len(game["options"]))
    for col, option_id in zip(columns, game["options"]):
        option = game["cards"][option_id]
        with col:
            if option["image"]:
                st.image(option["image"])
            hint = "👉 " if game["level"]["prompt"] and option_id == card["id"] else ""
            if st.button(f"{hint}{option['emoji']} {option['label']}", key=f"opt_{game['index']}_{option_id}"):
                correct = option_id == card["id"]
                ms = int((time.time() - game["shown_at"]) * 1000)
                sv.record_attempt(user["id"], game["deck"]["id"], card["id"], correct,
                                  game["level"]["prompt"], len(game["options"]), ms)
                game["right"] += int(correct)
                game["feedback"] = "right" if correct else "wrong"
                st.rerun()
    if st.button("⏸️ Закончить", key="game_stop"):
        del st.session_state.game
        st.rerun()


def _escape(text: str) -> str:
    import html

    return html.escape(text)


# ---------- Коммуникатор ----------

def communicator(user: dict) -> None:
    _wrap()
    st.header("💬 Хочу сказать")
    strip = st.session_state.setdefault("strip", [])
    st.markdown(f'<div class="strip">{_escape(" ".join(strip)) or "&nbsp;"}</div>', unsafe_allow_html=True)
    if strip:
        speak(" ".join(strip), "🔊 Сказать")
    a, b, c = st.columns(3)
    if a.button("⬅️ Убрать", key="strip_back") and strip:
        strip.pop()
        st.rerun()
    if b.button("🗑️ Очистить", key="strip_clear"):
        strip.clear()
        st.rerun()
    if c.button("✅ Показать учителю", key="strip_send") and strip:
        sv.log_phrase(user["id"], strip)
        strip.clear()
        st.success("Учитель увидит твою фразу.")

    items = sv.aac_for_student(user["id"])
    categories = list(dict.fromkeys(i["category"] for i in items))
    if not categories:
        return
    for tab, category in zip(st.tabs(categories), categories):
        with tab:
            group = [i for i in items if i["category"] == category]
            for start in range(0, len(group), 4):
                for col, item in zip(st.columns(4), group[start:start + 4]):
                    with col:
                        if item["image"]:
                            st.image(item["image"])
                        if st.button(f"{item['emoji']} {item['label']}", key=f"aac_{item['id']}"):
                            strip.append(item["label"])
                            st.rerun()


# ---------- Пошаговые задания ----------

def schedule_page(user: dict) -> None:
    _wrap()
    st.header("📋 Мои задания")
    run = st.session_state.get("run")
    if not run:
        schedules = sv.student_schedules(user["id"])
        if not schedules:
            st.info("Учитель ещё не добавил задания.")
            return
        for s in schedules:
            if st.button(f"{s['emoji']} {s['title']}", key=f"run_{s['id']}"):
                steps = sv.get_steps(s["id"])
                if not steps:
                    st.info("В этом задании пока нет шагов.")
                else:
                    st.session_state.run = {"schedule": s, "steps": steps, "index": 0, "help": 0}
                    st.rerun()
        return

    steps, i = run["steps"], run["index"]

    def finish(done: int) -> None:
        sv.record_run(user["id"], run["schedule"]["id"], done, len(steps), run["help"])
        st.session_state.run_done = done == len(steps)
        del st.session_state.run

    step = steps[i]
    st.progress(i / len(steps), f"Шаг {i + 1} из {len(steps)}")
    if step["image"]:
        st.image(step["image"], width=360)
    else:
        big(step["emoji"])
    st.markdown(f'<div class="big-text">{_escape(step["text"])}</div>', unsafe_allow_html=True)
    if step["minutes"]:
        st.caption(f"Примерно {step['minutes']} мин")
    if step["audio"]:
        st.audio(step["audio"])
    speak(step["text"])
    if st.button("✅ Сделал", key=f"step_ok_{i}"):
        if i + 1 >= len(steps):
            finish(len(steps))
        else:
            run["index"] += 1
        st.rerun()
    a, b = st.columns(2)
    if a.button("🤝 Нужна помощь", key=f"step_help_{i}"):
        run["help"] += 1
        st.info("Позови учителя или покажи карточку «Помоги».")
    if b.button("⏸️ Закончить", key=f"step_stop_{i}"):
        finish(i)
        st.rerun()
