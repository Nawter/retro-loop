"""Room hub: in-memory WebSocket rooms, one snapshot on connect, then deltas."""

import json
import sqlite3
from collections.abc import Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app import cards, db

# Keyed by upper-cased code. Nothing here is persisted: a restart forgets every
# room and clients recover through a fresh snapshot.
rooms: dict[str, set[WebSocket]] = {}

router = APIRouter()


def snapshot(conn: sqlite3.Connection, session: sqlite3.Row, ws: WebSocket) -> dict:
    """The whole world for one session, read from SQLite right now, as this socket may see it."""

    def rows(table: str) -> list[dict]:
        return [
            dict(row)
            for row in conn.execute(
                f"SELECT * FROM {table} WHERE session_id = ?", (session["id"],)
            )
        ]

    votes = conn.execute(
        "SELECT card_id, count(*) AS n FROM votes WHERE session_id = ? GROUP BY card_id",
        (session["id"],),
    )
    return {
        "type": "snapshot",
        "phase": session["phase"],
        "cards": cards.visible(conn, session, ws),
        "clusters": rows("clusters"),
        "votes": {row["card_id"]: row["n"] for row in votes},  # JSON turns the keys into strings
        "decisions": rows("decisions"),
    }


def _leave(code: str, ws: WebSocket) -> None:
    room = rooms.get(code)
    if room:
        room.discard(ws)
        if not room:
            del rooms[code]


async def fanout(code: str, message: Callable[[WebSocket], dict | None]) -> None:
    """Send `message(ws)` to every socket in the room, skipping those it returns None for.
    A dead socket is dropped, never fatal."""
    code = code.upper()
    for ws in list(rooms.get(code, ())):
        event = message(ws)
        if event is None:
            continue
        try:
            await ws.send_json(event)
        except Exception:  # what a vanished client raises depends on the server
            _leave(code, ws)


async def broadcast(code: str, message: dict) -> None:
    """Send one event to every socket in the room."""
    await fanout(code, lambda _: message)


def _parse(raw: str | None) -> dict:
    """The client's message if this server handles its type, else the error reply it earns."""
    if raw is None:  # a binary frame
        return {"type": "error", "detail": "not a text frame"}
    try:
        message = json.loads(raw)
    except ValueError:
        return {"type": "error", "detail": "not JSON"}
    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
        return {"type": "error", "detail": "expected a JSON object with a string type"}
    if message["type"] not in cards.HANDLERS:
        return {"type": "error", "detail": f"unknown type: {message['type']}"}
    return message


async def _card(ws: WebSocket, code: str, session_id: int, message: dict) -> None:
    """One card.* message: the error goes to the sender, the event to the author's sockets."""
    conn = db.connect()
    try:
        reply = cards.handle(conn, session_id, ws, message)
    finally:
        conn.close()
    if reply["type"] == "error":
        await ws.send_json(reply)
        return
    author = ws.state.participant_id  # every tab of the author, nobody else, until reveal
    await fanout(code, lambda peer: reply if peer.state.participant_id == author else None)


@router.websocket("/ws/{code}")
async def room(ws: WebSocket, code: str) -> None:
    code = code.upper()
    # Accept first: a close before accept reaches a browser as HTTP 403, not as 1008.
    await ws.accept()
    conn = db.connect()
    try:
        session = conn.execute("SELECT * FROM sessions WHERE code = ?", (code,)).fetchone()
        if session is None:
            await ws.close(code=1008)
            return
        # Identity travels with the socket: a participant id, or None for an observer.
        token = ws.query_params.get("token", "")
        participant = conn.execute(
            "SELECT id FROM participants WHERE session_id = ? AND token = ?",
            (session["id"], token),
        ).fetchone()
        if token and participant is None:  # a token that is not this session's is a bad token
            await ws.close(code=1008)
            return
        ws.state.participant_id = participant["id"] if participant else None
        snap = snapshot(conn, session, ws)
    finally:
        conn.close()
    # No await between the read and the join, so no event can slip past the snapshot.
    rooms.setdefault(code, set()).add(ws)
    try:
        await ws.send_json(snap)
        while True:
            msg = await ws.receive()  # not receive_text(): that KeyErrors on a binary frame
            if msg["type"] == "websocket.disconnect":
                break
            message = _parse(msg.get("text"))
            if message["type"] in cards.HANDLERS:
                await _card(ws, code, session["id"], message)
            else:
                await ws.send_json(message)  # the error reply _parse built
    except WebSocketDisconnect:
        pass
    finally:
        _leave(code, ws)
