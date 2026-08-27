"""Room hub: in-memory WebSocket rooms, one snapshot on connect, then deltas."""

import json
import sqlite3

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app import db

# Keyed by upper-cased code. Nothing here is persisted: a restart forgets every
# room and clients recover through a fresh snapshot.
rooms: dict[str, set[WebSocket]] = {}

router = APIRouter()


def snapshot(conn: sqlite3.Connection, session: sqlite3.Row) -> dict:
    """The whole world for one session, read from SQLite right now."""

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
        "cards": rows("cards"),
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


async def broadcast(code: str, message: dict) -> None:
    """Send one event to every socket in the room. A dead socket is dropped, never fatal."""
    code = code.upper()
    for ws in list(rooms.get(code, ())):
        try:
            await ws.send_json(message)
        except Exception:  # what a vanished client raises depends on the server
            _leave(code, ws)


def _error(raw: str) -> dict:
    """The reply to a client message this server does not handle."""
    try:
        message = json.loads(raw)
    except ValueError:
        return {"type": "error", "detail": "not JSON"}
    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
        return {"type": "error", "detail": "expected a JSON object with a string type"}
    return {"type": "error", "detail": f"unknown type: {message['type']}"}


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
        snap = snapshot(conn, session)
    finally:
        conn.close()
    # No await between the read and the join, so no event can slip past the snapshot.
    rooms.setdefault(code, set()).add(ws)
    try:
        await ws.send_json(snap)
        while True:
            await ws.send_json(_error(await ws.receive_text()))
    except WebSocketDisconnect:
        pass
    finally:
        _leave(code, ws)
