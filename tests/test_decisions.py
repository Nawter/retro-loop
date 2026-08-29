from app.sessions import PHASES
from test_cards import advance, assert_nothing_pending, connect, create, join, room
from test_clusters import OPEN, advance_to, drain, everyone

DECISION_KEYS = {"id", "kind", "text", "owner"}


def create_decision(ws, kind="action", text="pair more", owner="Ann"):
    """Send a decision.create and return the reply: the echoed event or the error."""
    ws.send_json({"type": "decision.create", "kind": kind, "text": text, "owner": owner})
    return ws.receive_json()


def stored(db):
    return [
        tuple(row)
        for row in db.execute("SELECT id, kind, text, owner FROM decisions ORDER BY id")
    ]


def test_create_echoes_the_committed_decision_to_the_whole_room(client, db):
    with room(client) as r:
        advance_to(client, r, "cluster")
        r.sam1.send_json(
            {"type": "decision.create", "kind": "action", "text": " pair more \n", "owner": " Ann "}
        )
        decision = {"id": 1, "kind": "action", "text": "pair more", "owner": "Ann"}
        for ws in everyone(r):
            assert ws.receive_json() == {"type": "decision", "decision": decision}
        assert_nothing_pending(r.stranger)
        assert stored(db) == [(1, "action", "pair more", "Ann")]


def test_edit_by_another_participant_replaces_kind_text_and_owner(client, db):
    with room(client) as r:
        advance_to(client, r, "cluster")
        decision = create_decision(r.sam1)["decision"]
        for ws in everyone(r):
            drain(ws)
        r.ann1.send_json({
            "type": "decision.edit", "id": decision["id"],
            "kind": "decision", "text": " no more standups ", "owner": None,
        })
        edited = {"id": decision["id"], "kind": "decision", "text": "no more standups", "owner": None}
        for ws in everyone(r):
            assert ws.receive_json() == {"type": "decision", "decision": edited}
        assert_nothing_pending(r.stranger)
        assert stored(db) == [(decision["id"], "decision", "no more standups", None)]


def test_delete_by_another_participant_removes_the_row_and_echoes_decision_deleted(client, db):
    with room(client) as r:
        advance_to(client, r, "cluster")
        decision = create_decision(r.sam1)["decision"]
        for ws in everyone(r):
            drain(ws)
        r.ann1.send_json({"type": "decision.delete", "id": decision["id"]})
        for ws in everyone(r):
            assert ws.receive_json() == {"type": "decision.deleted", "id": decision["id"]}
        assert_nothing_pending(r.stranger)
        assert stored(db) == []


def test_kind_must_be_exactly_decision_or_action_on_create_and_edit(client, db):
    with room(client) as r:
        advance_to(client, r, "cluster")
        decision = create_decision(r.sam1)["decision"]
        for ws in everyone(r):
            drain(ws)
        before = stored(db)
        for kind in ("Decision", "ACTION", None, "", 1, ["action"]):
            assert create_decision(r.sam1, kind=kind)["type"] == "error", kind
            r.sam1.send_json({"type": "decision.edit", "id": decision["id"], "kind": kind, "text": "x"})
            assert r.sam1.receive_json()["type"] == "error", kind
        r.sam1.send_json({"type": "decision.create", "text": "x"})  # missing
        assert r.sam1.receive_json()["type"] == "error"
        r.sam1.send_json({"type": "decision.edit", "id": decision["id"], "text": "x"})  # missing
        assert r.sam1.receive_json()["type"] == "error"
        for ws in (r.sam2, r.ann1, r.observer, r.stranger):
            assert_nothing_pending(ws)
        assert stored(db) == before
        for kind in ("decision", "action"):
            assert create_decision(r.sam1, kind=kind)["decision"]["kind"] == kind


def test_text_is_stripped_and_limited_to_500_characters_on_create_and_edit(client, db):
    session = create(client)
    with connect(client, session["code"], join(client, session["code"], "Sam")) as ws:
        ws.receive_json()
        for _ in range(2):
            advance(client, session)
            drain(ws)
        decision = create_decision(ws, text="  " + "x" * 500 + "\n")["decision"]
        assert decision["text"] == "x" * 500
        for text in ("x" * 501, " " + "x" * 501 + " ", "", "   ", None, 7, ["x"]):
            assert create_decision(ws, text=text)["type"] == "error", text
            ws.send_json({"type": "decision.edit", "id": decision["id"], "kind": "action", "text": text})
            assert ws.receive_json()["type"] == "error", text
        ws.send_json({"type": "decision.create", "kind": "action"})  # missing
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "decision.edit", "id": decision["id"], "kind": "action"})  # missing
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "decision.edit", "id": decision["id"], "kind": "action", "text": " " + "y" * 500})
        assert ws.receive_json()["decision"]["text"] == "y" * 500
    assert stored(db) == [(decision["id"], "action", "y" * 500, None)]


