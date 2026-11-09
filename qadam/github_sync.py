"""Резервная копия базы в отдельный ПРИВАТНЫЙ репозиторий GitHub (Contents API)."""
from __future__ import annotations

import base64
import json
import sqlite3
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

from . import config

API = "https://api.github.com"
FILE = "qadam.db"
DEBOUNCE_SECONDS = 30.0

_timer: threading.Timer | None = None
_lock = threading.Lock()
last_status = ""


class SyncError(RuntimeError):
    pass


def settings() -> tuple[str, str, str]:
    return (config.get("GITHUB_BACKUP_REPO"), config.get("GITHUB_BACKUP_TOKEN"),
            config.get("GITHUB_BACKUP_BRANCH", "main"))


def enabled() -> bool:
    repo, token, _ = settings()
    return bool(repo and token)


def _request(method: str, url: str, token: str, body: dict | None = None,
             accept: str = "application/vnd.github+json") -> bytes:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "qadam-app",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def _require_private(repo: str, token: str) -> None:
    info = json.loads(_request("GET", f"{API}/repos/{repo}", token))
    if not info.get("private"):
        raise SyncError("Репозиторий публичный. Данные детей можно хранить только в приватном.")


def _remote_sha(repo: str, token: str, branch: str) -> str | None:
    try:
        raw = _request("GET", f"{API}/repos/{repo}/contents/{FILE}?ref={branch}", token)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    return json.loads(raw).get("sha")


def backup_now() -> str:
    global last_status
    repo, token, branch = settings()
    if not (repo and token):
        raise SyncError("Резервное копирование не настроено.")
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / FILE
        src = sqlite3.connect(config.db_path())
        dst = sqlite3.connect(snapshot)
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()
        payload = snapshot.read_bytes()
    try:
        _require_private(repo, token)
        body = {"message": f"backup {config.now_iso()}", "branch": branch,
                "content": base64.b64encode(payload).decode()}
        sha = _remote_sha(repo, token, branch)
        if sha:
            body["sha"] = sha
        _request("PUT", f"{API}/repos/{repo}/contents/{FILE}", token, body)
    except urllib.error.HTTPError as e:
        raise SyncError(f"GitHub ответил ошибкой {e.code}.") from e
    except urllib.error.URLError as e:
        raise SyncError("Нет связи с GitHub.") from e
    last_status = f"Сохранено в GitHub: {config.now_iso()}"
    return last_status


def restore_if_missing() -> bool:
    """Скачать базу, если локальной нет (новый контейнер Streamlit Cloud)."""
    path = config.db_path()
    repo, token, branch = settings()
    if path.exists() or not (repo and token):
        return False
    try:
        _require_private(repo, token)
        raw = _request("GET", f"{API}/repos/{repo}/contents/{FILE}?ref={branch}", token,
                       accept="application/vnd.github.raw+json")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise SyncError(f"GitHub ответил ошибкой {e.code}.") from e
    except urllib.error.URLError as e:
        raise SyncError("Нет связи с GitHub.") from e
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return True


def _run_backup() -> None:
    global _timer, last_status
    with _lock:
        _timer = None
    try:
        backup_now()
    except Exception as e:  # фоновая задача не должна ронять приложение
        last_status = f"Ошибка резервной копии: {e}"


def schedule_backup() -> None:
    """Отложенная копия: серия записей даёт один коммит."""
    global _timer
    if not enabled():
        return
    with _lock:
        if _timer is None:
            _timer = threading.Timer(DEBOUNCE_SECONDS, _run_backup)
            _timer.daemon = True
            _timer.start()
