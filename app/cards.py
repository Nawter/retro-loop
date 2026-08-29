"""Cards: validate, persist, serialise, and filter per recipient."""

import sqlite3
from collections.abc import Callable

from fastapi import WebSocket

COLUMNS = ("start", "stop", "continue")
MAX_TEXT = 500

# Every read joins the author, so a card always knows its name.
SELECT = (
    "SELECT cards.*, participants.name AS author FROM cards "
    "JOIN participants ON participants.id = cards.participant_id "
)


class Rejected(Exception):
    """Why a message was refused; the detail goes back to the sender."""


def stripped(message: dict, key: str, limit: int) -> str:
    """`message[key]` as a non-empty string of at most `limit` characters, stripped, or Rejected."""
    value = message.get(key)
    if not isinstance(value, str):
        raise Rejected(f"{key} must be a string")
    value = value.strip()
    if not value:
        raise Rejected(f"{key} is empty")
    if len(value) > limit:
        raise Rejected(f"{key} is over {limit} characters")
    return value


def ident(value, what: str = "id") -> int:
    """`value` as a SQLite rowid, or Rejected."""
    # bool is an int in Python; a SQLite rowid is a positive signed 64-bit int, so anything
    # outside that range is refused here rather than crashing in the bind below
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 2**63:
        raise Rejected(f"{what} must be a positive integer")
    return value


def ref(conn: sqlite3.Connection, table: str, session_id: int, value, what: str = "id") -> int:
    """`value` as the id of a row of `table` in this session, or Rejected."""
    row_id = ident(value, what)
    if conn.execute(
        f"SELECT 1 FROM {table} WHERE id = ? AND session_id = ?", (row_id, session_id)
    ).fetchone() is None:
        raise Rejected(f"no such {table[:-1]} in this session")
    return row_id


def serialise(row: sqlite3.Row, ws: WebSocket) -> dict:
    """The seven-key card object as this socket may see it."""
    return {
        "id": row["id"],
        "column": row["kind"],
        "text": row["text"],
        "anonymous": bool(row["anonymous"]),
        "author": None if row["anonymous"] else row["author"],
        "mine": row["participant_id"] == ws.state.participant_id,
        "cluster_id": row["cluster_id"],
    }


def event(row: sqlite3.Row, ws: WebSocket) -> dict:
    return {"type": "card", "card": serialise(row, ws)}


def rows(conn: sqlite3.Connection, session_id: int) -> list[sqlite3.Row]:
    """Every card in the session, by id."""
    return conn.execute(
        SELECT + "WHERE cards.session_id = ? ORDER BY cards.id", (session_id,)
    ).fetchall()


def visible(conn: sqlite3.Connection, session: sqlite3.Row, ws: WebSocket) -> list[dict]:
    """The snapshot's cards: only the participant's own before reveal, everyone's from then on."""
    cards = [serialise(row, ws) for row in rows(conn, session["id"])]
    if session["phase"] == "write":
        return [card for card in cards if card["mine"]]  # an observer owns none
    return cards


def read(conn: sqlite3.Connection, card_id: int) -> sqlite3.Row:
    return conn.execute(SELECT + "WHERE cards.id = ?", (card_id,)).fetchone()


def _own(conn: sqlite3.Connection, session_id: int, ws: WebSocket, message: dict) -> sqlite3.Row:
    """The card `id` names, if this participant wrote it in this session."""
    card_id = ident(message.get("id"))
    row = conn.execute(
        SELECT + "WHERE cards.id = ? AND cards.session_id = ? AND cards.participant_id = ?",
        (card_id, session_id, ws.state.participant_id),
    ).fetchone()
    if row is None:
        raise Rejected("no such card of yours")
    return row


def _create(conn: sqlite3.Connection, session_id: int, ws: WebSocket, message: dict) -> dict:
    column = message.get("column")
    if column not in COLUMNS:
        raise Rejected("column must be start, stop or continue")
    text = stripped(message, "text", MAX_TEXT)
    anonymous = message.get("anonymous")
    if not isinstance(anonymous, bool):
        raise Rejected("anonymous must be true or false")
    with conn:
        card_id = conn.execute(
            "INSERT INTO cards (session_id, participant_id, kind, text, anonymous) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, ws.state.participant_id, column, text, anonymous),
        ).lastrowid
    return event(read(conn, card_id), ws)


def _edit(conn: sqlite3.Connection, session_id: int, ws: WebSocket, message: dict) -> dict:
    row = _own(conn, session_id, ws, message)
    # column and anonymous are fixed at creation: anything else in the message is ignored
    text = stripped(message, "text", MAX_TEXT)
    with conn:
        conn.execute("UPDATE cards SET text = ? WHERE id = ?", (text, row["id"]))
    return event(read(conn, row["id"]), ws)


def _delete(conn: sqlite3.Connection, session_id: int, ws: WebSocket, message: dict) -> dict:
    row = _own(conn, session_id, ws, message)
    with conn:
        conn.execute("DELETE FROM cards WHERE id = ?", (row["id"],))
    return {"type": "card.deleted", "id": row["id"]}


HANDLERS = {"card.create": _create, "card.edit": _edit, "card.delete": _delete}


def handle(
    conn: sqlite3.Connection, session_id: int, ws: WebSocket, message: dict,
    handlers: dict = HANDLERS, phases: tuple[str, ...] = ("write",),
) -> dict | Callable[[WebSocket], dict]:
    """Apply one message through `handlers`, open in `phases` to participants only:
    the event to send, or the error for the sender. Clusters and decisions reuse this gate."""
    try:
        if ws.state.participant_id is None:
            raise Rejected("observers only watch")
        phase = conn.execute(
            "SELECT phase FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()["phase"]
        if phase not in phases:
            raise Rejected(f"{message['type']} is closed in {phase}")
        return handlers[message["type"]](conn, session_id, ws, message)
    except Rejected as why:
        return {"type": "error", "detail": str(why)}
