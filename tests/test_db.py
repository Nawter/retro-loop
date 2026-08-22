import sqlite3

import pytest

from app import db as db_module

TABLES = {"sessions", "participants", "cards", "clusters", "votes", "decisions"}


def test_schema_creates_every_table(db):
    rows = db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    assert TABLES <= {row["name"] for row in rows}


def test_connection_uses_row_factory(db):
    assert db.execute("SELECT 1 AS one").fetchone()["one"] == 1


def test_connection_enables_foreign_keys_and_wal(db):
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_foreign_keys_are_enforced(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO participants (session_id, name, token) VALUES (999, 'nobody', 't')"
        )


def test_booting_twice_keeps_the_rows(db):
    db.execute("INSERT INTO sessions (code, facilitator_token) VALUES ('ABC234', 'tok')")
    db.commit()

    db_module.init()  # second boot against the same file

    assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1
    rows = db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    assert TABLES <= {row["name"] for row in rows}
