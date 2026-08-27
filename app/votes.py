"""Votes: three per participant, the budget enforced inside the insert's own transaction."""

import sqlite3
from collections.abc import Callable

from fastapi import WebSocket

from app import cards
from app.cards import Rejected, ref

BUDGET = 3
PHASES = ("vote",)


def _tally(conn: sqlite3.Connection, session_id: int) -> list[sqlite3.Row]:
    """How many votes each participant has on each card, for every pair with at least one."""
    return conn.execute(
        "SELECT card_id, participant_id, count(*) AS n FROM votes WHERE session_id = ? "
        "GROUP BY card_id, participant_id",
        (session_id,),
    ).fetchall()


def visible(conn: sqlite3.Connection, session_id: int, ws: WebSocket) -> dict:
    """The snapshot's votes: every card's total, and only this socket's own share and budget left."""
    me = ws.state.participant_id
    counts: dict[str, int] = {}
    mine: dict[str, int] = {}
    for row in _tally(conn, session_id):
        card = str(row["card_id"])  # JSON object keys are strings: say so here, not in the client
        counts[card] = counts.get(card, 0) + row["n"]
        if row["participant_id"] == me:
            mine[card] = row["n"]
    return {"counts": counts, "mine": mine, "left": 0 if me is None else BUDGET - sum(mine.values())}


def _event(conn: sqlite3.Connection, session_id: int, card_id: int) -> Callable[[WebSocket], dict]:
    """The vote event after a commit: the card's total for everyone, `mine` and `left` per recipient.
    Read once here, since the hub closes `conn` before the fan-out; the callable only looks up."""
    count, mine, spent = 0, {}, {}
    for row in _tally(conn, session_id):
        who = row["participant_id"]
        spent[who] = spent.get(who, 0) + row["n"]
        if row["card_id"] == card_id:
            count += row["n"]
            mine[who] = row["n"]

    def message(ws: WebSocket) -> dict:
        me = ws.state.participant_id  # None for an observer: nothing is theirs, nothing is left
        return {
            "type": "vote", "card_id": card_id, "count": count,
            "mine": mine.get(me, 0),
            "left": 0 if me is None else BUDGET - spent.get(me, 0),
        }

    return message


def _cast(
    conn: sqlite3.Connection, session_id: int, ws: WebSocket, message: dict
) -> Callable[[WebSocket], dict]:
    card_id = ref(conn, "cards", session_id, message.get("id"))
    me = ws.state.participant_id
    with conn:  # commits, or rolls back the Rejected raised inside
        # Python's sqlite3 only opens its implicit transaction at the INSERT, which would leave the
        # count outside it: take the write lock first, so two racing casts cannot both be the third
        conn.execute("BEGIN IMMEDIATE")
        spent = conn.execute(
            "SELECT count(*) FROM votes WHERE session_id = ? AND participant_id = ?",
            (session_id, me),
        ).fetchone()[0]
        if spent >= BUDGET:
            raise Rejected(f"no votes left: {BUDGET} per person")
        conn.execute(
            "INSERT INTO votes (session_id, participant_id, card_id) VALUES (?, ?, ?)",
            (session_id, me, card_id),
        )
    return _event(conn, session_id, card_id)


def _remove(
    conn: sqlite3.Connection, session_id: int, ws: WebSocket, message: dict
) -> Callable[[WebSocket], dict]:
    """Take back the newest of this participant's own votes on the card."""
    card_id = ref(conn, "cards", session_id, message.get("id"))
    with conn:
        gone = conn.execute(
            "DELETE FROM votes WHERE id = (SELECT max(id) FROM votes "
            "WHERE session_id = ? AND participant_id = ? AND card_id = ?)",
            (session_id, ws.state.participant_id, card_id),
        ).rowcount
    if not gone:
        raise Rejected("no vote of yours on this card")
    return _event(conn, session_id, card_id)


HANDLERS = {"vote.cast": _cast, "vote.remove": _remove}


def handle(conn: sqlite3.Connection, session_id: int, ws: WebSocket, message: dict):
    return cards.handle(conn, session_id, ws, message, HANDLERS, PHASES)
