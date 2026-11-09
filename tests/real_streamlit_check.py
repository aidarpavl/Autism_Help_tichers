"""Открывает все страницы в настоящем Streamlit (запускается в GitHub Actions)."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["QADAM_DB"] = str(Path(tempfile.mkdtemp()) / "check.db")

from streamlit.testing.v1 import AppTest  # noqa: E402

from qadam import db  # noqa: E402
from qadam import services as sv  # noqa: E402

db.init_db()
teacher = sv.register_teacher("teacher", "password123", "Анна Учитель")
student = sv.teacher_create_student(teacher, "pupil", "secret1", "Тест Ученик")


def open_all(user_id: int) -> int:
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.session_state["user_id"] = user_id
    at.run()
    assert not at.exception, at.exception
    pages = at.radio(key="nav").options
    for page in pages:
        at.radio(key="nav").set_value(page).run()
        assert not at.exception, (page, at.exception)
    return len(pages)


login = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
assert not login.exception, login.exception
print("Открыто страниц:", open_all(teacher) + open_all(student))
