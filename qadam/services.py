"""Прикладные операции. Каждая проверяет, что объект принадлежит учителю."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from . import config, db, security

USERNAME_RE = re.compile(r"^[a-z0-9_.-]{3,32}$")
MAX_FAILED = 5
LOCK_MINUTES = 5
PROFILE_FIELDS = {"full_name", "email", "phone", "school", "specialization", "birth_date", "avatar"}
STUDENT_PROFILE_FIELDS = {
    "support_notes", "communication_level", "sensory", "interests", "reinforcers",
    "known_triggers", "strengths", "iep_goals", "parent_contact", "consent_ai",
}


class ValidationError(ValueError):
    """Сообщение можно показать пользователю."""


class AccessDenied(PermissionError):
    pass


# ---------- Пользователи ----------

def _clean_username(username: str) -> str:
    u = (username or "").strip().lower()
    if not USERNAME_RE.match(u):
        raise ValidationError("Логин: 3–32 символа, латинские буквы, цифры, точка, дефис или подчёркивание.")
    return u


def _check_password(password: str, minimum: int) -> None:
    if len(password or "") < minimum:
        raise ValidationError(f"Пароль должен быть не короче {minimum} символов.")


def _check_name(name: str) -> str:
    n = (name or "").strip()
    if len(n) < 2:
        raise ValidationError("Укажите имя.")
    return n


def _ensure_free(username: str) -> None:
    if db.one("SELECT 1 FROM users WHERE username=?", [username]):
        raise ValidationError("Такой логин уже занят.")


def _new_invite() -> str:
    while True:
        code = security.invite_code()
        if not db.one("SELECT 1 FROM users WHERE invite_code=?", [code]):
            return code


def register_teacher(username, password, full_name, email="", school="", signup_code="") -> int:
    required = config.get("TEACHER_SIGNUP_CODE")
    if required and signup_code.strip() != required:
        raise ValidationError("Неверный код регистрации учителя.")
    u = _clean_username(username)
    _check_password(password, 8)
    name = _check_name(full_name)
    _ensure_free(u)
    return db.run(
        "INSERT INTO users(role,username,password_hash,full_name,email,school,invite_code,created_at)"
        " VALUES('teacher',?,?,?,?,?,?,?)",
        [u, security.hash_password(password), name, email.strip(), school.strip(), _new_invite(), config.now_iso()],
    )


def _create_student(teacher_id, username, password, full_name, birth_date="") -> int:
    u = _clean_username(username)
    _check_password(password, 6)
    name = _check_name(full_name)
    _ensure_free(u)
    sid = db.run(
        "INSERT INTO users(role,username,password_hash,full_name,birth_date,teacher_id,created_at)"
        " VALUES('student',?,?,?,?,?,?)",
        [u, security.hash_password(password), name, birth_date, teacher_id, config.now_iso()],
    )
    db.run("INSERT INTO student_profiles(student_id) VALUES(?)", [sid])
    return sid


def register_student(username, password, full_name, invite_code, birth_date="") -> int:
    t = db.one("SELECT id FROM users WHERE role='teacher' AND invite_code=?",
               [(invite_code or "").strip().upper()])
    if not t:
        raise ValidationError("Код учителя не найден. Попросите код у своего учителя.")
    return _create_student(t["id"], username, password, full_name, birth_date)


def teacher_create_student(teacher_id, username, password, full_name, birth_date="") -> int:
    return _create_student(teacher_id, username, password, full_name, birth_date)


def authenticate(username: str, password: str) -> dict:
    u = (username or "").strip().lower()
    user = db.one("SELECT * FROM users WHERE username=?", [u])
    generic = ValidationError("Неверный логин или пароль.")
    if not user:
        security.hash_password(password or "")  # одинаковое время ответа
        raise generic
    if user["locked_until"] and user["locked_until"] > config.now_iso():
        raise ValidationError(f"Вход временно заблокирован. Попробуйте через {LOCK_MINUTES} минут.")
    if not security.verify_password(password or "", user["password_hash"]):
        failed = user["failed_logins"] + 1
        lock = ""
        if failed >= MAX_FAILED:
            lock = (config.now() + timedelta(minutes=LOCK_MINUTES)).isoformat(sep=" ")
            failed = 0
        db.run("UPDATE users SET failed_logins=?, locked_until=? WHERE id=?", [failed, lock, user["id"]])
        raise generic
    db.run("UPDATE users SET failed_logins=0, locked_until='' WHERE id=?", [user["id"]])
    return get_user(user["id"])


def get_user(user_id: int) -> dict | None:
    u = db.one("SELECT * FROM users WHERE id=?", [user_id])
    if u:
        u.pop("password_hash", None)
    return u


def update_profile(user_id: int, **fields) -> None:
    clean = {k: (v or "").strip() for k, v in fields.items() if k in PROFILE_FIELDS}
    if "full_name" in clean:
        clean["full_name"] = _check_name(clean["full_name"])
    if not clean:
        return
    columns = ",".join(f"{k}=?" for k in clean)
    db.run(f"UPDATE users SET {columns} WHERE id=?", [*clean.values(), user_id])


def change_username(user_id: int, new_username: str) -> None:
    u = _clean_username(new_username)
    other = db.one("SELECT id FROM users WHERE username=?", [u])
    if other and other["id"] != user_id:
        raise ValidationError("Такой логин уже занят.")
    db.run("UPDATE users SET username=? WHERE id=?", [u, user_id])


def change_password(user_id: int, current: str, new: str, repeat: str) -> None:
    row = db.one("SELECT role,password_hash FROM users WHERE id=?", [user_id])
    if not row or not security.verify_password(current or "", row["password_hash"]):
        raise ValidationError("Текущий пароль указан неверно.")
    if new != repeat:
        raise ValidationError("Новые пароли не совпадают.")
    _check_password(new, 8 if row["role"] == "teacher" else 6)
    db.run("UPDATE users SET password_hash=? WHERE id=?", [security.hash_password(new), user_id])


def reset_student_password(teacher_id: int, student_id: int, new: str) -> None:
    require_student(teacher_id, student_id)
    _check_password(new, 6)
    db.run("UPDATE users SET password_hash=?, failed_logins=0, locked_until='' WHERE id=?",
           [security.hash_password(new), student_id])


def regenerate_invite(teacher_id: int) -> str:
    code = _new_invite()
    db.run("UPDATE users SET invite_code=? WHERE id=? AND role='teacher'", [code, teacher_id])
    return code


def list_students(teacher_id: int) -> list[dict]:
    return db.rows(
        "SELECT id,username,full_name,birth_date,avatar FROM users"
        " WHERE role='student' AND teacher_id=? ORDER BY full_name", [teacher_id])


def require_student(teacher_id: int, student_id: int) -> dict:
    s = db.one("SELECT id,username,full_name,birth_date,avatar FROM users"
               " WHERE id=? AND role='student' AND teacher_id=?", [student_id, teacher_id])
    if not s:
        raise AccessDenied("Этот ученик не закреплён за вами.")
    return s


def delete_student(teacher_id: int, student_id: int) -> None:
    require_student(teacher_id, student_id)
    db.run("DELETE FROM users WHERE id=?", [student_id])


def get_student_profile(student_id: int) -> dict:
    return db.one("SELECT * FROM student_profiles WHERE student_id=?", [student_id]) or {}


def update_student_profile(teacher_id: int, student_id: int, **fields) -> None:
    require_student(teacher_id, student_id)
    clean = {k: v for k, v in fields.items() if k in STUDENT_PROFILE_FIELDS}
    if clean:
        columns = ",".join(f"{k}=?" for k in clean)
        db.run(f"UPDATE student_profiles SET {columns} WHERE student_id=?", [*clean.values(), student_id])


def _owned(table: str, obj_id: int, teacher_id: int) -> dict:
    assert table in {"ktp", "decks", "schedules", "aba_programs", "lesson_plans", "reports"}
    row = db.one(f"SELECT * FROM {table} WHERE id=? AND teacher_id=?", [obj_id, teacher_id])
    if not row:
        raise AccessDenied("Нет доступа к этому объекту.")
    return row


# ---------- КТП и планы ----------

def save_ktp(teacher_id, title, subject, grade, filename, topics: list[dict]) -> int:
    clean = [t for t in topics if _s(t.get("topic"))]
    if not clean:
        raise ValidationError("В плане нет ни одной темы.")
    kid = db.run(
        "INSERT INTO ktp(teacher_id,title,subject,grade,filename,created_at) VALUES(?,?,?,?,?,?)",
        [teacher_id, title.strip() or filename or "КТП", subject.strip(), grade.strip(), filename, config.now_iso()])
    replace_topics(teacher_id, kid, clean)
    return kid


def replace_topics(teacher_id: int, ktp_id: int, topics: list[dict]) -> None:
    """Обновляет темы, сохраняя id, чтобы планы и наборы не теряли связь."""
    _owned("ktp", ktp_id, teacher_id)
    existing = {r["id"] for r in db.rows("SELECT id FROM ktp_topics WHERE ktp_id=?", [ktp_id])}
    kept, statements, position = set(), [], 0
    for t in topics:
        topic = _s(t.get("topic"))
        if not topic:
            continue
        position += 1
        values = [position, _s(t.get("section")), topic, _s(t.get("goals")),
                  _s(t.get("lesson_date")), _s(t.get("hours"))]
        tid = _int_or_none(t.get("id"))
        if tid in existing:
            kept.add(tid)
            statements.append((
                "UPDATE ktp_topics SET position=?,section=?,topic=?,goals=?,lesson_date=?,hours=? WHERE id=?",
                [*values, tid]))
        else:
            statements.append((
                "INSERT INTO ktp_topics(position,section,topic,goals,lesson_date,hours,ktp_id) VALUES(?,?,?,?,?,?,?)",
                [*values, ktp_id]))
    statements += [("DELETE FROM ktp_topics WHERE id=?", [i]) for i in existing - kept]
    db.run_many(statements)


def _s(v) -> str:
    try:
        if v is None or bool(v != v):  # NaN из таблицы
            return ""
    except TypeError:  # pandas.NA
        return ""
    return str(v).strip()


def _int_or_none(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):  # None, NaN, NA
        return None


def list_ktp(teacher_id: int) -> list[dict]:
    return db.rows(
        "SELECT k.*, (SELECT COUNT(*) FROM ktp_topics t WHERE t.ktp_id=k.id) topics"
        " FROM ktp k WHERE teacher_id=? ORDER BY k.id DESC", [teacher_id])


def get_topics(teacher_id: int, ktp_id: int) -> list[dict]:
    _owned("ktp", ktp_id, teacher_id)
    return db.rows("SELECT * FROM ktp_topics WHERE ktp_id=? ORDER BY position", [ktp_id])


def delete_ktp(teacher_id: int, ktp_id: int) -> None:
    _owned("ktp", ktp_id, teacher_id)
    db.run("DELETE FROM ktp WHERE id=?", [ktp_id])


def get_ktp(teacher_id: int, ktp_id: int) -> dict:
    return _owned("ktp", ktp_id, teacher_id)


def save_lesson_plan(teacher_id, topic_id, student_id, title, content, source) -> int:
    if student_id:
        require_student(teacher_id, student_id)
    now = config.now_iso()
    return db.run(
        "INSERT INTO lesson_plans(teacher_id,topic_id,student_id,title,content,source,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?)", [teacher_id, topic_id, student_id, title, content, source, now, now])


def list_lesson_plans(teacher_id: int) -> list[dict]:
    return db.rows(
        "SELECT p.*, u.full_name student_name FROM lesson_plans p"
        " LEFT JOIN users u ON u.id=p.student_id WHERE p.teacher_id=? ORDER BY p.id DESC", [teacher_id])


def update_lesson_plan(teacher_id: int, plan_id: int, title: str, content: str) -> None:
    _owned("lesson_plans", plan_id, teacher_id)
    db.run("UPDATE lesson_plans SET title=?,content=?,updated_at=? WHERE id=?",
           [title, content, config.now_iso(), plan_id])


def delete_lesson_plan(teacher_id: int, plan_id: int) -> None:
    _owned("lesson_plans", plan_id, teacher_id)
    db.run("DELETE FROM lesson_plans WHERE id=?", [plan_id])


# ---------- Карточки ----------

def create_deck(teacher_id, title, description="", topic_id=None) -> int:
    if not title.strip():
        raise ValidationError("Укажите название набора.")
    return db.run("INSERT INTO decks(teacher_id,title,description,topic_id,created_at) VALUES(?,?,?,?,?)",
                  [teacher_id, title.strip(), description.strip(), topic_id, config.now_iso()])


def list_decks(teacher_id: int) -> list[dict]:
    return db.rows("SELECT d.*, (SELECT COUNT(*) FROM cards c WHERE c.deck_id=d.id) cards"
                   " FROM decks d WHERE teacher_id=? ORDER BY d.id DESC", [teacher_id])


def delete_deck(teacher_id: int, deck_id: int) -> None:
    _owned("decks", deck_id, teacher_id)
    db.run("DELETE FROM decks WHERE id=?", [deck_id])


def add_card(teacher_id, deck_id, label, emoji="🔹", question="", image=None, audio=None) -> int:
    _owned("decks", deck_id, teacher_id)
    if not label.strip():
        raise ValidationError("Укажите подпись карточки.")
    pos = db.one("SELECT COALESCE(MAX(position),0)+1 p FROM cards WHERE deck_id=?", [deck_id])["p"]
    return db.run("INSERT INTO cards(deck_id,position,emoji,label,question,image,audio) VALUES(?,?,?,?,?,?,?)",
                  [deck_id, pos, emoji.strip() or "🔹", label.strip(), question.strip(), image, audio])


def get_cards(deck_id: int) -> list[dict]:
    return db.rows("SELECT * FROM cards WHERE deck_id=? ORDER BY position", [deck_id])


def teacher_cards(teacher_id: int, deck_id: int) -> list[dict]:
    _owned("decks", deck_id, teacher_id)
    return get_cards(deck_id)


def delete_card(teacher_id: int, card_id: int) -> None:
    row = db.one("SELECT c.id FROM cards c JOIN decks d ON d.id=c.deck_id WHERE c.id=? AND d.teacher_id=?",
                 [card_id, teacher_id])
    if not row:
        raise AccessDenied("Нет доступа к карточке.")
    db.run("DELETE FROM cards WHERE id=?", [card_id])


def set_deck_students(teacher_id: int, deck_id: int, student_ids: list[int]) -> None:
    _owned("decks", deck_id, teacher_id)
    for s in student_ids:
        require_student(teacher_id, s)
    db.run_many([("DELETE FROM deck_assignments WHERE deck_id=?", [deck_id])] + [
        ("INSERT INTO deck_assignments(deck_id,student_id) VALUES(?,?)", [deck_id, s]) for s in student_ids])


def deck_students(deck_id: int) -> list[int]:
    return [r["student_id"] for r in db.rows("SELECT student_id FROM deck_assignments WHERE deck_id=?", [deck_id])]


def student_decks(student_id: int) -> list[dict]:
    return db.rows(
        "SELECT d.*, (SELECT COUNT(*) FROM cards c WHERE c.deck_id=d.id) cards FROM decks d"
        " JOIN deck_assignments a ON a.deck_id=d.id WHERE a.student_id=? ORDER BY d.id DESC", [student_id])


def require_deck_for_student(student_id: int, deck_id: int) -> None:
    if not db.one("SELECT 1 FROM deck_assignments WHERE student_id=? AND deck_id=?", [student_id, deck_id]):
        raise AccessDenied("Набор не назначен этому ученику.")


def record_attempt(student_id, deck_id, card_id, correct, prompted, options_count, response_ms=0) -> None:
    require_deck_for_student(student_id, deck_id)
    db.run("INSERT INTO card_attempts(student_id,deck_id,card_id,correct,prompted,options_count,response_ms,created_at)"
           " VALUES(?,?,?,?,?,?,?,?)",
           [student_id, deck_id, card_id, int(correct), int(prompted), options_count, int(response_ms), config.now_iso()])


def recent_attempts(student_id: int, deck_id: int, limit: int = 12) -> list[dict]:
    return db.rows("SELECT * FROM card_attempts WHERE student_id=? AND deck_id=? ORDER BY id DESC LIMIT ?",
                   [student_id, deck_id, limit])


# ---------- Коммуникатор ----------

def aac_for_student(student_id: int) -> list[dict]:
    me = db.one("SELECT teacher_id FROM users WHERE id=?", [student_id]) or {}
    return db.rows(
        "SELECT * FROM aac_items WHERE teacher_id IS NULL"
        " OR (teacher_id=? AND (student_id IS NULL OR student_id=?)) ORDER BY category,id",
        [me.get("teacher_id"), student_id])


def add_aac_item(teacher_id, category, emoji, label, student_id=None, image=None) -> int:
    if student_id:
        require_student(teacher_id, student_id)
    if not label.strip() or not category.strip():
        raise ValidationError("Укажите раздел и подпись.")
    return db.run("INSERT INTO aac_items(teacher_id,student_id,category,emoji,label,image) VALUES(?,?,?,?,?,?)",
                  [teacher_id, student_id, category.strip(), emoji.strip() or "💬", label.strip(), image])


def teacher_aac_items(teacher_id: int) -> list[dict]:
    return db.rows("SELECT a.*, u.full_name student_name FROM aac_items a LEFT JOIN users u ON u.id=a.student_id"
                   " WHERE a.teacher_id=? ORDER BY a.category,a.id", [teacher_id])


def delete_aac_item(teacher_id: int, item_id: int) -> None:
    db.run("DELETE FROM aac_items WHERE id=? AND teacher_id=?", [item_id, teacher_id])


def log_phrase(student_id: int, words: list[str]) -> None:
    if words:
        db.run("INSERT INTO aac_log(student_id,sentence,words,created_at) VALUES(?,?,?,?)",
               [student_id, " ".join(words), len(words), config.now_iso()])


def phrase_log(teacher_id: int, student_id: int, limit: int = 50) -> list[dict]:
    require_student(teacher_id, student_id)
    return db.rows("SELECT sentence,created_at FROM aac_log WHERE student_id=? ORDER BY id DESC LIMIT ?",
                   [student_id, limit])


# ---------- Пошаговые расписания ----------

def create_schedule(teacher_id, title, emoji="📋", description="") -> int:
    if not title.strip():
        raise ValidationError("Укажите название задания.")
    return db.run("INSERT INTO schedules(teacher_id,title,emoji,description,created_at) VALUES(?,?,?,?,?)",
                  [teacher_id, title.strip(), emoji.strip() or "📋", description.strip(), config.now_iso()])


def list_schedules(teacher_id: int) -> list[dict]:
    return db.rows("SELECT s.*, (SELECT COUNT(*) FROM schedule_steps x WHERE x.schedule_id=s.id) steps"
                   " FROM schedules s WHERE teacher_id=? ORDER BY s.id DESC", [teacher_id])


def delete_schedule(teacher_id: int, schedule_id: int) -> None:
    _owned("schedules", schedule_id, teacher_id)
    db.run("DELETE FROM schedules WHERE id=?", [schedule_id])


def add_step(teacher_id, schedule_id, text, emoji="▶️", minutes=0, image=None, audio=None) -> int:
    _owned("schedules", schedule_id, teacher_id)
    if not text.strip():
        raise ValidationError("Опишите шаг.")
    pos = db.one("SELECT COALESCE(MAX(position),0)+1 p FROM schedule_steps WHERE schedule_id=?", [schedule_id])["p"]
    return db.run("INSERT INTO schedule_steps(schedule_id,position,text,emoji,minutes,image,audio) VALUES(?,?,?,?,?,?,?)",
                  [schedule_id, pos, text.strip(), emoji.strip() or "▶️", int(minutes or 0), image, audio])


def get_steps(schedule_id: int) -> list[dict]:
    return db.rows("SELECT * FROM schedule_steps WHERE schedule_id=? ORDER BY position", [schedule_id])


def teacher_steps(teacher_id: int, schedule_id: int) -> list[dict]:
    _owned("schedules", schedule_id, teacher_id)
    return get_steps(schedule_id)


def delete_step(teacher_id: int, step_id: int) -> None:
    row = db.one("SELECT x.id FROM schedule_steps x JOIN schedules s ON s.id=x.schedule_id"
                 " WHERE x.id=? AND s.teacher_id=?", [step_id, teacher_id])
    if not row:
        raise AccessDenied("Нет доступа к шагу.")
    db.run("DELETE FROM schedule_steps WHERE id=?", [step_id])


def set_schedule_students(teacher_id: int, schedule_id: int, student_ids: list[int]) -> None:
    _owned("schedules", schedule_id, teacher_id)
    for s in student_ids:
        require_student(teacher_id, s)
    db.run_many([("DELETE FROM schedule_assignments WHERE schedule_id=?", [schedule_id])] + [
        ("INSERT INTO schedule_assignments(schedule_id,student_id) VALUES(?,?)", [schedule_id, s]) for s in student_ids])


def schedule_students(schedule_id: int) -> list[int]:
    return [r["student_id"] for r in db.rows("SELECT student_id FROM schedule_assignments WHERE schedule_id=?", [schedule_id])]


def student_schedules(student_id: int) -> list[dict]:
    return db.rows("SELECT s.* FROM schedules s JOIN schedule_assignments a ON a.schedule_id=s.id"
                   " WHERE a.student_id=? ORDER BY s.id DESC", [student_id])


def record_run(student_id, schedule_id, steps_done, steps_total, help_count=0) -> None:
    if not db.one("SELECT 1 FROM schedule_assignments WHERE student_id=? AND schedule_id=?", [student_id, schedule_id]):
        raise AccessDenied("Задание не назначено этому ученику.")
    db.run("INSERT INTO schedule_runs(schedule_id,student_id,steps_done,steps_total,help_count,created_at)"
           " VALUES(?,?,?,?,?,?)", [schedule_id, student_id, steps_done, steps_total, help_count, config.now_iso()])


# ---------- ABA ----------

def create_program(teacher_id, student_id, target, domain, mastery_pct=80, mastery_sessions=3) -> int:
    require_student(teacher_id, student_id)
    if not target.strip():
        raise ValidationError("Сформулируйте навык.")
    return db.run("INSERT INTO aba_programs(teacher_id,student_id,target,domain,mastery_pct,mastery_sessions,created_at)"
                  " VALUES(?,?,?,?,?,?,?)",
                  [teacher_id, student_id, target.strip(), domain, mastery_pct, mastery_sessions, config.now_iso()])


def list_programs(teacher_id: int, student_ids: list[int] | None = None, active_only=False) -> list[dict]:
    sql = ("SELECT p.*, u.full_name student_name FROM aba_programs p JOIN users u ON u.id=p.student_id"
           " WHERE p.teacher_id=?")
    params: list = [teacher_id]
    if student_ids is not None:
        if not student_ids:
            return []
        sql += f" AND p.student_id IN ({','.join('?' * len(student_ids))})"
        params += student_ids
    if active_only:
        sql += " AND p.status='В работе'"
    return db.rows(sql + " ORDER BY u.full_name,p.id", params)


def delete_program(teacher_id: int, program_id: int) -> None:
    _owned("aba_programs", program_id, teacher_id)
    db.run("DELETE FROM aba_programs WHERE id=?", [program_id])


def program_sessions(program_id: int) -> list[dict]:
    return db.rows("SELECT * FROM aba_sessions WHERE program_id=? ORDER BY session_date,id", [program_id])


def set_program_status(teacher_id: int, program_id: int, status: str) -> None:
    _owned("aba_programs", program_id, teacher_id)
    db.run("UPDATE aba_programs SET status=? WHERE id=?", [status, program_id])


def record_aba_session(teacher_id, program_id, session_date, correct, prompted, incorrect, notes="") -> bool:
    """Сохраняет сессию; возвращает True, если навык только что освоен."""
    from .analytics import is_mastered

    program = _owned("aba_programs", program_id, teacher_id)
    if correct + prompted + incorrect <= 0:
        raise ValidationError("В сессии нет ни одной пробы.")
    db.run("INSERT INTO aba_sessions(program_id,session_date,correct,prompted,incorrect,notes,created_at)"
           " VALUES(?,?,?,?,?,?,?)", [program_id, str(session_date), correct, prompted, incorrect, notes, config.now_iso()])
    if program["status"] == "В работе" and is_mastered(
            program_sessions(program_id), program["mastery_pct"], program["mastery_sessions"]):
        db.run("UPDATE aba_programs SET status='Освоено' WHERE id=?", [program_id])
        return True
    return False


# ---------- Поведение и настроение ----------

def add_behavior(teacher_id, student_id, occurred_at, setting, antecedent, behavior, consequence,
                 intensity, duration_min, notes="") -> int:
    require_student(teacher_id, student_id)
    if not behavior.strip():
        raise ValidationError("Опишите поведение.")
    return db.run(
        "INSERT INTO behavior_logs(student_id,author_id,occurred_at,setting,antecedent,behavior,consequence,"
        "intensity,duration_min,notes) VALUES(?,?,?,?,?,?,?,?,?,?)",
        [student_id, teacher_id, str(occurred_at), setting.strip(), antecedent.strip(), behavior.strip(),
         consequence.strip(), int(intensity), int(duration_min), notes.strip()])


def list_behavior(teacher_id: int, student_id: int, limit: int = 200) -> list[dict]:
    require_student(teacher_id, student_id)
    return db.rows("SELECT * FROM behavior_logs WHERE student_id=? ORDER BY occurred_at DESC LIMIT ?", [student_id, limit])


def add_mood(student_id: int, mood: str, source: str) -> None:
    db.run("INSERT INTO mood_logs(student_id,mood,source,created_at) VALUES(?,?,?,?)",
           [student_id, mood, source, config.now_iso()])


def recent_moods(student_id: int, days: int = 30) -> list[dict]:
    since = (config.now() - timedelta(days=days)).isoformat(sep=" ")
    return db.rows("SELECT mood,source,created_at FROM mood_logs WHERE student_id=? AND created_at>=? ORDER BY id",
                   [student_id, since])


# ---------- Отчёты ----------

def list_reports(teacher_id: int, student_id: int) -> list[dict]:
    require_student(teacher_id, student_id)
    return db.rows("SELECT * FROM reports WHERE teacher_id=? AND student_id=? ORDER BY id DESC", [teacher_id, student_id])


def report_prompts(teacher_id: int, report_id: int) -> list[dict]:
    _owned("reports", report_id, teacher_id)
    return db.rows("SELECT * FROM report_prompts WHERE report_id=? ORDER BY severity DESC,id", [report_id])


def update_prompt_result(teacher_id: int, prompt_id: int, prompt: str, response: str) -> None:
    row = db.one("SELECT p.id FROM report_prompts p JOIN reports r ON r.id=p.report_id"
                 " WHERE p.id=? AND r.teacher_id=?", [prompt_id, teacher_id])
    if not row:
        raise AccessDenied("Нет доступа к этому промту.")
    db.run("UPDATE report_prompts SET prompt=?,response=? WHERE id=?", [prompt, response, prompt_id])


def delete_report(teacher_id: int, report_id: int) -> None:
    _owned("reports", report_id, teacher_id)
    db.run("DELETE FROM reports WHERE id=?", [report_id])


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)
