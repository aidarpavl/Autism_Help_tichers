import base64
import io
import json
from datetime import timedelta

import pytest
from docx import Document
from openpyxl import Workbook

from qadam import ai, analytics, config, db, exporter, github_sync, ktp_parser, security
from qadam import services as sv


def teacher(name="teacher1"):
    return sv.register_teacher(name, "password123", "Анна Учитель")


def student(tid, name="pupil1", full="Айбек Серіков"):
    return sv.teacher_create_student(tid, name, "secret1", full)


# ---------- Вход ----------

def test_password_hash_roundtrip():
    h = security.hash_password("тест-пароль")
    assert "тест-пароль" not in h
    assert security.verify_password("тест-пароль", h)
    assert not security.verify_password("другой", h)
    assert not security.verify_password("x", "мусор")


def test_register_login_and_invite():
    t = teacher()
    code = sv.get_user(t)["invite_code"]
    s = sv.register_student("Kid_1", "secret1", "Ребёнок Тест", code.lower())
    assert sv.get_user(s)["teacher_id"] == t
    assert sv.authenticate("kid_1", "secret1")["id"] == s
    assert "password_hash" not in sv.get_user(s)
    with pytest.raises(sv.ValidationError):
        sv.register_student("kid2", "secret1", "Ребёнок", "NOCODE")
    with pytest.raises(sv.ValidationError):
        sv.register_teacher("teacher1", "password123", "Дубль")
    with pytest.raises(sv.ValidationError):
        sv.register_teacher("short", "1", "Имя")


def test_signup_code(monkeypatch):
    monkeypatch.setenv("TEACHER_SIGNUP_CODE", "SCHOOL")
    with pytest.raises(sv.ValidationError):
        sv.register_teacher("teach", "password123", "Имя Ф", signup_code="bad")
    assert sv.register_teacher("teach", "password123", "Имя Ф", signup_code="SCHOOL")


def test_lockout_after_failures():
    teacher()
    for _ in range(sv.MAX_FAILED):
        with pytest.raises(sv.ValidationError):
            sv.authenticate("teacher1", "wrong")
    with pytest.raises(sv.ValidationError, match="заблокирован"):
        sv.authenticate("teacher1", "password123")


def test_profile_and_password_change():
    t = teacher()
    sv.update_profile(t, full_name="Новое Имя", school="Школа 5", role="student", password_hash="x")
    u = sv.get_user(t)
    assert (u["full_name"], u["school"], u["role"]) == ("Новое Имя", "Школа 5", "teacher")
    with pytest.raises(sv.ValidationError):
        sv.change_password(t, "wrong", "newpassword1", "newpassword1")
    with pytest.raises(sv.ValidationError):
        sv.change_password(t, "password123", "newpassword1", "different")
    sv.change_password(t, "password123", "newpassword1", "newpassword1")
    assert sv.authenticate("teacher1", "newpassword1")


# ---------- Разграничение доступа ----------

def test_teacher_cannot_touch_foreign_data():
    a, b = teacher("aaa"), teacher("bbb")
    s = student(a)
    deck = sv.create_deck(a, "Фрукты")
    prog = sv.create_program(a, s, "Навык", "Игра")
    ktp = sv.save_ktp(a, "К", "", "", "", [{"topic": "Т"}])
    for call in [
        lambda: sv.update_student_profile(b, s, interests="x"),
        lambda: sv.reset_student_password(b, s, "hacked1"),
        lambda: sv.delete_student(b, s),
        lambda: sv.teacher_cards(b, deck),
        lambda: sv.add_card(b, deck, "x"),
        lambda: sv.set_deck_students(b, deck, []),
        lambda: sv.record_aba_session(b, prog, "2026-01-01", 1, 0, 0),
        lambda: sv.get_topics(b, ktp),
        lambda: sv.add_behavior(b, s, "2026-01-01 10:00:00", "", "", "x", "", 1, 0),
        lambda: ai.generate_report(b, s, config.now().date(), config.now().date()),
    ]:
        with pytest.raises(sv.AccessDenied):
            call()


