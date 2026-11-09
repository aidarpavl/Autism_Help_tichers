import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qadam import db  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("QADAM_DB", str(tmp_path / "t.db"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GITHUB_BACKUP_REPO", raising=False)
    monkeypatch.delenv("GITHUB_BACKUP_TOKEN", raising=False)
    monkeypatch.delenv("TEACHER_SIGNUP_CODE", raising=False)
    db.set_write_hook(None)
    db.init_db()
    yield
