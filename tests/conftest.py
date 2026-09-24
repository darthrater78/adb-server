import os
import sys
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parent.parent / "app"

# auth.py fails closed at import without these, so set them before any app
# module is imported.
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("APP_USERNAME", "operator")
os.environ.setdefault("APP_PASSWORD", "correct-horse-battery")
os.environ.setdefault("ALLOWED_HOSTS", "testserver")
os.environ.setdefault("COOKIE_SECURE", "false")
sys.path.insert(0, str(APP_DIR))
os.chdir(APP_DIR)  # main.py mounts "static" and "templates" relative to cwd

import auth  # noqa: E402
import db  # noqa: E402
import staging  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setattr(staging, "STAGING_ROOT", str(tmp_path / "staging"))
    db.init_db()
    if "main" in sys.modules:  # the display time zone is cached per process
        sys.modules["main"]._display_zone.clear()
        sys.modules["main"]._clock.clear()
    yield


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    import main

    # No context manager: skips lifespan, so the poll scheduler never starts.
    return TestClient(main.app)


CSRF = "test-csrf-token"


@pytest.fixture
def authed(client):
    client.cookies.set(auth.COOKIE_NAME, auth._serializer.dumps({"authenticated": True, "csrf": CSRF, "epoch": db.get_session_epoch()}))
    return client
