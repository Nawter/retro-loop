"""Cards: validate, persist, serialise, and filter per recipient."""

import sqlite3

from fastapi import WebSocket

COLUMNS = ("start", "stop", "continue")
MAX_TEXT = 500

# Every read joins the author, so a card always knows its name.
SELECT = (
    "SELECT cards.*, participants.name AS author FROM cards "
    "JOIN participants ON participants.id = cards.participant_id "
)


class Rejected(Exception):
    """Why a card.* message was refused; the detail goes back to the sender."""


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


def _text(message: dict) -> str:
    text = message.get("text")
    if not isinstance(text, str):
        raise Rejected("text must be a string")
    text = text.strip()
    if not text:
        raise Rejected("text is empty")
    if len(text) > MAX_TEXT:
        raise Rejected(f"text is over {MAX_TEXT} characters")
    return text


def _row(conn: sqlite3.Connection, card_id: int) -> sqlite3.Row:
    return conn.execute(SELECT + "WHERE cards.id = ?", (card_id,)).fetchone()


def _own(conn: sqlite3.Connection, session_id: int, ws: WebSocket, message: dict) -> sqlite3.Row:
    """The card `id` names, if this participant wrote it in this session."""
    card_id = message.get("id")
    if isinstance(card_id, bool) or not isinstance(card_id, int):  # bool is an int in Python
        raise Rejected("id must be an integer")
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
    text = _text(message)
    anonymous = message.get("anonymous")
    if not isinstance(anonymous, bool):
        raise Rejected("anonymous must be true or false")
    with conn:
        card_id = conn.execute(
            "INSERT INTO cards (session_id, participant_id, kind, text, anonymous) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, ws.state.participant_id, column, text, anonymous),
        ).lastrowid
    return event(_row(conn, card_id), ws)


def _edit(conn: sqlite3.Connection, session_id: int, ws: WebSocket, message: dict) -> dict:
    row = _own(conn, session_id, ws, message)
    text = _text(message)  # column and anonymous are fixed at creation: anything else is ignored
    with conn:
        conn.execute("UPDATE cards SET text = ? WHERE id = ?", (text, row["id"]))
    return event(_row(conn, row["id"]), ws)


def _delete(conn: sqlite3.Connection, session_id: int, ws: WebSocket, message: dict) -> dict:
    row = _own(conn, session_id, ws, message)
    with conn:
        conn.execute("DELETE FROM cards WHERE id = ?", (row["id"],))
    return {"type": "card.deleted", "id": row["id"]}


HANDLERS = {"card.create": _create, "card.edit": _edit, "card.delete": _delete}


def handle(conn: sqlite3.Connection, session_id: int, ws: WebSocket, message: dict) -> dict:
    """Apply one card.* message: the event for the author's sockets, or the error for the sender."""
    try:
        if ws.state.participant_id is None:
            raise Rejected("observers cannot write cards")
        phase = conn.execute(
            "SELECT phase FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()["phase"]
        if phase != "write":
            raise Rejected(f"cards are frozen in {phase}")
        return HANDLERS[message["type"]](conn, session_id, ws, message)
    except Rejected as why:
        return {"type": "error", "detail": str(why)}