def test_owner_is_optional_blank_means_none_and_is_limited_to_100_characters(client, db):
    session = create(client)
    with connect(client, session["code"], join(client, session["code"], "Sam")) as ws:
        ws.receive_json()
        for _ in range(2):
            advance(client, session)
            drain(ws)
        ws.send_json({"type": "decision.create", "kind": "action", "text": "x"})  # absent
        assert ws.receive_json()["decision"]["owner"] is None
        for owner in (None, "", "   ", "\n"):
            assert create_decision(ws, owner=owner)["decision"]["owner"] is None, repr(owner)
        assert create_decision(ws, owner="  Sam ")["decision"]["owner"] == "Sam"
        assert create_decision(ws, owner=" " + "x" * 100 + " ")["decision"]["owner"] == "x" * 100
        before = stored(db)
        assert [row[3] for row in before] == [None] * 5 + ["Sam", "x" * 100]
        for owner in ("x" * 101, " " + "x" * 101, 7, 0, True, ["Sam"], {"name": "Sam"}):
            assert create_decision(ws, owner=owner)["type"] == "error", owner
            ws.send_json({"type": "decision.edit", "id": 1, "kind": "action", "text": "x", "owner": owner})
            assert ws.receive_json()["type"] == "error", owner
        ws.send_json({"type": "decision.edit", "id": 1, "kind": "action", "text": "x"})  # absent
        assert ws.receive_json()["decision"]["owner"] is None
        ws.send_json({"type": "decision.edit", "id": 1, "kind": "action", "text": "x", "owner": " Ann "})
        assert ws.receive_json()["decision"]["owner"] == "Ann"
        ws.send_json({"type": "decision.edit", "id": 1, "kind": "action", "text": "x", "owner": " "})
        assert ws.receive_json()["decision"]["owner"] is None
    assert stored(db) == before


def test_edit_and_delete_reject_ids_that_are_not_decisions_of_this_session(client, db):
    other = create(client)
    with connect(client, other["code"], join(client, other["code"], "Zed")) as zed:
        zed.receive_json()
        for _ in range(2):
            advance(client, other)
            drain(zed)
        theirs = create_decision(zed)["decision"]
    with room(client) as r:
        advance_to(client, r, "cluster")
        mine = create_decision(r.sam1)["decision"]
        for ws in everyone(r):
            drain(ws)
        before = stored(db)
        assert mine["id"] == 2  # theirs is 1, so `true` must be refused, not bound as 1
        for bad in ("1", 1.0, True, None, 0, -1, 2**63, 2**70, -(2**70), [2], 999, theirs["id"]):
            for op in (
                {"type": "decision.edit", "id": bad, "kind": "action", "text": "x"},
                {"type": "decision.delete", "id": bad},
            ):
                r.sam1.send_json(op)
                assert r.sam1.receive_json()["type"] == "error", op
        for ws in (r.sam2, r.ann1, r.observer, r.stranger):
            assert_nothing_pending(ws)
        assert stored(db) == before


def test_observers_cannot_create_edit_or_delete_decisions(client, db):
    with room(client) as r:
        advance_to(client, r, "cluster")
        decision = create_decision(r.sam1)["decision"]
        for ws in everyone(r):
            drain(ws)
        before = stored(db)
        for op in (
            {"type": "decision.create", "kind": "action", "text": "x"},
            {"type": "decision.edit", "id": decision["id"], "kind": "action", "text": "x"},
            {"type": "decision.delete", "id": decision["id"]},
        ):
            r.observer.send_json(op)
            assert r.observer.receive_json()["type"] == "error", op
        for ws in (r.sam1, r.sam2, r.ann1, r.stranger):
            assert_nothing_pending(ws)
        assert stored(db) == before


def test_decisions_are_open_from_cluster_to_discuss_and_closed_in_write_reveal_and_done(client, db):
    with room(client) as r:
        for phase in PHASES:
            if phase != "write":
                advance_to(client, r, phase)
            before = stored(db)
            ops = (
                {"type": "decision.create", "kind": "action", "text": phase},
                {"type": "decision.edit", "id": 1, "kind": "decision", "text": phase},
                {"type": "decision.delete", "id": 1},
            )
            for op in ops:
                r.ann1.send_json(op)
                reply = r.ann1.receive_json()
                if phase in OPEN:
                    assert reply["type"] in ("decision", "decision.deleted"), (phase, op)
                    for ws in (r.sam1, r.sam2, r.observer):
                        assert ws.receive_json() == reply
                else:
                    assert reply["type"] == "error", (phase, op)
            for ws in (r.sam1, r.sam2, r.observer, r.stranger):
                assert_nothing_pending(ws)
            assert stored(db) == before  # created, edited and deleted again; or never touched


def test_decision_and_decision_deleted_are_server_only_types(client, db):
    session = create(client)
    with connect(client, session["code"], join(client, session["code"], "Sam")) as ws:
        ws.receive_json()
        for message in (
            {"type": "decision", "decision": {"kind": "action", "text": "x"}},
            {"type": "decision.deleted", "id": 1},
        ):
            ws.send_json(message)
            assert ws.receive_json()["detail"] == f"unknown type: {message['type']}"
    assert stored(db) == []


def test_snapshot_lists_four_key_decisions_by_id(client):
    with room(client) as r:
        advance_to(client, r, "cluster")
        create_decision(r.sam1, "action", "pair more", "Ann")
        create_decision(r.ann1, "decision", "no standups", None)
        for ws in everyone(r):
            drain(ws)
        for token in (r.sam, r.ann, None):
            with connect(client, r.code, token) as ws:
                snap = ws.receive_json()
            assert snap["decisions"] == [
                {"id": 1, "kind": "action", "text": "pair more", "owner": "Ann"},
                {"id": 2, "kind": "decision", "text": "no standups", "owner": None},
            ]
            assert all(set(d) == DECISION_KEYS for d in snap["decisions"])
        with connect(client, create(client)["code"]) as ws:
            assert ws.receive_json()["decisions"] == []
