from contextlib import contextmanager
from types import SimpleNamespace

from app.sessions import PHASES

CARD_KEYS = {"id", "column", "text", "anonymous", "author", "mine", "cluster_id"}


def create(client):
    return client.post("/sessions").json()


def join(client, code, name):
    return client.post(f"/sessions/{code}/join", json={"name": name}).json()["participant_token"]


def advance(client, session):
    return client.post(
        f"/sessions/{session['code']}/advance", json={"token": session["facilitator_token"]}
    )


def connect(client, code, token=None):
    return client.websocket_connect(f"/ws/{code}" + (f"?token={token}" if token else ""))


def assert_nothing_pending(ws):
    """Poke the server: its error reply lands after anything already queued."""
    ws.send_text("poke")
    assert ws.receive_json()["type"] == "error"


def create_card(ws, column="start", text="pairing", anonymous=False):
    """Send a card.create and return the reply: the echoed event or the error."""
    ws.send_json({"type": "card.create", "column": column, "text": text, "anonymous": anonymous})
    return ws.receive_json()


def stored(db):
    return [
        tuple(row) for row in db.execute("SELECT id, kind, text, anonymous FROM cards ORDER BY id")
    ]


@contextmanager
def room(client):
    """Sam on two sockets, Ann on one, an observer, and a stranger in another room, snapshots read."""
    session = create(client)
    code = session["code"]
    sam, ann = join(client, code, "Sam"), join(client, code, "Ann")
    with (
        connect(client, code, sam) as sam1,
        connect(client, code, sam) as sam2,
        connect(client, code, ann) as ann1,
        connect(client, code) as observer,
        connect(client, create(client)["code"]) as stranger,
    ):
        for ws in (sam1, sam2, ann1, observer, stranger):
            ws.receive_json()
        yield SimpleNamespace(
            session=session, code=code, sam=sam, ann=ann,
            sam1=sam1, sam2=sam2, ann1=ann1, observer=observer, stranger=stranger,
        )


def test_create_echoes_the_committed_card_to_the_authors_sockets_only(client, db):
    with room(client) as r:
        r.sam1.send_json(
            {"type": "card.create", "column": "start", "text": "  pairing \n", "anonymous": False}
        )
        card = {
            "id": 1, "column": "start", "text": "pairing", "anonymous": False,
            "author": "Sam", "mine": True, "cluster_id": None,
        }
        assert r.sam1.receive_json() == r.sam2.receive_json() == {"type": "card", "card": card}
        for ws in (r.ann1, r.observer, r.stranger):
            assert_nothing_pending(ws)
        assert stored(db) == [(1, "start", "pairing", 0)]


def test_write_phase_snapshot_holds_only_your_own_cards(client):
    session = create(client)
    code = session["code"]
    sam, ann = join(client, code, "Sam"), join(client, code, "Ann")
    with connect(client, code, sam) as ws:
        ws.receive_json()
        create_card(ws, "start", "sam's secret")
    with connect(client, code, ann) as ws:
        ws.receive_json()
        create_card(ws, "stop", "ann's secret")

    with connect(client, code, sam) as ws:
        cards = ws.receive_json()["cards"]
    assert [(c["text"], c["mine"]) for c in cards] == [("sam's secret", True)]
    with connect(client, code, ann) as ws:
        cards = ws.receive_json()["cards"]
    assert [(c["text"], c["mine"]) for c in cards] == [("ann's secret", True)]
    with connect(client, code) as ws:
        assert ws.receive_json()["cards"] == []


def test_anonymous_card_names_no_author_for_anyone_in_any_phase(client):
    with room(client) as r:
        echo = create_card(r.sam1, "stop", "meetings", anonymous=True)["card"]
        assert (echo["anonymous"], echo["author"], echo["mine"]) == (True, None, True)
        assert r.sam2.receive_json()["card"] == echo
        with connect(client, r.code, r.sam) as ws:
            assert ws.receive_json()["cards"][0]["author"] is None  # not even for its author

        advance(client, r.session)  # reveal
        for ws, mine in ((r.sam1, True), (r.sam2, True), (r.ann1, False), (r.observer, False)):
            assert ws.receive_json() == {"type": "phase", "phase": "reveal"}
            card = ws.receive_json()["card"]
            assert (card["text"], card["author"], card["mine"]) == ("meetings", None, mine)

        for _ in PHASES[1:]:  # reveal through done: every snapshot shows the card, never the name
            for token, mine in ((r.sam, True), (r.ann, False), (None, False)):
                with connect(client, r.code, token) as ws:
                    (card,) = ws.receive_json()["cards"]
                    assert (card["text"], card["author"], card["mine"]) == ("meetings", None, mine)
            advance(client, r.session)


