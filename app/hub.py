"""Room hub: in-memory WebSocket rooms, one snapshot on connect, then deltas."""

import json
import sqlite3
from collections.abc import Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app import cards, clusters, db, decisions, votes

# Keyed by upper-cased code. Nothing here is persisted: a restart forgets every
# room and clients recover through a fresh snapshot.
rooms: dict[str, set[WebSocket]] = {}

# Message type -> the module that handles it; each module gates its own phases and observers.
MODULES = {kind: module for module in (cards, clusters, decisions, votes) for kind in module.HANDLERS}

router = APIRouter()


def snapshot(conn: sqlite3.Connection, session: sqlite3.Row, ws: WebSocket) -> dict:
    """The whole world for one session, read from SQLite right now, as this socket may see it."""
    return {
        "type": "snapshot",
        "phase": session["phase"],
        "cards": cards.visible(conn, session, ws),
        "clusters": clusters.rows(conn, session["id"]),
        "votes": votes.visible(conn, session["id"], ws),
        "decisions": decisions.rows(conn, session["id"]),
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
    if message["type"] not in MODULES:
        return {"type": "error", "detail": f"unknown type: {message['type']}"}
    return message


async def _apply(ws: WebSocket, code: str, session_id: int, message: dict) -> None:
    """One handled message: the error goes to the sender, the event to whoever may see it."""
    module = MODULES[message["type"]]
    conn = db.connect()
    try:
        reply = module.handle(conn, session_id, ws, message)
    finally:
        conn.close()
    # ponytail: no per-room lock. Two fan-outs can interleave only if a send blocks on
    # backpressure; a per-room asyncio.Lock around handle+fanout if that ever reorders events.
    if callable(reply):  # a card move: `mine` differs per recipient, as in the reveal fan-out
        await fanout(code, reply)
    elif reply["type"] == "error":
        await ws.send_json(reply)
    elif module is cards:  # every tab of the author, nobody else, until reveal
        author = ws.state.participant_id
        await fanout(code, lambda peer: reply if peer.state.participant_id == author else None)
    else:  # clusters and decisions: the same bytes for the whole room, observers included
        await broadcast(code, reply)


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
            if message["type"] in MODULES:
                await _apply(ws, code, session["id"], message)
            else:
                await ws.send_json(message)  # the error reply _parse built
    except WebSocketDisconnect:
        pass
    finally:
        _leave(code, ws)
