"""Session lifecycle: create, join, advance phase."""

import secrets
import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import db, hub

# Codes get read aloud and typed: no 0/O, no 1/I/l.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6

# The one place the order is defined; every check reads it.
PHASES = ("write", "reveal", "cluster", "vote", "discuss", "done")

router = APIRouter()


class JoinBody(BaseModel):
    name: str


class AdvanceBody(BaseModel):
    token: str


def _tokens_match(sent: str, stored: str) -> bool:
    return secrets.compare_digest(sent.encode(), stored.encode())


def _get_session(conn: sqlite3.Connection, code: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM sessions WHERE code = ?", (code.upper(),)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "unknown session code")
    return row


@router.post("/sessions", status_code=201)
def create_session() -> dict[str, str]:
    token = secrets.token_urlsafe(32)
    conn = db.connect()
    try:
        while True:
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
            try:
                with conn:
                    conn.execute(
                        "INSERT INTO sessions (code, facilitator_token) VALUES (?, ?)",
                        (code, token),
                    )
                return {"code": code, "facilitator_token": token}
            except sqlite3.IntegrityError:  # code collision: roll again
                continue
    finally:
        conn.close()


@router.post("/sessions/{code}/join", status_code=201)
def join_session(code: str, body: JoinBody) -> dict[str, str]:
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "display name is required")
    conn = db.connect()
    try:
        session = _get_session(conn, code)
        if session["phase"] == PHASES[-1]:
            raise HTTPException(409, "this retro is over")
        token = secrets.token_urlsafe(32)
        with conn:
            conn.execute(
                "INSERT INTO participants (session_id, name, token) VALUES (?, ?, ?)",
                (session["id"], name, token),
            )
        return {"participant_token": token}
    finally:
        conn.close()


@router.post("/sessions/{code}/advance")
async def advance_phase(code: str, body: AdvanceBody) -> dict[str, str]:
    """Async so it can await the broadcast; the SQLite work here is microseconds."""
    conn = db.connect()
    try:
        session = _get_session(conn, code)
        if not _tokens_match(body.token, session["facilitator_token"]):
            raise HTTPException(403, "only the facilitator advances the phase")
        if session["phase"] == PHASES[-1]:
            raise HTTPException(409, "the retro is already done")
        phase = PHASES[PHASES.index(session["phase"]) + 1]
        with conn:
            conn.execute(
                "UPDATE sessions SET phase = ? WHERE id = ?", (phase, session["id"])
            )
    finally:
        conn.close()
    await hub.broadcast(code, {"type": "phase", "phase": phase})  # after the commit
    return {"phase": phase}
