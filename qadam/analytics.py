"""Метрики ребёнка, поиск проблем, адаптивный темп и критерий освоения."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta

from . import db

NEGATIVE_MOODS = {"Грустно", "Сердито", "Страшно", "Устал"}
MIN_ATTEMPTS = 8


def pct(part: float, whole: float) -> int:
    return round(100 * part / whole) if whole else 0


# ---------- Адаптивный темп (раннее вмешательство) ----------

def adaptive_level(attempts: list[dict]) -> dict:
    """Настройки следующего раунда по последним попыткам.

    Подсказанные ответы не считаются самостоятельными, поэтому успешность
    считается только по ответам без подсказки.
    """
    if len(attempts) < 4:
        return {"options": 2, "prompt": True, "session_size": 5, "accuracy": None,
                "message": "Начинаем с лёгкого уровня: два варианта и подсказка."}
    window = attempts[:10]
    last_options = window[0]["options_count"]
    independent = sum(1 for a in window if a["correct"] and not a["prompted"])
    accuracy = pct(independent, len(window))
    if accuracy >= 80:
        options = min(last_options + 1, 4)
        return {"options": options, "prompt": False, "session_size": 10, "accuracy": accuracy,
                "message": "Хороший результат: усложняем и убираем подсказку."}
    if accuracy >= 50:
        return {"options": last_options, "prompt": False, "session_size": 8, "accuracy": accuracy,
                "message": "Закрепляем на этом уровне без подсказки."}
    return {"options": max(last_options - 1, 2), "prompt": True, "session_size": 5, "accuracy": accuracy,
            "message": "Упрощаем: меньше вариантов, короче занятие, есть подсказка."}


# ---------- ABA ----------

def session_pct(s: dict) -> int:
    return pct(s["correct"], s["correct"] + s["prompted"] + s["incorrect"])


def is_mastered(sessions: list[dict], mastery_pct: int, mastery_sessions: int) -> bool:
    if len(sessions) < mastery_sessions:
        return False
    return all(session_pct(s) >= mastery_pct for s in sessions[-mastery_sessions:])


# ---------- Сводка за период ----------

def _top(counter: Counter, n: int = 5) -> list[dict]:
    return [{"name": k, "count": v} for k, v in counter.most_common(n) if k]


def snapshot(student_id: int, start: date, end: date) -> dict:
    a, b = start.isoformat(), (end + timedelta(days=1)).isoformat()
    span = (end - start).days + 1
    prev_a = (start - timedelta(days=span)).isoformat()
    prev_b = start.isoformat()
    return {
        "period": {"start": a, "end": end.isoformat(), "days": span},
        "cards": _cards(student_id, a, b, prev_a, prev_b),
        "aba": _aba(student_id, a, b),
        "behavior": _behavior(student_id, a, b, prev_a, prev_b),
        "mood": _mood(student_id, a, b),
        "schedules": _schedules(student_id, a, b),
        "aac": _aac(student_id, a, b, prev_a, prev_b),
    }


def _cards(sid, a, b, prev_a, prev_b) -> dict:
    rows = db.rows(
        "SELECT t.*, d.title deck, c.label FROM card_attempts t JOIN decks d ON d.id=t.deck_id"
        " JOIN cards c ON c.id=t.card_id WHERE t.student_id=? AND t.created_at>=? AND t.created_at<? ORDER BY t.id",
        [sid, a, b])
    prev = db.one("SELECT COUNT(*) n, SUM(correct AND NOT prompted) ok FROM card_attempts"
                  " WHERE student_id=? AND created_at>=? AND created_at<?", [sid, prev_a, prev_b])
    n = len(rows)
    ok = sum(1 for r in rows if r["correct"] and not r["prompted"])
    decks: dict[str, list] = {}
    labels: dict[str, list] = {}
    days: dict[str, list] = {}
    for r in rows:
        decks.setdefault(r["deck"], []).append(r)
        labels.setdefault(r["label"], []).append(r)
        days.setdefault(r["created_at"][:10], []).append(r)
    indep = lambda rs: pct(sum(1 for x in rs if x["correct"] and not x["prompted"]), len(rs))
    hardest = sorted(
        ({"label": k, "attempts": len(v), "accuracy": indep(v)} for k, v in labels.items() if len(v) >= 3),
        key=lambda x: x["accuracy"])[:5]
    return {
        "attempts": n, "accuracy": pct(ok, n),
        "prompted_rate": pct(sum(1 for r in rows if r["prompted"]), n),
        "avg_seconds": round(sum(r["response_ms"] for r in rows) / n / 1000, 1) if n else 0,
        "prev_attempts": prev["n"] or 0,
        "prev_accuracy": pct(prev["ok"] or 0, prev["n"] or 0),
        "by_deck": [{"deck": k, "attempts": len(v), "accuracy": indep(v),
                     "prompted_rate": pct(sum(1 for x in v if x["prompted"]), len(v))} for k, v in decks.items()],
        "hardest": hardest,
        "trend": [{"date": k, "accuracy": indep(v), "attempts": len(v)} for k, v in sorted(days.items())],
    }


def _aba(sid, a, b) -> dict:
    programs = db.rows("SELECT * FROM aba_programs WHERE student_id=? ORDER BY id", [sid])
    out, end_date = [], b
    for p in programs:
        sessions = db.rows("SELECT * FROM aba_sessions WHERE program_id=? AND session_date>=? AND session_date<?"
                           " ORDER BY session_date,id", [p["id"], a, end_date])
        values = [session_pct(s) for s in sessions]
        total = sum(s["correct"] + s["prompted"] + s["incorrect"] for s in sessions)
        out.append({
            "target": p["target"], "domain": p["domain"], "status": p["status"],
            "mastery_pct": p["mastery_pct"], "sessions": len(sessions), "trials": total,
            "first_pct": values[0] if values else None, "last_pct": values[-1] if values else None,
            "prompted_rate": pct(sum(s["prompted"] for s in sessions), total),
            "series": [{"date": s["session_date"], "pct": v} for s, v in zip(sessions, values)],
        })
    return {"programs": out}


def _behavior(sid, a, b, prev_a, prev_b) -> dict:
    rows = db.rows("SELECT * FROM behavior_logs WHERE student_id=? AND occurred_at>=? AND occurred_at<? ORDER BY occurred_at",
                   [sid, a, b])
    prev = db.one("SELECT COUNT(*) n FROM behavior_logs WHERE student_id=? AND occurred_at>=? AND occurred_at<?",
                  [sid, prev_a, prev_b])["n"]
    days = Counter(r["occurred_at"][:10] for r in rows)
    hours = Counter()
    for r in rows:
        try:
            hours[f"{datetime.fromisoformat(r['occurred_at']).hour:02d}:00"] += 1
        except ValueError:
            pass
    n = len(rows)
    return {
        "count": n, "prev_count": prev,
        "avg_intensity": round(sum(r["intensity"] for r in rows) / n, 1) if n else 0,
        "severe": sum(1 for r in rows if r["intensity"] >= 4),
        "avg_duration": round(sum(r["duration_min"] for r in rows) / n, 1) if n else 0,
        "top_behaviors": _top(Counter(r["behavior"] for r in rows)),
        "top_antecedents": _top(Counter(r["antecedent"] for r in rows)),
        "top_consequences": _top(Counter(r["consequence"] for r in rows)),
        "top_settings": _top(Counter(r["setting"] for r in rows)),
        "by_hour": _top(hours, 3),
        "by_day": [{"date": k, "count": v} for k, v in sorted(days.items())],
    }


def _mood(sid, a, b) -> dict:
    rows = db.rows("SELECT mood FROM mood_logs WHERE student_id=? AND created_at>=? AND created_at<?", [sid, a, b])
    counts = Counter(r["mood"] for r in rows)
    return {"count": len(rows), "counts": dict(counts),
            "negative_share": pct(sum(v for k, v in counts.items() if k in NEGATIVE_MOODS), len(rows))}


def _schedules(sid, a, b) -> dict:
    rows = db.rows("SELECT r.*, s.title FROM schedule_runs r JOIN schedules s ON s.id=r.schedule_id"
                   " WHERE r.student_id=? AND r.created_at>=? AND r.created_at<?", [sid, a, b])
    n = len(rows)
    full = sum(1 for r in rows if r["steps_done"] >= r["steps_total"])
    per: dict[str, list] = {}
    for r in rows:
        per.setdefault(r["title"], []).append(r)
    return {
        "runs": n, "completion_rate": pct(full, n),
        "help_per_run": round(sum(r["help_count"] for r in rows) / n, 1) if n else 0,
        "by_schedule": [{"title": k, "runs": len(v),
                         "completion_rate": pct(sum(1 for x in v if x["steps_done"] >= x["steps_total"]), len(v))}
                        for k, v in per.items()],
    }


def _aac(sid, a, b, prev_a, prev_b) -> dict:
    rows = db.rows("SELECT sentence,words FROM aac_log WHERE student_id=? AND created_at>=? AND created_at<?", [sid, a, b])
    prev = db.one("SELECT COUNT(*) n FROM aac_log WHERE student_id=? AND created_at>=? AND created_at<?",
                  [sid, prev_a, prev_b])["n"]
    n = len(rows)
    return {"phrases": n, "prev_phrases": prev,
            "avg_words": round(sum(r["words"] for r in rows) / n, 1) if n else 0,
            "top_phrases": _top(Counter(r["sentence"] for r in rows))}


# ---------- Поиск проблем ----------

def detect_problems(s: dict) -> list[dict]:
    problems: list[dict] = []

    def add(key, title, severity, domain, evidence):
        problems.append({"key": key, "title": title, "severity": severity, "domain": domain, "evidence": evidence})

    c = s["cards"]
    if c["attempts"] >= MIN_ATTEMPTS and c["accuracy"] < 60:
        add("cards_low", "Низкая самостоятельная точность в карточках", 3 if c["accuracy"] < 40 else 2, "обучение",
            [f"Самостоятельно верно {c['accuracy']}% из {c['attempts']} ответов."])
    if c["attempts"] >= MIN_ATTEMPTS and c["prompted_rate"] >= 60:
        add("prompt_dependence", "Зависимость от подсказок", 2, "обучение",
            [f"С подсказкой дано {c['prompted_rate']}% ответов."])
    for d in c["by_deck"]:
        if d["attempts"] >= MIN_ATTEMPTS and d["accuracy"] < 50 and c["accuracy"] >= 60:
            add(f"deck:{d['deck']}", f"Трудности с набором «{d['deck']}»", 2, "обучение",
                [f"Точность {d['accuracy']}% при {d['attempts']} ответах."])
    if c["hardest"] and c["hardest"][0]["accuracy"] < 40:
        names = ", ".join(f"{h['label']} ({h['accuracy']}%)" for h in c["hardest"] if h["accuracy"] < 40)
        add("hard_items", "Отдельные карточки не усваиваются", 1, "обучение", [f"Сложные понятия: {names}."])
    if c["prev_attempts"] >= MIN_ATTEMPTS and c["attempts"] >= MIN_ATTEMPTS and c["accuracy"] <= c["prev_accuracy"] - 15:
        add("cards_regress", "Снижение результатов", 3, "обучение",
            [f"Точность упала с {c['prev_accuracy']}% до {c['accuracy']}%."])

    for p in s["aba"]["programs"]:
        if p["status"] != "В работе" or p["sessions"] < 3:
            continue
        if p["last_pct"] <= p["first_pct"] and p["last_pct"] < p["mastery_pct"]:
            add(f"aba:{p['target']}", f"Нет прогресса по навыку «{p['target']}»", 2, "ABA",
                [f"{p['sessions']} сессий: от {p['first_pct']}% к {p['last_pct']}%, цель {p['mastery_pct']}%."])
        if p["prompted_rate"] >= 60:
            add(f"aba_prompt:{p['target']}", f"Навык «{p['target']}» выполняется только с подсказкой", 2, "ABA",
                [f"Подсказанных проб {p['prompted_rate']}% из {p['trials']}."])

    b = s["behavior"]
    if b["count"]:
        trigger = b["top_antecedents"][0] if b["top_antecedents"] else None
        behavior_name = b["top_behaviors"][0]["name"]
        days = max(s["period"]["days"], 1)
        if b["prev_count"] >= 2 and b["count"] >= b["prev_count"] * 1.5:
            add("behavior_up", "Рост нежелательного поведения", 3, "поведение",
                [f"Случаев: было {b['prev_count']}, стало {b['count']}.", f"Чаще всего: {behavior_name}."])
        if b["severe"]:
            add("behavior_severe", "Эпизоды высокой интенсивности", 3, "поведение",
                [f"{b['severe']} эпизодов с интенсивностью 4–5 из {b['count']}."])
        if trigger and trigger["count"] >= 3 and trigger["count"] / b["count"] >= 0.4:
            add("trigger", f"Устойчивый триггер: {trigger['name']}", 2, "поведение",
                [f"Предшествовал {trigger['count']} из {b['count']} случаев."])
        if b["count"] / days >= 1 and not any(p["key"] == "behavior_up" for p in problems):
            add("behavior_freq", "Частое нежелательное поведение", 2, "поведение",
                [f"В среднем {round(b['count'] / days, 1)} случая в день. Чаще всего: {behavior_name}."])

    m = s["mood"]
    if m["count"] >= 4 and m["negative_share"] >= 40:
        add("mood", "Преобладает негативное состояние", 2, "эмоции",
            [f"{m['negative_share']}% отметок негативные, всего {m['count']}: {m['counts']}."])

    r = s["schedules"]
    if r["runs"] >= 3 and r["completion_rate"] < 60:
        add("schedule", "Пошаговые задания не доводятся до конца", 2, "самостоятельность",
            [f"Завершено {r['completion_rate']}% из {r['runs']} запусков."])
    if r["runs"] >= 3 and r["help_per_run"] >= 2:
        add("schedule_help", "Высокая потребность в помощи при выполнении шагов", 1, "самостоятельность",
            [f"В среднем {r['help_per_run']} запросов помощи за запуск."])

    a = s["aac"]
    if a["phrases"] >= 5 and a["avg_words"] < 1.5:
        add("aac_short", "Коммуникация ограничена одиночными карточками", 1, "коммуникация",
            [f"Средняя длина фразы {a['avg_words']} карточки, всего фраз {a['phrases']}."])
    if a["prev_phrases"] >= 5 and a["phrases"] <= a["prev_phrases"] / 2:
        add("aac_drop", "Коммуникатор стали использовать реже", 2, "коммуникация",
            [f"Фраз: было {a['prev_phrases']}, стало {a['phrases']}."])

    total = c["attempts"] + r["runs"] + a["phrases"] + b["count"] + m["count"] + sum(p["sessions"] for p in s["aba"]["programs"])
    if total < 10:
        add("no_data", "Недостаточно данных для надёжных выводов", 1, "наблюдение",
            [f"За период всего {total} записей."])
    return sorted(problems, key=lambda p: -p["severity"])


def strengths(s: dict) -> list[str]:
    out = []
    c = s["cards"]
    if c["attempts"] >= MIN_ATTEMPTS and c["accuracy"] >= 80:
        out.append(f"Высокая самостоятельная точность в карточках: {c['accuracy']}%.")
    if c["prev_attempts"] >= MIN_ATTEMPTS and c["attempts"] >= MIN_ATTEMPTS and c["accuracy"] >= c["prev_accuracy"] + 10:
        out.append(f"Точность выросла с {c['prev_accuracy']}% до {c['accuracy']}%.")
    for p in s["aba"]["programs"]:
        if p["status"] == "Освоено":
            out.append(f"Освоен навык «{p['target']}».")
        elif p["sessions"] >= 3 and p["last_pct"] > p["first_pct"]:
            out.append(f"Рост по навыку «{p['target']}»: от {p['first_pct']}% к {p['last_pct']}%.")
    b = s["behavior"]
    if b["prev_count"] >= 2 and b["count"] <= b["prev_count"] / 2:
        out.append(f"Нежелательное поведение снизилось: было {b['prev_count']}, стало {b['count']}.")
    r = s["schedules"]
    if r["runs"] >= 3 and r["completion_rate"] >= 80:
        out.append(f"Пошаговые задания завершаются в {r['completion_rate']}% случаев.")
    a = s["aac"]
    if a["phrases"] >= 5 and a["avg_words"] >= 2:
        out.append(f"Строит фразы в среднем из {a['avg_words']} карточек.")
    return out
