import pytest
from starlette.websockets import WebSocketDisconnect

from app import hub
from app.sessions import PHASES

SNAPSHOT_KEYS = {"type", "phase", "cards", "clusters", "votes", "decisions"}


def create(client):
    return client.post("/sessions").json()


def advance(client, session):
    return client.post(
        f"/sessions/{session['code']}/advance", json={"token": session["facilitator_token"]}
    )


def assert_nothing_pending(ws):
    """Poke the server: its error reply lands after anything already queued."""
    ws.send_text("poke")
    assert ws.receive_json()["type"] == "error"


def test_snapshot_is_the_first_message_and_arrives_once(client):
    session = create(client)
    with client.websocket_connect(f"/ws/{session['code']}") as ws:
        assert ws.receive_json()["type"] == "snapshot"
        advance(client, session)
        assert ws.receive_json() == {"type": "phase", "phase": "reveal"}
        assert_nothing_pending(ws)


def test_new_session_snapshot_is_empty_with_exactly_the_six_keys(client):
    with client.websocket_connect(f"/ws/{create(client)['code']}") as ws:
        assert ws.receive_json() == {
            "type": "snapshot",
            "phase": "write",
            "cards": [],
            "clusters": [],
            "votes": {},
            "decisions": [],
        }


def test_snapshot_is_read_from_sqlite_at_connect_time(client, db):
    session = create(client)
    advance(client, session)  # reveal, before anyone connects
    sid = db.execute(
        "SELECT id FROM sessions WHERE code = ?", (session["code"],)
    ).fetchone()["id"]
    pid = db.execute(
        "INSERT INTO participants (session_id, name, token) VALUES (?, 'Sam', 't')", (sid,)
    ).lastrowid
    cid = db.execute(
        "INSERT INTO cards (session_id, participant_id, kind, text) "
        "VALUES (?, ?, 'start', 'pairing')",
        (sid, pid),
    ).lastrowid
    for _ in range(2):
        db.execute(
            "INSERT INTO votes (session_id, participant_id, card_id) VALUES (?, ?, ?)",
            (sid, pid, cid),
        )
    db.execute("INSERT INTO clusters (session_id, name) VALUES (?, 'process')", (sid,))
    db.execute(
        "INSERT INTO decisions (session_id, kind, text) VALUES (?, 'action', 'pair more')",
        (sid,),
    )
    db.commit()

    with client.websocket_connect(f"/ws/{session['code']}") as ws:
        snap = ws.receive_json()
    assert set(snap) == SNAPSHOT_KEYS
    assert snap["phase"] == "reveal"
    assert [card["text"] for card in snap["cards"]] == ["pairing"]
    assert snap["votes"] == {str(cid): 2}
    assert [cluster["name"] for cluster in snap["clusters"]] == ["process"]
    assert [decision["text"] for decision in snap["decisions"]] == ["pair more"]


def test_connecting_works_in_done_phase(client):
    session = create(client)
    for _ in PHASES[1:]:
        advance(client, session)
    with client.websocket_connect(f"/ws/{session['code']}") as ws:
        assert ws.receive_json()["phase"] == "done"


def test_unknown_code_is_closed_with_1008_and_never_joins_a_room(client):
    with client.websocket_connect("/ws/XXXXXX") as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()  # no snapshot: the first thing on the wire is the close
    assert closed.value.code == 1008
    assert "XXXXXX" not in hub.rooms


def test_code_is_case_insensitive_so_one_room_is_shared(client):
    session = create(client)
    code = session["code"]
    with (
        client.websocket_connect(f"/ws/{code.lower()}") as a,
        client.websocket_connect(f"/ws/{code}") as b,
    ):
        a.receive_json(), b.receive_json()
        assert list(hub.rooms) == [code] and len(hub.rooms[code]) == 2
        advance(client, session)
        assert a.receive_json() == b.receive_json() == {"type": "phase", "phase": "reveal"}