def test_student_only_gets_assigned_deck():
    t = teacher()
    s = student(t)
    deck = sv.create_deck(t, "Набор")
    card = sv.add_card(t, deck, "Яблоко")
    with pytest.raises(sv.AccessDenied):
        sv.record_attempt(s, deck, card, True, False, 2)
    sv.set_deck_students(t, deck, [s])
    sv.record_attempt(s, deck, card, True, False, 2)
    assert len(sv.recent_attempts(s, deck)) == 1


# ---------- КТП ----------

def _xlsx():
    wb = Workbook()
    ws = wb.active
    ws.append(["Календарно-тематический план"])
    ws.append([])
    ws.append(["№", "Раздел", "Тема урока", "Цели обучения", "Кол-во часов", "Дата"])
    ws.append([1, "Цвета", "Красный цвет", "Узнаёт красный", 1, "01.09"])
    ws.append([2, "", "Синий цвет", "Узнаёт синий", 1, "03.09"])
    ws.append(["Итого", "", "", "", 2, ""])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def test_parse_xlsx():
    t = ktp_parser.parse("plan.xlsx", _xlsx())
    assert [x["topic"] for x in t] == ["Красный цвет", "Синий цвет"]
    assert t[0]["goals"] == "Узнаёт красный" and t[0]["section"] == "Цвета" and t[1]["section"] == "Цвета"
    assert t[1]["lesson_date"] == "03.09" and t[0]["hours"] == "1"


def test_parse_docx_table_and_kazakh_header():
    doc = Document()
    table = doc.add_table(rows=2, cols=3)
    for i, h in enumerate(["Сабақ тақырыбы", "Оқу мақсаты", "Күні"]):
        table.cell(0, i).text = h
    for i, v in enumerate(["Түстер", "Түстерді ажырату", "05.09"]):
        table.cell(1, i).text = v
    out = io.BytesIO()
    doc.save(out)
    t = ktp_parser.parse("p.docx", out.getvalue())
    assert t == [{"section": "", "topic": "Түстер", "goals": "Түстерді ажырату", "lesson_date": "05.09", "hours": ""}]


def test_parse_csv_cp1251_semicolon():
    data = "Тема;Цель\nЖивотные;Называет животных\n".encode("cp1251")
    assert ktp_parser.parse("p.csv", data)[0]["topic"] == "Животные"


def test_parse_txt_numbered_and_errors():
    t = ktp_parser.parse("p.txt", "Раздел 1. Осень\n1. Листья\n2) Дождь\nпросто текст".encode())
    assert [x["topic"] for x in t] == ["Листья", "Дождь"] and t[0]["section"].startswith("Раздел")
    with pytest.raises(ktp_parser.ParseError):
        ktp_parser.parse("p.exe", b"x")
    with pytest.raises(ktp_parser.ParseError):
        ktp_parser.parse("p.xlsx", b"not excel")
    with pytest.raises(ktp_parser.ParseError):
        ktp_parser.parse("p.txt", "нет тем".encode())


def test_replace_topics_keeps_ids_and_ignores_nan():
    t = teacher()
    k = sv.save_ktp(t, "К", "", "", "", [{"topic": "А"}, {"topic": "Б"}, {"topic": float("nan")}])
    rows = sv.get_topics(t, k)
    assert len(rows) == 2
    plan = sv.save_lesson_plan(t, rows[1]["id"], None, "Б", "текст", "template")
    sv.replace_topics(t, k, [{"id": rows[1]["id"], "topic": "Б2", "goals": float("nan")}, {"id": float("nan"), "topic": "В"}])
    new = sv.get_topics(t, k)
    assert [x["topic"] for x in new] == ["Б2", "В"] and new[0]["id"] == rows[1]["id"] and new[0]["goals"] == ""
    assert db.one("SELECT topic_id FROM lesson_plans WHERE id=?", [plan])["topic_id"] == rows[1]["id"]


# ---------- Темп и ABA ----------

def attempts(n, correct, prompted=False, options=3):
    return [{"correct": correct, "prompted": prompted, "options_count": options}] * n


