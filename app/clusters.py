"""Clusters: create, rename, and move cards into and out of them."""

import sqlite3
from collections.abc import Callable
from functools import partial

from fastapi import WebSocket

from app import cards
from app.cards import Rejected, ref, stripped

MAX_NAME = 100
# Clustering and decisions are open from `cluster` until the facilitator ends the retro.
PHASES = ("cluster", "vote", "discuss")


def rows(conn: sqlite3.Connection, session_id: int) -> list[dict]:
    """Every cluster in the session, by id, as the two-key object."""
    return [
        dict(row)
        for row in conn.execute(
            "SELECT id, name FROM clusters WHERE session_id = ? ORDER BY id", (session_id,)
        )
    ]


def _event(conn: sqlite3.Connection, cluster_id: int) -> dict:
    row = conn.execute("SELECT id, name FROM clusters WHERE id = ?", (cluster_id,)).fetchone()
    return {"type": "cluster", "cluster": dict(row)}


def _create(conn: sqlite3.Connection, session_id: int, ws: WebSocket, message: dict) -> dict:
    name = stripped(message, "name", MAX_NAME)
    with conn:
        cluster_id = conn.execute(
            "INSERT INTO clusters (session_id, name) VALUES (?, ?)", (session_id, name)
        ).lastrowid
    return _event(conn, cluster_id)


def _rename(conn: sqlite3.Connection, session_id: int, ws: WebSocket, message: dict) -> dict:
    cluster_id = ref(conn, "clusters", session_id, message.get("id"))
    name = stripped(message, "name", MAX_NAME)
    with conn:
        conn.execute("UPDATE clusters SET name = ? WHERE id = ?", (name, cluster_id))
    return _event(conn, cluster_id)


def _move(
    conn: sqlite3.Connection, session_id: int, ws: WebSocket, message: dict
) -> Callable[[WebSocket], dict]:
    """Set the card's cluster, or clear it for `null`. Anyone may move anyone's card; the
    echo is #4's card event, so `mine` is per recipient and relative to the author."""
    card_id = ref(conn, "cards", session_id, message.get("id"))
    if "cluster_id" not in message:
        raise Rejected("cluster_id is required: null takes a card out of its cluster")
    cluster_id = message["cluster_id"]
    if cluster_id is not None:
        ref(conn, "clusters", session_id, cluster_id, "cluster_id")
    with conn:
        conn.execute("UPDATE cards SET cluster_id = ? WHERE id = ?", (cluster_id, card_id))
    return partial(cards.event, cards.read(conn, card_id))


HANDLERS = {"cluster.create": _create, "cluster.rename": _rename, "card.move": _move}


def handle(conn: sqlite3.Connection, session_id: int, ws: WebSocket, message: dict):
    return cards.handle(conn, session_id, ws, message, HANDLERS, PHASES)