def test_reveal_fans_out_every_card_to_every_socket_after_the_phase_event(client):
    with room(client) as r:
        create_card(r.sam1, "start", "one")
        r.sam2.receive_json()
        create_card(r.ann1, "stop", "two", anonymous=True)
        create_card(r.sam2, "continue", "three")
        r.sam1.receive_json()

        assert advance(client, r.session).json() == {"phase": "reveal"}
        expected = [
            {"id": 1, "column": "start", "text": "one", "anonymous": False, "author": "Sam", "cluster_id": None},
            {"id": 2, "column": "stop", "text": "two", "anonymous": True, "author": None, "cluster_id": None},
            {"id": 3, "column": "continue", "text": "three", "anonymous": False, "author": "Sam", "cluster_id": None},
        ]
        sams, anns, nobodys = (True, False, True), (False, True, False), (False, False, False)
        for ws, mine in ((r.sam1, sams), (r.sam2, sams), (r.ann1, anns), (r.observer, nobodys)):
            assert ws.receive_json() == {"type": "phase", "phase": "reveal"}
            for card, m in zip(expected, mine):
                assert ws.receive_json() == {"type": "card", "card": card | {"mine": m}}
            assert_nothing_pending(ws)
        assert_nothing_pending(r.stranger)

        with connect(client, r.code, r.ann) as ws:  # from now on every snapshot holds every card, by id
            snap = ws.receive_json()
        assert snap["cards"] == [c | {"mine": m} for c, m in zip(expected, anns)]
        assert all(set(card) == CARD_KEYS for card in snap["cards"])

        assert advance(client, r.session).json() == {"phase": "cluster"}  # only the phase event
        for ws in (r.sam1, r.sam2, r.ann1, r.observer):
            assert ws.receive_json() == {"type": "phase", "phase": "cluster"}
            assert_nothing_pending(ws)


def test_reveal_with_no_cards_sends_only_the_phase_event(client):
    session = create(client)
    with connect(client, session["code"], join(client, session["code"], "Sam")) as ws:
        ws.receive_json()
        advance(client, session)
        assert ws.receive_json() == {"type": "phase", "phase": "reveal"}
        assert_nothing_pending(ws)


def test_edit_changes_text_only_and_echoes_to_the_authors_sockets(client, db):
    with room(client) as r:
        card = create_card(r.sam1)["card"]
        r.sam2.receive_json()
        r.sam2.send_json({
            "type": "card.edit", "id": card["id"], "text": " less pairing ",
            "column": "stop", "anonymous": True,  # fixed at creation: ignored
        })
        edited = {"type": "card", "card": card | {"text": "less pairing"}}
        assert r.sam1.receive_json() == r.sam2.receive_json() == edited
        for ws in (r.ann1, r.observer, r.stranger):
            assert_nothing_pending(ws)
        assert stored(db) == [(card["id"], "start", "less pairing", 0)]


def test_delete_removes_the_row_and_echoes_card_deleted_to_the_authors_sockets(client, db):
    with room(client) as r:
        card = create_card(r.sam1)["card"]
        r.sam2.receive_json()
        r.sam1.send_json({"type": "card.delete", "id": card["id"]})
        deleted = {"type": "card.deleted", "id": card["id"]}
        assert r.sam1.receive_json() == r.sam2.receive_json() == deleted
        for ws in (r.ann1, r.observer, r.stranger):
            assert_nothing_pending(ws)
        assert stored(db) == []


def test_only_the_author_edits_or_deletes_a_card(client, db):
    with room(client) as r:
        card = create_card(r.sam1)["card"]
        r.sam2.receive_json()
        before = stored(db)
        for op in (
            {"type": "card.edit", "id": card["id"], "text": "hijacked"},
            {"type": "card.delete", "id": card["id"]},
        ):
            r.ann1.send_json(op)
            assert r.ann1.receive_json()["type"] == "error", op
        assert stored(db) == before
        for ws in (r.sam1, r.sam2, r.observer, r.stranger):
            assert_nothing_pending(ws)