def test_adaptive_level():
    assert analytics.adaptive_level([])["options"] == 2
    up = analytics.adaptive_level(attempts(10, True))
    assert up["options"] == 4 and up["prompt"] is False
    assert analytics.adaptive_level(attempts(10, True, options=4))["options"] == 4
    down = analytics.adaptive_level(attempts(10, False))
    assert down["options"] == 2 and down["prompt"] is True
    # верно, но с подсказкой — это не самостоятельный успех, усложнять нельзя
    assert analytics.adaptive_level(attempts(10, True, prompted=True))["prompt"] is True


def test_mastery_and_status():
    t = teacher()
    s = student(t)
    p = sv.create_program(t, s, "Навык", "Игра", 80, 3)
    assert sv.record_aba_session(t, p, "2026-01-01", 9, 1, 0) is False
    assert sv.record_aba_session(t, p, "2026-01-02", 8, 1, 1) is False
    assert sv.record_aba_session(t, p, "2026-01-03", 8, 2, 0) is True
    assert db.one("SELECT status FROM aba_programs WHERE id=?", [p])["status"] == "Освоено"
    with pytest.raises(sv.ValidationError):
        sv.record_aba_session(t, p, "2026-01-04", 0, 0, 0)


# ---------- Аналитика и отчёт ----------

def rich_student():
    t = teacher()
    s = student(t)
    sv.update_student_profile(t, s, interests="Айбек любит поезда", parent_contact="+7 700 000 00 00",
                              reinforcers="мыльные пузыри", consent_ai=1)
    deck = sv.create_deck(t, "Цвета")
    cards = [sv.add_card(t, deck, n) for n in ("Красный", "Синий")]
    sv.set_deck_students(t, deck, [s])
    for i in range(12):
        sv.record_attempt(s, deck, cards[i % 2], i % 4 == 0, True, 2, 3000)
    now = config.now()
    for i in range(5):
        sv.add_behavior(t, s, (now - timedelta(hours=i)).isoformat(sep=" "), "Класс", "Переход между занятиями",
                        "Крик / плач", "Перерыв", 4 if i == 0 else 2, 3)
    for _ in range(4):
        sv.add_mood(s, "Грустно", "ребёнок")
    return t, s, now.date()


def test_detect_problems():
    _, s, today = rich_student()
    snap = analytics.snapshot(s, today - timedelta(days=6), today)
    assert snap["cards"]["attempts"] == 12 and snap["cards"]["prompted_rate"] == 100
    keys = {p["key"] for p in analytics.detect_problems(snap)}
    assert {"cards_low", "prompt_dependence", "behavior_severe", "trigger", "mood"} <= keys
    severities = [p["severity"] for p in analytics.detect_problems(snap)]
    assert severities == sorted(severities, reverse=True)


def test_empty_student_reports_lack_of_data():
    t = teacher()
    s = student(t)
    today = config.now().date()
    assert [p["key"] for p in analytics.detect_problems(analytics.snapshot(s, today, today))] == ["no_data"]


def test_report_without_ai_has_prompt_per_problem():
    t, s, today = rich_student()
    rid = ai.generate_report(t, s, today - timedelta(days=6), today)
    report = db.one("SELECT * FROM reports WHERE id=?", [rid])
    prompts = sv.report_prompts(t, rid)
    assert report["source"] == "template" and len(prompts) >= 4
    assert len({p["prompt"] for p in prompts}) == len(prompts)
    assert "Айбек Серіков" in report["content"] and "ИИ не подключён" in report["content"]


def test_report_with_ai_is_anonymised(monkeypatch):
    t, s, today = rich_student()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    sent = []

    def fake(system, user, max_tokens=1500):
        sent.append(system + user)
        return "ПРОМТ-ИЛИ-ОТВЕТ"

    monkeypatch.setattr(ai, "call_llm", fake)
    rid = ai.generate_report(t, s, today - timedelta(days=6), today)
    blob = "\n".join(sent)
    assert sent and "Айбек" not in blob and "Серіков" not in blob and "+7 700" not in blob
    assert "поезда" in blob
    assert db.one("SELECT source FROM reports WHERE id=?", [rid])["source"] == "ai"
    assert sv.report_prompts(t, rid)[0]["prompt"] == "ПРОМТ-ИЛИ-ОТВЕТ"


