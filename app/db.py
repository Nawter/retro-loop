"""SQLite access. `schema.sql` is the source of truth."""

import os
import sqlite3
from pathlib import Path

SCHEMA = Path(__file__).with_name("schema.sql")


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open a connection with the pragmas this app assumes everywhere."""
    conn = sqlite3.connect(path or os.environ.get("RETRO_DB", "retro.db"))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")  # per connection, so set it every time
    conn.execute("PRAGMA journal_mode = WAL")  # stored in the file, so a no-op after the first
    return conn


def init(path: str | Path | None = None) -> None:
    """Create anything missing. Safe to run on every boot."""
    conn = connect(path)
    try:
        conn.executescript(SCHEMA.read_text())
    finally:
        conn.close()