def test_edit_and_delete_reject_ids_that_are_not_integers_or_not_in_this_session(client, db):
    with room(client) as r:
        mine = create_card(r.sam1)["card"]
        r.sam2.receive_json()
        other = create(client)
        with connect(client, other["code"], join(client, other["code"], "Zed")) as zed:
            zed.receive_json()
            theirs = create_card(zed)["card"]
        before = stored(db)
        assert mine["id"] == 1  # so `true`, which SQLite would bind as 1, must be refused as an id
        for bad in ("1", 1.0, True, None, 999, theirs["id"]):
            for op in ({"type": "card.edit", "id": bad, "text": "x"}, {"type": "card.delete", "id": bad}):
                r.sam1.send_json(op)
                assert r.sam1.receive_json()["type"] == "error", op
        assert stored(db) == before
        assert_nothing_pending(r.sam2)


def test_column_must_be_exactly_start_stop_or_continue(client, db):
    session = create(client)
    with connect(client, session["code"], join(client, session["code"], "Sam")) as ws:
        ws.receive_json()
        for column in ("Start", "begin", "", None, 1, ["start"]):
            assert create_card(ws, column=column)["type"] == "error", column
        ws.send_json({"type": "card.create", "text": "x", "anonymous": False})  # missing
        assert ws.receive_json()["type"] == "error"
        assert stored(db) == []
        for column in ("start", "stop", "continue"):
            assert create_card(ws, column=column)["card"]["column"] == column


def test_text_is_stripped_and_limited_to_500_characters_on_create_and_edit(client, db):
    session = create(client)
    with connect(client, session["code"], join(client, session["code"], "Sam")) as ws:
        ws.receive_json()
        card = create_card(ws, text="  " + "x" * 500 + "\n")["card"]
        assert card["text"] == "x" * 500
        for text in ("x" * 501, " " + "x" * 501 + " ", "", "   ", None, 7, ["x"]):
            assert create_card(ws, text=text)["type"] == "error", text
            ws.send_json({"type": "card.edit", "id": card["id"], "text": text})
            assert ws.receive_json()["type"] == "error", text
        ws.send_json({"type": "card.create", "column": "start", "anonymous": False})  # missing
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "card.edit", "id": card["id"]})  # missing
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "card.edit", "id": card["id"], "text": " " + "y" * 500})
        assert ws.receive_json()["card"]["text"] == "y" * 500
    assert stored(db) == [(card["id"], "start", "y" * 500, 0)]


def test_anonymous_must_be_a_json_boolean(client, db):
    session = create(client)
    with connect(client, session["code"], join(client, session["code"], "Sam")) as ws:
        ws.receive_json()
        for anonymous in ("true", 1, None, 0, "false"):
            assert create_card(ws, anonymous=anonymous)["type"] == "error", anonymous
        ws.send_json({"type": "card.create", "column": "start", "text": "x"})  # missing
        assert ws.receive_json()["type"] == "error"
        assert stored(db) == []
        assert create_card(ws, anonymous=True)["card"]["anonymous"] is True
        assert create_card(ws, anonymous=False)["card"]["anonymous"] is False
    assert [row[3] for row in stored(db)] == [1, 0]


def test_card_operations_are_accepted_in_write_only(client, db):
    with room(client) as r:
        card = create_card(r.sam1)["card"]
        r.sam2.receive_json()
        before = stored(db)
        ops = (
            {"type": "card.create", "column": "start", "text": "late", "anonymous": False},
            {"type": "card.edit", "id": card["id"], "text": "late"},
            {"type": "card.delete", "id": card["id"]},
        )
        for phase in PHASES[1:]:
            advance(client, r.session)
            for ws in (r.sam1, r.sam2, r.ann1, r.observer):
                assert ws.receive_json() == {"type": "phase", "phase": phase}
                if phase == "reveal":
                    ws.receive_json()  # the one card, revealed
            for op in ops:
                r.sam1.send_json(op)
                assert r.sam1.receive_json()["type"] == "error", (phase, op)
            for ws in (r.sam2, r.ann1, r.observer, r.stranger):
                assert_nothing_pending(ws)
        assert stored(db) == before


def test_card_and_card_deleted_are_server_only_types(client, db):
    session = create(client)
    with connect(client, session["code"], join(client, session["code"], "Sam")) as ws:
        ws.receive_json()
        for message in (
            {"type": "card", "card": {"column": "start", "text": "x", "anonymous": False}},
            {"type": "card.deleted", "id": 1},
        ):
            ws.send_json(message)
            assert ws.receive_json()["detail"] == f"unknown type: {message['type']}"
        assert stored(db) == []