def test_no_consent_means_no_ai(monkeypatch):
    t, s, today = rich_student()
    sv.update_student_profile(t, s, consent_ai=0)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

    def boom(*a, **k):
        raise AssertionError("ИИ не должен вызываться без согласия")

    monkeypatch.setattr(ai, "call_llm", boom)
    rid = ai.generate_report(t, s, today, today)
    assert "согласии" in db.one("SELECT content FROM reports WHERE id=?", [rid])["content"]
    content, source = ai.generate_lesson_plan("Математика", "1", "Счёт", "до 5", 40, s)
    assert source == "template" and "Счёт" in content


def test_ai_failure_falls_back(monkeypatch):
    t, s, today = rich_student()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

    def fail(*a, **k):
        raise ai.AIError("нет связи")

    monkeypatch.setattr(ai, "call_llm", fail)
    rid = ai.generate_report(t, s, today - timedelta(days=6), today)
    r = db.one("SELECT * FROM reports WHERE id=?", [rid])
    assert r["source"] == "template" and "не ответил" in r["content"]


def test_cards_generation(monkeypatch):
    assert ai.generate_cards("Домашние животные", "Называет корову и козу")[1] == "template"
    assert ai.generate_cards("Домашние животные", "Называет корову и козу")[0]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(ai, "call_llm", lambda *a, **k: 'Вот: [{"emoji":"🐄","label":"Корова","question":"Покажи корову"},{"bad":1}]')
    cards, source = ai.generate_cards("Т", "Ц")
    assert source == "ai" and cards == [{"emoji": "🐄", "label": "Корова", "question": "Покажи корову"}]
    monkeypatch.setattr(ai, "call_llm", lambda *a, **k: "не json")
    assert ai.generate_cards("Домашние животные", "")[1] == "template"


def test_lesson_template_timing_and_docx():
    text = ai.template_lesson("Мир", "1", "Осень", "Называет", 40, {})
    assert "## Ход урока" in text
    doc = Document(io.BytesIO(exporter.markdown_to_docx(text)))
    assert doc.tables and len(doc.tables[0].rows) == 7
    assert any("Осень" in p.text for p in doc.paragraphs)


# ---------- GitHub ----------

class FakeGitHub:
    def __init__(self, private=True):
        self.private, self.file, self.sha = private, None, None

    def __call__(self, method, url, token, body=None, accept=""):
        if url.endswith("/repos/me/data"):
            return json.dumps({"private": self.private}).encode()
        if method == "GET":
            if self.file is None:
                import urllib.error
                raise urllib.error.HTTPError(url, 404, "nf", {}, None)
            return self.file if "raw" in accept else json.dumps({"sha": "abc"}).encode()
        self.sha = body.get("sha")
        self.file = base64.b64decode(body["content"])
        return b"{}"


def test_backup_and_restore(monkeypatch):
    monkeypatch.setenv("GITHUB_BACKUP_REPO", "me/data")
    monkeypatch.setenv("GITHUB_BACKUP_TOKEN", "t")
    gh = FakeGitHub()
    monkeypatch.setattr(github_sync, "_request", gh)
    t = teacher()
    github_sync.backup_now()
    assert gh.file.startswith(b"SQLite format 3") and gh.sha is None
    github_sync.backup_now()
    assert gh.sha == "abc"
    new = config.db_path().parent / "restored.db"
    monkeypatch.setenv("QADAM_DB", str(new))
    assert github_sync.restore_if_missing() is True
    assert sv.get_user(t)["username"] == "teacher1"
    assert github_sync.restore_if_missing() is False


def test_backup_refuses_public_repo(monkeypatch):
    monkeypatch.setenv("GITHUB_BACKUP_REPO", "me/data")
    monkeypatch.setenv("GITHUB_BACKUP_TOKEN", "t")
    gh = FakeGitHub(private=False)
    monkeypatch.setattr(github_sync, "_request", gh)
    with pytest.raises(github_sync.SyncError, match="публичный"):
        github_sync.backup_now()
    assert gh.file is None