def test_advance_reaches_every_socket_in_its_room_and_no_other_room(client):
    ours, theirs = create(client), create(client)
    with (
        client.websocket_connect(f"/ws/{ours['code']}") as a,
        client.websocket_connect(f"/ws/{ours['code']}") as b,
        client.websocket_connect(f"/ws/{theirs['code']}") as c,
    ):
        a.receive_json(), b.receive_json(), c.receive_json()
        assert advance(client, ours).status_code == 200
        for ws in (a, b):
            assert ws.receive_json() == {"type": "phase", "phase": "reveal"}
            assert_nothing_pending(ws)  # exactly one event, no second snapshot
        assert_nothing_pending(c)


def test_rejected_advance_broadcasts_nothing(client):
    session = create(client)
    with client.websocket_connect(f"/ws/{session['code']}") as ws:
        ws.receive_json()
        wrong = client.post(f"/sessions/{session['code']}/advance", json={"token": "nope"})
        assert wrong.status_code == 403
        assert_nothing_pending(ws)

        for _ in PHASES[1:]:
            advance(client, session)
            ws.receive_json()
        assert advance(client, session).status_code == 409
        assert_nothing_pending(ws)


def test_advance_with_no_open_sockets_succeeds(client):
    session = create(client)
    assert session["code"] not in hub.rooms
    assert advance(client, session).status_code == 200

    with client.websocket_connect(f"/ws/{session['code']}"):
        pass  # room was created and then emptied
    assert advance(client, session).json() == {"phase": "cluster"}


def test_unhandled_client_message_gets_an_error_reply_to_the_sender_only(client):
    session = create(client)
    with (
        client.websocket_connect(f"/ws/{session['code']}") as sender,
        client.websocket_connect(f"/ws/{session['code']}") as other,
    ):
        sender.receive_json(), other.receive_json()
        for raw in ('plain text', '{"type": ', '[]', '{}', '{"type": 7}', '{"type": "nope"}',
                    '{"type": "snapshot"}', '{"type": "phase", "phase": "done"}'):
            sender.send_text(raw)
            assert sender.receive_json()["type"] == "error", raw
        assert_nothing_pending(other)
        advance(client, session)  # the sender's socket is still open and in the room
        assert sender.receive_json() == {"type": "phase", "phase": "reveal"}


def test_disconnected_socket_is_removed_and_the_rest_still_receive(client):
    session = create(client)
    code = session["code"]
    with client.websocket_connect(f"/ws/{code}") as a:
        a.receive_json()
        with client.websocket_connect(f"/ws/{code}") as b:
            b.receive_json()
            assert len(hub.rooms[code]) == 2
        assert len(hub.rooms[code]) == 1
        advance(client, session)
        assert a.receive_json() == {"type": "phase", "phase": "reveal"}


def test_a_send_that_raises_mid_fanout_drops_only_that_socket(client):
    session = create(client)
    code = session["code"]
    with (
        client.websocket_connect(f"/ws/{code}") as a,
        client.websocket_connect(f"/ws/{code}") as c,
    ):
        a.receive_json(), c.receive_json()
        live = set(hub.rooms[code])
        with client.websocket_connect(f"/ws/{code}") as b:
            b.receive_json()
            (dead,) = hub.rooms[code] - live  # b's server-side socket
        hub.rooms[code].add(dead)  # b vanished, but the hub has not noticed yet

        assert advance(client, session).status_code == 200
        assert a.receive_json() == c.receive_json() == {"type": "phase", "phase": "reveal"}
        assert hub.rooms[code] == live


def test_room_key_is_deleted_when_the_last_socket_leaves(client):
    code = create(client)["code"]
    with client.websocket_connect(f"/ws/{code}") as a:
        with client.websocket_connect(f"/ws/{code}"):
            assert code in hub.rooms
        assert code in hub.rooms
    assert code not in hub.rooms
