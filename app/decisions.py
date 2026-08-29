"""Decisions and action items: create, edit, delete. No author: `owner` is a typed name."""

import sqlite3

from fastapi import WebSocket

from app import cards
from app.cards import MAX_TEXT, Rejected, ref, stripped
from app.clusters import PHASES

KINDS = ("decision", "action")
MAX_OWNER = 100


def rows(conn: sqlite3.Connection, session_id: int) -> list[dict]:
    """Every decision in the session, by id, as the four-key object."""
    return [
        dict(row)
        for row in conn.execute(
            "SELECT id, kind, text, owner FROM decisions WHERE session_id = ? ORDER BY id",
            (session_id,),
        )
    ]


def _event(conn: sqlite3.Connection, decision_id: int) -> dict:
    row = conn.execute(
        "SELECT id, kind, text, owner FROM decisions WHERE id = ?", (decision_id,)
    ).fetchone()
    return {"type": "decision", "decision": dict(row)}


def _fields(message: dict) -> tuple[str, str, str | None]:
    """kind, text and owner, validated the same way on create and on edit."""
    kind = message.get("kind")
    if kind not in KINDS:
        raise Rejected("kind must be decision or action")
    text = stripped(message, "text", MAX_TEXT)
    owner = message.get("owner")
    if owner is not None:
        if not isinstance(owner, str):
            raise Rejected("owner must be a string or null")
        owner = owner.strip() or None  # blank means no owner
        if owner and len(owner) > MAX_OWNER:
            raise Rejected(f"owner is over {MAX_OWNER} characters")
    return kind, text, owner


def _create(conn: sqlite3.Connection, session_id: int, ws: WebSocket, message: dict) -> dict:
    fields = _fields(message)
    with conn:
        decision_id = conn.execute(
            "INSERT INTO decisions (session_id, kind, text, owner) VALUES (?, ?, ?, ?)",
            (session_id, *fields),
        ).lastrowid
    return _event(conn, decision_id)


def _edit(conn: sqlite3.Connection, session_id: int, ws: WebSocket, message: dict) -> dict:
    decision_id = ref(conn, "decisions", session_id, message.get("id"))
    fields = _fields(message)
    with conn:
        conn.execute(
            "UPDATE decisions SET kind = ?, text = ?, owner = ? WHERE id = ?",
            (*fields, decision_id),
        )
    return _event(conn, decision_id)


def _delete(conn: sqlite3.Connection, session_id: int, ws: WebSocket, message: dict) -> dict:
    decision_id = ref(conn, "decisions", session_id, message.get("id"))
    with conn:
        conn.execute("DELETE FROM decisions WHERE id = ?", (decision_id,))
    return {"type": "decision.deleted", "id": decision_id}


HANDLERS = {"decision.create": _create, "decision.edit": _edit, "decision.delete": _delete}


def handle(conn: sqlite3.Connection, session_id: int, ws: WebSocket, message: dict):
    return cards.handle(conn, session_id, ws, message, HANDLERS, PHASES)
