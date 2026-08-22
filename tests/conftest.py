import pytest
from fastapi.testclient import TestClient

from app import db as db_module
from app.main import app


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A real SQLite file with a fresh schema, for one test."""
    monkeypatch.setenv("RETRO_DB", str(tmp_path / "retro.db"))
    db_module.init()
    conn = db_module.connect()
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def client(db):
    """The app, against that same database."""
    with TestClient(app) as c:
        yield c
